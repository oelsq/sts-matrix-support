"""Matrix support bot with legal, financial and general response routes.

The specialists are separate prompt/source packs using a shared LLM gateway.
Native E2EE is opt-in; encrypted rooms require verified client devices.
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
from free_reply import FreeReplyMode
from knowledge_coverage import coverage_answer
from offline_knowledge import KnowledgeStore, validate_llm_endpoint, validate_local_endpoint
from urgent_help import urgent_finance_answer, legal_urgent_notice
from human_handoff import HumanHandoff, HandoffStore, handoff_command, choose_department, department_roster, handoff_selection, HandoffChoiceError

from matrix_inbox import InboxStore
from matrix_delivery import RequestDispatcher, sync_batch

log = logging.getLogger("support_bot")


async def main():
    os.umask(0o077)
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
    from state_cipher import StateCipher,private_directory
    from matrix_crypto import client_options,MatrixCrypto
    state_path=Path(os.getenv('HANDOFF_DB') or os.getenv('MATRIX_ROOM_ROUTES_FILE','/app/handoff_state/room_routes.json')).parent
    private_directory(state_path)
    cipher=StateCipher.from_env(os.environ)
    client = AsyncClient(os.environ["MATRIX_HOMESERVER"], os.environ["MATRIX_USER"],**client_options(os.environ,cipher,state_path))
    crypto=MatrixCrypto(client,state_path/'crypto') if os.getenv('MATRIX_E2EE_ENABLED')=='1' else None
    departments = department_roster(os.environ)
    department_users = {user for roster in departments.values() for user in roster}
    identity=hashlib.sha256((os.environ['MATRIX_HOMESERVER'].rstrip('/')+'\0'+os.environ['MATRIX_USER']).encode()).hexdigest()
    inbox=InboxStore(os.getenv('MATRIX_INBOX_DB',str(state_path/'matrix_inbox.sqlite3')),identity,cipher=cipher)
    conversations = ConversationMemory(persistent_store=inbox)
    handoff = None
    if departments:
        handoff = HumanHandoff(
            client,
            HandoffStore(os.getenv("HANDOFF_DB", "/app/handoff_state/handoff.sqlite3"),cipher=cipher,retention_days=int(os.getenv("HANDOFF_CONTEXT_RETENTION_DAYS","30"))),
            departments,
            os.environ["ELEMENT_PUBLIC_URL"],secure=bool(crypto),
        )
    latest_context = {}
    previous_routes = {}
    free_mode = FreeReplyMode.from_env(os.environ, fixed_routes)
    log.info("Free model rooms configured: %d", len(free_mode.routes) if free_mode else 0)
    private_rooms=None
    if os.getenv('MATRIX_PRIVATE_AI_MODE') in {'1','optional'}:
        if crypto is None:raise RuntimeError('Private AI mode requires native E2EE')
        from private_rooms import PrivateRooms
        private_rooms=PrivateRooms(inbox,client,fixed_routes,os.environ['ELEMENT_PUBLIC_URL'],cipher,free_mode=free_mode)
    verification=None
    if crypto:
        from matrix_verification import DeviceVerification
        verification=DeviceVerification(client,state_path/'crypto',lambda user:user in department_users or bool(private_rooms and private_rooms.known_peer(user)) or any(user in room.users for ident,room in client.rooms.items() if ident in fixed_routes))
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
            if free_mode and free_mode.selected(room_id, domain):
                return await free_mode.reply(http, domain, question, knowledge,
                    previous_question=previous_question, search_question=search_question,
                    endpoint=os.environ["LLM_ENDPOINT"], model=model_for_domain(domain), api_key=api_key)
            retrieval_started = time.monotonic()
            prepared = await asyncio.to_thread(prepare_local_reply,domain, question, knowledge,
                                           search_question=search_question, previous_question=previous_question,
                                           allow_model_without_sources=False)
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
            async def complete(request, stage="draft"):
                async with http.post(
                    os.environ["LLM_ENDPOINT"].rstrip("/") + "/chat/completions",
                    json=request,
                    headers={"Authorization": "Bearer " + api_key},
                ) as response:
                    response.raise_for_status()
                    return await response.json()

            for attempt in range(2):
                data = await complete(payload)
                answer = data["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Empty LLM response")
                if local_evidence:
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
                if citation_failure and attempt:
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

        from matrix_transport import TypingIndicators,MatrixSender
        typing=TypingIndicators(client)

        async def process_event(room, event):
            if (
                event.sender == client.user
                or event.sender in department_users
                or (handoff and handoff.store.contains_room(room.room_id))
            ):
                return
            if private_rooms and not private_rooms.permitted(room.room_id,event.sender):
                log.warning('Private AI room ownership or membership check rejected request')
                return
            key = (room.room_id, event.sender)
            question = event.body[:12000]
            from answer_feedback import feedback_reply
            feedback = feedback_reply(inbox, room.room_id, event.sender, question)
            if feedback is not None:
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message", content=matrix_content(feedback))
                return
            from bot_help import help_reply
            help_domain = fixed_routes.get(room.room_id)
            if help_domain is None and len(previous_routes.get(key, ())) == 1:
                help_domain = previous_routes[key][0]
            # Explicit commands stay deterministic. Natural-language requests
            # in free rooms are answered by the model, including capabilities.
            natural_free = bool(free_mode and free_mode.selected(room.room_id, help_domain)
                                and not question.lstrip().startswith("!"))
            help_answer = None if natural_free else help_reply(question, help_domain)
            if help_answer is not None:
                await dispatcher.send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(help_answer))
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
            if private_rooms and (question.strip().casefold()=='!лично' or (os.getenv('MATRIX_PRIVATE_AI_MODE')=='1' and room.room_id not in private_rooms.records)):
                domain=None if question.strip().casefold()=='!лично' else fixed_routes.get(room.room_id)
                link=await private_rooms.invitation(event.sender,domain)
                await dispatcher.send(room_id=room.room_id,message_type='m.room.message',content=matrix_content(link))
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
            typing.start(room.room_id)
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
                typing.finish(room.room_id)
            answer = "\n\n".join(replies)
            latest_context[key] = (question, answer)
            if len(latest_context) > 1000:
                latest_context.pop(next(iter(latest_context)))
            if handoff and any(domain in departments for domain in domains):
                answer += department_note(question, answer)
            inbox.reply_domain(event.event_id, domains)
            result = await dispatcher.send(
                room_id=room.room_id,
                message_type="m.room.message",
                content=matrix_content(answer),
            )
            log.info("Send result: %s", type(result).__name__)

        durable_sync=hasattr(client,'add_response_callback')
        sender=MatrixSender(http,client,os.environ['MATRIX_HOMESERVER'],secure_room=private_rooms.required_encryption if private_rooms else None) if durable_sync and hasattr(client,'access_token') else None
        dispatcher=RequestDispatcher(inbox,client,process_event,allow_unconfirmed=not durable_sync,sender=sender)
        ready=asyncio.Event()
        worker=None
        knowledge_monitor=None
        security_worker=None

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
            await sync_batch(client,response,inbox,eligible_room,eligible_event,crypto=crypto)
            if crypto:await crypto.resume(inbox,eligible_room,eligible_event)
            ready.set();dispatcher.wake.set()

        async def security_maintenance():
            last_cleanup=0.
            last_handoff=0.
            while True:
                try:
                    if verification:await verification.step()
                    if private_rooms:await private_rooms.operator_step(state_path/'crypto')
                    if crypto and await crypto.resume(inbox,eligible_room,eligible_event):dispatcher.wake.set()
                    if handoff and handoff.secure and time.monotonic()-last_handoff>10:
                        await handoff.flush_secure();last_handoff=time.monotonic()
                    if handoff and time.monotonic()-last_cleanup>300:
                        handoff.store.expire_context();last_cleanup=time.monotonic()
                except asyncio.CancelledError:raise
                except Exception as exc:log.warning('Security maintenance deferred (%s)',type(exc).__name__)
                await asyncio.sleep(1)

        try:
            if knowledge_dir:
                from knowledge_monitor import monitor
                knowledge_monitor=asyncio.create_task(monitor(knowledge_dir,str(state_path/'knowledge-freshness.json')))
            session=inbox.meta('matrix_session')
            restored=False
            if crypto:crypto.guard_existing_store(json.loads(session) if session else None)
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
            if crypto:crypto.remember_identity()
            log.info("Matrix session ready; durable cursor=%s pending=%d",bool(inbox.cursor),inbox.pending())
            if verification:
                from nio import ToDeviceEvent
                client.add_to_device_callback(verification.callback,ToDeviceEvent)
                security_worker=asyncio.create_task(security_maintenance())
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
            if security_worker:
                security_worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):await security_worker
            if knowledge_monitor:
                knowledge_monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):await knowledge_monitor
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):await worker
            if sender:log.info('Matrix delivery summary: %s',json.dumps(sender.metrics))
            await typing.close()
            await client.close()
            inbox.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    asyncio.run(main())
