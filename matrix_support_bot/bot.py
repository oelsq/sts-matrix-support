"""Matrix support bot with legal, financial and general response routes.

The specialists are separate prompt/source packs using a shared LLM gateway.
They work only in unencrypted demonstration rooms.
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import aiohttp
from nio import AsyncClient, LoginResponse, InviteMemberEvent, RoomMessageText, RoomMemberEvent

from llm_settings import api_key as read_llm_key, model_for_domain
from message_format import matrix_content
from evidence_scope import scope_evidence
from response_guidance import practical_fallback, request_clarification
from conversation import ConversationMemory
from agents import messages, render, route_for_room, review_messages
from checked_answers import checked_legal_answer, checked_work_payment_answer, checked_practical_guidance
from knowledge_coverage import coverage_answer
from offline_knowledge import KnowledgeStore, needs_bank, validate_llm_endpoint, validate_local_endpoint
from urgent_help import urgent_finance_answer, legal_urgent_notice
from human_handoff import HumanHandoff, HandoffStore, handoff_command, choose_department, department_roster, handoff_selection, HandoffChoiceError

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
    conversations = ConversationMemory()
    started_ms = int(time.time() * 1000)
    seen = set()
    previous_routes = {}
    from flowise_client import FlowiseClient
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
            search_question, previous_question = conversations.resolve(room_id, sender, domain, question)
            clarification = request_clarification(domain, search_question, previous_question)
            if clarification:
                log.info("Clarification before retrieval; domain=%s", domain)
                return clarification
            if knowledge is not None and domain != "general":
                retrieval_started = time.monotonic()
                local_evidence = scope_evidence(domain, search_question, knowledge.search(domain, search_question, limit=8))
                log.info("Evidence retrieval: domain=%s passages=%d elapsed_ms=%d continuation=%s",
                         domain, len(local_evidence), int((time.monotonic()-retrieval_started)*1000), bool(previous_question))
                if not local_evidence:
                    log.info("Agent fallback: domain=%s reason=no_evidence", domain)
                    return practical_fallback(domain, search_question)
                checked = checked_practical_guidance(domain, search_question, local_evidence)
                if checked:
                    log.info("Source-bound practical guidance; domain=%s", domain)
                    return checked
                checked = checked_work_payment_answer(domain, search_question, local_evidence)
                if checked:
                    log.info("Source-bound work payment answer; domain=%s", domain)
                    return checked
                checked = checked_legal_answer(search_question, local_evidence, domain=domain)
                if checked:
                    return checked
            if domain == "legal" and local_evidence is not None:
                from agents import legal_aid_eligibility_answer
                bounded = legal_aid_eligibility_answer(question, local_evidence)
                if bounded:
                    return bounded
            payload = {
                "model": model_for_domain(domain),
                "messages": messages(domain, question, local_evidence,
                                     previous_question if search_question != question else ""),
                "max_tokens": 2300,
                "temperature": 0.1,
            }
            workflow = bool(flowise and getattr(flowise,'is_workflow',lambda *_:False)(room_id,domain))
            async def complete(request, stage="draft"):
                if workflow:
                    from agentflow_graphs import MARKER
                    result = await flowise.run_workflow(http,domain,room_id,sender,event_id,request['messages'],
                                review_messages(domain,search_question,MARKER,local_evidence))
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

            for attempt in range(2):
                data = await complete(payload)
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
                rendered = render(domain, answer.strip(), question, local_evidence)
                citation_failure = "Не удалось подготовить ответ с проверяемой" in rendered
                truncated = data["choices"][0].get("finish_reason") == "length"
                citation_failure = citation_failure or truncated
                if citation_failure and attempt:
                    log.info("Agent fallback: domain=%s reason=answer_validation", domain)
                    return practical_fallback(domain, search_question, validation_failed=True)
                if local_evidence is None or not citation_failure or attempt:
                    return rendered
                log.info("Retrying agent %s after invalid citation format", domain)
                payload["messages"] += [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content":
                     "Перепиши ответ короче (до 180 слов) по переданным фрагментам. Убери неподтверждённые числа, сроки и условия. "
                     "Разрешены ТОЛЬКО метки " + ", ".join("[S" + str(i) + "]" for i in range(1, len(local_evidence) + 1)) +
                     ". Никаких слов, номеров пунктов, тире или диапазонов внутри скобок. "
                     "Если утверждение не подтверждается фрагментом, убери его; если данных недостаточно, прямо сообщи это."},
                ]

        async def message(room, event):
            if (
                event.sender == client.user
                or event.sender in department_users
                or (handoff and handoff.store.contains_room(room.room_id))
                or event.server_timestamp < started_ms
                or event.event_id in seen
            ):
                return
            seen.add(event.event_id)
            if len(seen) > 10000:
                seen.clear()
                seen.add(event.event_id)

            key = (room.room_id, event.sender)
            question = event.body[:12000]
            if question.strip().casefold() == "!помощь":
                await client.room_send(room_id=room.room_id, message_type="m.room.message", content=matrix_content(
                    "**Как пользоваться**\n\nЗадайте вопрос обычными словами. Для продолжения напишите уточнение в этом же чате.\n\n"
                    "- `!человек` — обратиться к сотруднику выбранного департамента.\n"
                    "- `!сотрудники` — список сотрудников и команды выбора.\n"
                    "- `!юрдеп2` или `!финдеп3` — выбрать конкретного сотрудника.\n"
                    "- `!другой` — сменить сотрудника; используйте команду здесь, в чате ИИ.\n"
                    "- `!новый` — начать новую тему.\n"
                    "- `!база` — посмотреть доступные темы знаний."))
                return
            if question.strip().casefold() == "!новый":
                conversations.clear(room.room_id, event.sender)
                latest_context.pop(key, None)
                previous_routes.pop(key, None)
                await client.room_send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content("Начинаем новую тему. Контекст ваших предыдущих вопросов сброшен; история комнаты сохранена."))
                return
            if question.strip().casefold() in {"!темы", "!база"}:
                await client.room_send(room_id=room.room_id, message_type="m.room.message",
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
                await client.room_send(room_id=room.room_id, message_type="m.room.message", content=content)
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
                await client.room_send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(answer))
                return
            notice = legal_urgent_notice(question) if "legal" in domains else None
            if notice:
                await client.room_send(room_id=room.room_id, message_type="m.room.message",
                                       content=matrix_content(notice))
            if flowise and room.room_id in flowise.routes:
                await client.room_send(room_id=room.room_id, message_type="m.room.message",
                                       content={"msgtype":"m.text", "body":"Готовлю ответ по проверенным документам…"})
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
                    replies.append(f"{name} временно недоступен. Попробуйте позже.")
            answer = "\n\n".join(replies)
            latest_context[key] = (question, answer)
            if len(latest_context) > 1000:
                latest_context.pop(next(iter(latest_context)))
            if handoff and any(domain in departments for domain in domains):
                answer += "\n\nНужна проверка сотрудника? Напишите !человек."
            result = await client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content=matrix_content(answer),
            )
            log.info("Send result: %s", type(result).__name__)

        try:
            response = await client.login(
                os.environ["MATRIX_PASSWORD"], device_name="STS Support Bot"
            )
            if not isinstance(response, LoginResponse):
                raise RuntimeError("Matrix login failed: " + type(response).__name__)
            log.info("Matrix login successful")
            client.add_event_callback(invite, InviteMemberEvent)
            client.add_event_callback(human_member, RoomMemberEvent)
            client.add_event_callback(message, RoomMessageText)
            await client.sync_forever(timeout=30000)
        finally:
            await client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("nio").setLevel(logging.WARNING)
    asyncio.run(main())
