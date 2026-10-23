"""Matrix support bot with legal, financial and general response routes.

The specialists are separate prompt/source packs using a shared LLM gateway.
They work only in unencrypted demonstration rooms.
"""
import asyncio
import json
import logging
import contextlib
import hashlib
import os
import time
from pathlib import Path

import aiohttp
from nio import AsyncClient, LoginResponse, InviteMemberEvent, RoomMessageText, RoomMemberEvent, SyncResponse

from llm_settings import api_key as read_llm_key, model_for_domain
from message_format import matrix_content
from response_guidance import practical_fallback, is_document_request
from answer_usefulness import usefulness_issues, department_note
from conversation import ConversationMemory
from agents import messages, render, route_for_room, review_messages
from local_reply import prepare_local_reply
from knowledge_coverage import coverage_answer
from offline_knowledge import KnowledgeStore, validate_llm_endpoint, validate_local_endpoint
from urgent_help import urgent_finance_answer, legal_urgent_notice
from human_handoff import HumanHandoff, HandoffStore, handoff_command, choose_department, department_roster, handoff_selection, HandoffChoiceError

from matrix_inbox import InboxStore
from matrix_delivery import RequestDispatcher, sync_batch

log = logging.getLogger("support_bot")


async def main():
    required = (
        "MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD",
        "LLM_ENDPOINT", "LLM_MODEL",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing settings: " + ", ".join(missing))
    offline = os.getenv("OFFLINE_MODE") == "1"
    api_key = read_llm_key()
    knowledge = None
    knowledge_dir = os.getenv("KNOWLEDGE_DIR")
    if offline:
        host = validate_llm_endpoint(
            os.environ["LLM_ENDPOINT"], os.getenv("LLM_ALLOWED_HOSTS", "")
        )
        validate_local_endpoint(
            os.environ["MATRIX_HOMESERVER"],
            os.getenv("MATRIX_ALLOWED_HOSTS", ""),
            "Matrix",
        )
        if not knowledge_dir:
            raise RuntimeError("Missing KNOWLEDGE_DIR in offline mode")
        log.info("Offline mode enabled; LLM host %s", host)
    if knowledge_dir:
        knowledge = KnowledgeStore(knowledge_dir)
        log.info("Local document knowledge loaded; passages %d", len(knowledge.passages))
    routes_file = Path(os.getenv("MATRIX_ROOM_ROUTES_FILE", "/app/room_routes.json"))
    fixed_routes = {}
    if routes_file.exists():
        fixed_routes = json.loads(routes_file.read_text(encoding="utf-8"))
        if (
            not isinstance(fixed_routes, dict)
            or any(
                not isinstance(room_id, str) or domain not in {"legal", "finance"}
                for room_id, domain in fixed_routes.items()
            )
        ):
            raise RuntimeError("Invalid Matrix room routes file")
    log.info("Dedicated specialist rooms configured: %d", len(fixed_routes))
    client = AsyncClient(os.environ["MATRIX_HOMESERVER"], os.environ["MATRIX_USER"])
    departments = department_roster(os.environ)
    department_users = {user for roster in departments.values() for user in roster}
    handoff = None
    if departments:
        handoff = HumanHandoff(
            client,
            HandoffStore(os.getenv("HANDOFF_DB", "/app/handoff_state/handoff.sqlite3")),
            departments,
            os.environ["ELEMENT_PUBLIC_URL"],
        )
    latest_context = {}
    state_path=Path(os.getenv('HANDOFF_DB') or os.getenv('MATRIX_ROOM_ROUTES_FILE','/app/handoff_state/room_routes.json')).parent
    identity=hashlib.sha256((os.environ['MATRIX_HOMESERVER'].rstrip('/')+'\0'+os.environ['MATRIX_USER']).encode()).hexdigest()
    inbox=InboxStore(os.getenv('MATRIX_INBOX_DB',str(state_path/'matrix_inbox.sqlite3')),identity)
    conversations = ConversationMemory(persistent_store=inbox)
    previous_routes = {}
    from flowise_client import FlowiseClient, FlowiseReviewRejected
    flowise = FlowiseClient.from_env(os.environ)
    if flowise:
        for room_id, entry in flowise.routes.items():
            if room_id in fixed_routes and fixed_routes[room_id] != entry["domain"]:
                raise RuntimeError("Flowise domain conflicts with room route")
            fixed_routes[room_id] = entry["domain"]
        log.info("Flowise pilot rooms configured: %d", len(flowise.routes))
    timeout = aiohttp.ClientTimeout(total=90)

    async with aiohttp.ClientSession(timeout=timeout) as http:
        async def human_member(room, event):
            if handoff and event.membership == "join":
                try:
                    await handoff.member_joined(room.room_id, event.state_key)
                except Exception as exc:
                    log.error("Departure from human room failed (%s)", type(exc).__name__)

        async def invite(room, event):
            if event.state_key == client.user:
                if handoff and handoff.store.contains_room(room.room_id):
                    return
                result = await client.join(room.room_id)
                log.info("Join result: %s", type(result).__name__)

        async def ask_agent(domain, question, room_id, sender, event_id):
            local_evidence = None
            search_question, previous_question = conversations.resolve(room_id, sender, domain, question, request_id=event_id)
            retrieval_started = time.monotonic()
            prepared = prepare_local_reply(domain, question, knowledge,
                                           search_question=search_question, previous_question=previous_question)
            local_evidence = prepared.evidence
            log.info("Reply preparation: domain=%s mechanism=%s passages=%d elapsed_ms=%d continuation=%s",
                     domain, prepared.mechanism, len(local_evidence or []),
                     int((time.monotonic()-retrieval_started)*1000), bool(previous_question))
            if prepared.answer is not None:
                return prepared.answer
            payload = {
                "model": model_for_domain(domain),
                "messages": messages(domain, question, local_evidence,
                                     previous_question if search_question != question else ""),
                "max_tokens": 2300,
                "temperature": 0.1,
            }
            workflow = bool(flowise and getattr(flowise,'is_workflow',lambda *_:False)(room_id,domain))
            quality = workflow and flowise.routes[room_id].get('quality_contract')=='sts.claims.v1'
            async def complete(request, stage="draft"):
                if workflow:
                    from agentflow_graphs import MARKER
                    context = {'question':question,'previous_question':previous_question or '', 'evidence':local_evidence}
                    kwargs = {'context':context} if quality else {}
                    result = await flowise.run_workflow(http,domain,room_id,sender,event_id,request['messages'],
                                review_messages(domain,search_question,MARKER,local_evidence),**kwargs)
                    log.info('Agentflows V2 workflow completed; domain=%s',domain)
                    return {'choices':[{'message':{'content':result},'finish_reason':None}]}
                if flowise and flowise.selected(room_id, domain):
                    result = await flowise.predict(http, domain, room_id, sender, event_id, request["messages"], stage=stage)
                    log.info("Flowise response received; domain=%s stage=%s", domain, stage)
                    return {"choices": [{"message": {"content": result}, "finish_reason": None}]}
                async with http.post(
                    os.environ["LLM_ENDPOINT"].rstrip("/") + "/chat/completions",
                    json=request,
                    headers={"Authorization": "Bearer " + api_key},
                ) as response:
                    response.raise_for_status()
                    return await response.json()

            if quality:
                from quality_reply import quality_reply
                try:
                    result=await quality_reply(flowise,http,domain,room_id,sender,event_id,question,previous_question,
                                               local_evidence,knowledge,search_question=search_question)
                    log.info('Agentflows V2 workflow completed; domain=%s research=%d',domain,result.research_attempts)
                    return result.answer
                except FlowiseReviewRejected:
                    return practical_fallback(domain,search_question,validation_failed=True)
            for attempt in range(2):
                try:
                    data = await complete(payload)
                except FlowiseReviewRejected as exc:
                    log.info("Agent claim review rejected; domain=%s", domain)
                    return practical_fallback(domain, search_question, validation_failed=True)
                answer = data["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Empty LLM response")
                if local_evidence and not workflow:
                    review = {"model": model_for_domain(domain),
                              "messages": review_messages(domain, search_question, answer, local_evidence),
                              "max_tokens": 2300, "temperature": 0.1}
                    data = await complete(review, stage="review")
                    answer = data["choices"][0]["message"]["content"]
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("Empty LLM review")
                rendered = render(domain, answer.strip(), question, local_evidence, review=getattr(answer,"review",None))
                citation_failure = "Не удалось подготовить ответ с проверяемой" in rendered
                truncated = data["choices"][0].get("finish_reason") == "length"
                quality_issues = [] if citation_failure or domain == "general" else usefulness_issues(question, rendered)
                citation_failure = citation_failure or truncated or bool(quality_issues)
                if citation_failure and (attempt or quality):
                    log.info("Agent fallback: domain=%s reason=answer_validation", domain)
                    return practical_fallback(domain, search_question, validation_failed=True)
                if not citation_failure:
                    return rendered
                if local_evidence is None:
                    return practical_fallback(domain, search_question, validation_failed=True)
                log.info("Retrying agent %s after response checks: %s", domain, quality_issues or ["citation_or_length"])
                payload["messages"] += [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content":
                     f"Перепиши ответ короче (до {320 if is_document_request(question) else 180} слов) по переданным фрагментам. Убери неподтверждённые числа, сроки и условия. "
                     "Разрешены ТОЛЬКО метки " + ", ".join("[S" + str(i) + "]" for i in range(1, len(local_evidence) + 1)) +
                     ". Никаких слов, номеров пунктов, тире или диапазонов внутри скобок. "
                     "Если утверждение не подтверждается фрагментом, убери его; если данных недостаточно, прямо сообщи это. "
                     "Сохрани полезный ответ по существу и конкретное действие. Не перенаправляй к сотруднику вместо ответа. "
                     "Если просили письмо, напиши сам черновик с заполнителями. Ошибки предыдущей попытки: " + ", ".join(quality_issues)},
                ]

        typing_counts = {}
        async def indicate_typing(room_id, delta):
            # Ephemeral presence avoids spending the message quota on progress text.
            if not hasattr(client,'room_typing'):return
            count=max(0,typing_counts.get(room_id,0)+delta)
            if count:typing_counts[room_id]=count
            else:typing_counts.pop(room_id,None)
            if delta>0 and count>1 or delta<0 and count>0:return
            try:await client.room_typing(room_id,typing_state=bool(count),timeout=30000)
            except Exception as exc:log.debug('Typing indicator unavailable (%s)',type(exc).__name__)

        async def process_event(room, event):
            if (
                event.sender == client.user
                or event.sender in department_users
                or (handoff and handoff.store.contains_room(room.room_id))
            ):
                return
            key = (room.room_id, event.sender)
            question = event.body[:12000]
            if question.strip().casefold() == "!помощь":
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message", content=matrix_content(
                    "**Как пользоваться**\n\nЗадайте вопрос обычными словами. Для продолжения напишите уточнение в этом же чате.\n\n"
                    "- `!человек` — обратиться к сотруднику выбранного департамента.\n"
                    "- `!сотрудники` — список сотрудников и команды выбора.\n"
                    "- `!юрдеп2` или `!финдеп3` — выбрать конкретного сотрудника.\n"
                    "- `!другой` — сменить сотрудника; используйте команду здесь, в чате ИИ.\n"
                    "- `!новый` — начать новую тему.\n"
                    "- `!база` — посмотреть доступные темы знаний.\n"
                    "- `!расчёт` — шаблон точного расчёта переплаты по полному графику платежей."))
                return
            if question.strip().casefold() == "!новый":
                conversations.clear(room.room_id, event.sender)
                latest_context.pop(key, None)
                previous_routes.pop(key, None)
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content("Начинаем новую тему. Контекст ваших предыдущих вопросов сброшен; история комнаты сохранена."))
                return
            if question.strip().casefold() in {"!темы", "!база"}:
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(coverage_answer(knowledge)))
                return
            command = handoff_command(question)
            if command:
                requested, note = command
                domain = choose_department(
                    requested, fixed_routes.get(room.room_id), previous_routes.get(key, ())
                )
                if not domain:
                    content = {"msgtype": "m.text", "body": "Выберите департамент: !юрдеп или !финдеп. Можно добавить описание вопроса после команды."}
                elif not handoff or domain not in departments:
                    content = {"msgtype": "m.text", "body": "Передача в этот департамент пока не настроена. Сообщение сотруднику не отправлено."}
                else:
                    try:
                        if question.strip().split(maxsplit=1)[0].casefold() == '!сотрудники':
                            content = matrix_content(handoff.directory(event.sender, domain))
                        else:
                            employee_number, change = handoff_selection(question)
                            content = await handoff.open(event.sender, domain, latest_context.get(key), note,
                                                         employee_number=employee_number, change=change,
                                                         request_id=event.event_id)
                            log.info("Human handoff completed; domain %s", domain)
                    except HandoffChoiceError as exc:
                        content = matrix_content(str(exc))
                    except Exception as exc:
                        log.error("Human handoff failed (%s)", type(exc).__name__)
                        content = {"msgtype": "m.text", "body": "Не удалось завершить передачу сотруднику. Попробуйте команду ещё раз. Приглашение в комнату могло уже появиться."}
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message", content=content)
                return
            domains = route_for_room(
                room.room_id, question, previous_routes.get(key, ()), fixed_routes
            )
            previous_routes[key] = domains
            log.info("Routing request to: %s", ",".join(domains))

            urgent = urgent_finance_answer(question, knowledge) if set(domains) & {"legal", "finance"} else None
            if urgent:
                answer = urgent
                latest_context[key] = (question, answer)
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(answer))
                return
            notice = legal_urgent_notice(question) if "legal" in domains else None
            if notice:
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(notice),kind='urgent',final=False)
            await indicate_typing(room.room_id,1)
            try:
                replies = []
                for domain in domains:
                    try:
                        replies.append(await ask_agent(domain, question, room.room_id, event.sender, event.event_id))
                        log.info("Agent %s answered", domain)
                    except Exception as exc:
                        log.error("Agent %s failed (%s)", domain, type(exc).__name__)
                        name = {
                            "legal": "Юридический агент",
                            "finance": "Финансовый агент",
                            "general": "Помощник",
                        }[domain]
                        replies.append(practical_fallback(domain, question) +
                                       "\n\nСервис генерации временно недоступен; выше — ограниченная помощь без нового ответа модели.")
            finally:
                await indicate_typing(room.room_id,-1)
            answer = "\n\n".join(replies)
            latest_context[key] = (question, answer)
            if len(latest_context) > 1000:
                latest_context.pop(next(iter(latest_context)))
            if handoff and any(domain in departments for domain in domains):
                answer += department_note(question, answer)
            result = await dispatcher.send(
                room_id=room.room_id,
                message_type="m.room.message",
                content=matrix_content(answer),
            )
            log.info("Send result: %s", type(result).__name__)

        durable_sync=hasattr(client,'add_response_callback')
        dispatcher=RequestDispatcher(inbox,client,process_event,allow_unconfirmed=not durable_sync)
        ready=asyncio.Event()
        worker=None
        knowledge_monitor=None

        def eligible_room(room_id):
            return not (handoff and handoff.store.contains_room(room_id))

        def eligible_event(room_id,event):
            return event.sender!=client.user and event.sender not in department_users and eligible_room(room_id)

        async def message(room,event):
            # A real SyncResponse provides the entire atomic batch, including gap metadata.
            if durable_sync:return
            if not eligible_event(room.room_id,event):return
            with inbox.db:inbox.enqueue(room.room_id,event)
            while await dispatcher.process_next():pass

        async def checkpoint(response):
            await sync_batch(client,response,inbox,eligible_room,eligible_event)
            ready.set();dispatcher.wake.set()

        try:
            if knowledge_dir:
                from knowledge_monitor import monitor
                knowledge_monitor=asyncio.create_task(monitor(knowledge_dir,str(state_path/'knowledge-freshness.json')))
            session=inbox.meta('matrix_session')
            restored=False
            if session and hasattr(client,'restore_login'):
                saved=json.loads(session)
                client.restore_login(saved['user_id'],saved['device_id'],saved['access_token'])
                who=await client.whoami()
                if getattr(who,'user_id',None)==os.environ['MATRIX_USER']:
                    restored=True
                elif getattr(who,'status_code',None) not in {'M_UNKNOWN_TOKEN','M_MISSING_TOKEN'}:
                    raise RuntimeError('Could not validate stored Matrix session')
            if not restored:
                response = await client.login(os.environ["MATRIX_PASSWORD"],device_name="STS Support Bot")
                if not isinstance(response, LoginResponse):
                    raise RuntimeError("Matrix login failed: " + type(response).__name__)
                if all(getattr(response,k,None) for k in ['user_id','device_id','access_token']):
                    with inbox.db:inbox.set_meta('matrix_session',json.dumps({k:getattr(response,k) for k in ['user_id','device_id','access_token']}))
            log.info("Matrix session ready; durable cursor=%s pending=%d",bool(inbox.cursor),inbox.pending())
            client.add_event_callback(invite, InviteMemberEvent)
            client.add_event_callback(human_member, RoomMemberEvent)
            client.add_event_callback(message, RoomMessageText)
            if durable_sync:
                client.add_response_callback(checkpoint,SyncResponse)
                worker=asyncio.create_task(dispatcher.run(ready, workers=int(os.getenv("MATRIX_REQUEST_WORKERS","1"))))
                await client.sync_forever(timeout=30000,since=inbox.cursor,full_state=True,
                                          sync_filter={'room':{'timeline':{'limit':100}}})
            else:
                while await dispatcher.process_next():pass
                await client.sync_forever(timeout=30000)
        finally:
            if knowledge_monitor:
                knowledge_monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):await knowledge_monitor
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):await worker
            await client.close()
            inbox.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    asyncio.run(main())
