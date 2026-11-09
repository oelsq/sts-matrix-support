"""User-visible help before optional escalation; no real model in these tests."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from answer_usefulness import usefulness_issues, department_note, should_offer_department
from agents import messages, review_messages
from local_reply import prepare_local_reply
from offline_knowledge import KnowledgeStore
from response_guidance import practical_fallback, retrieval_question

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope="module")
def corpus():
    return KnowledgeStore(ROOT/"offline_knowledge")

@pytest.mark.parametrize("text", [
    "Обратитесь в ЮрДеп. [S1]",
    "Финансовый агент\nНапишите !человек — сотрудник поможет. [S1]",
    "Не могу дать ответ. Обратитесь к специалисту. [S1]",
    "**Юридический агент**\n\nПроконсультируйтесь с юристом.\n\nЛокальные материалы для проверки:\n• длинное название акта не является полезным ответом",
])
def test_citations_and_long_source_cards_do_not_make_a_deflection_helpful(text):
    assert "handoff_only" in usefulness_issues("Начальник не платит зарплату",text)


def test_a_long_answer_must_not_start_by_pushing_user_to_department():
    answer="Обратитесь в юридический департамент. Сохраните трудовой договор и расчётные листки. Запишите период невыплаты и ожидаемую сумму."
    assert "premature_department_referral" in usefulness_issues("Не платят зарплату",answer)

@pytest.mark.parametrize("question,answer", [
    ("Какая комиссия?", "Комиссия составляет 0 тенге. [S1]"),
    ("Не платят зарплату", "Сохраните договор и расчётные листки. Запишите период невыплаты и попросите работодателя предоставить расчёт. [S1]"),
    ("Мошенник просит SMS-код", "Не сообщайте SMS-код. Самостоятельно свяжитесь с банком через известный официальный канал. [S1]"),
])
def test_short_facts_and_useful_actions_are_not_forced_into_a_generic_plan(question,answer):
    assert not usefulness_issues(question,answer)
    assert department_note(question,answer)==""


def test_draft_request_requires_the_actual_letter():
    question="Напиши требование работодателю о выплате зарплаты"
    assert "requested_draft_missing" in usefulness_issues(question,"1. Составьте требование. 2. Передайте работодателю. [S1]")
    assert not usefulness_issues(question,practical_fallback("legal",question+"\nПредыдущий вопрос: Начальник не платит зарплату"))

@pytest.mark.parametrize("domain",["legal","finance"])
def test_rental_followup_returns_editable_letter_in_same_production_path(corpus,domain):
    previous="Арендодатель не возвращает залог за квартиру"
    current="А напиши требование арендодателю."
    query=retrieval_question(current,previous)
    prepared=prepare_local_reply(domain,current,corpus,search_question=query,previous_question=previous)
    assert prepared.mechanism=="organizational_draft"
    assert prepared.answer and "Кому:" in prepared.answer and "Прошу" in prepared.answer
    assert "[сумма]" in prepared.answer and "основания удержания" in prepared.answer
    assert "вся сумма подлежит возврату" in prepared.answer
    assert "3 рабочих" not in prepared.answer and "10 дней" not in prepared.answer
    assert not usefulness_issues(current,prepared.answer)
    assert department_note(current,prepared.answer)==""

@pytest.mark.parametrize("domain",["legal","finance"])
def test_public_product_tariff_is_available_in_both_specialists(corpus,domain):
    question="Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?"
    prepared=prepare_local_reply(domain,question,corpus)
    assert prepared.answer and "0 тенге" in prepared.answer and "Не удалось" not in prepared.answer
    assert prepared.evidence and all(p["document_id"]=="halyk-superapp" for p in prepared.evidence)
    assert "оплата коммунальных услуг" in " ".join(prepared.evidence[0]["text"].lower().split())

@pytest.mark.parametrize("question", [
    "Что такое ГЭСВ?", "Как составить семейный бюджет?", "Срочно напиши претензию продавцу", "Сколько стоит перевод?",
])
def test_department_offer_is_not_added_to_routine_questions(question):
    assert not should_offer_department(question,"Ответ")
    assert not department_note(question,"Ответ")

@pytest.mark.parametrize("question", [
    "Ваш ответ не помог, хочу вторую оценку", "Хочу поговорить с сотрудником", "Меня задержала полиция", "Завтра суд, нужно проверить документы",
])
def test_optional_employee_help_remains_available_in_relevant_cases(question):
    assert should_offer_department(question,"Ответ по существу")
    assert "!человек" in department_note(question,"Ответ по существу")


def test_both_direct_and_flowise_prompts_require_help_before_escalation():
    for domain in ("legal","finance"):
        for result in (messages(domain,"Не платят зарплату",[]),review_messages(domain,"Напиши требование","draft",[])):
            assert "Сначала решай задачу пользователя" in result[0]["content"]
    config=json.loads((ROOT/"matrix_support_bot/agents/flowise_profiles.json").read_text())
    assert all("одной рекомендацией обратиться в департамент" in stage["instructions"] for item in config["domains"].values() for stage in item.values())

@pytest.mark.parametrize("domain",["legal","finance"])
@pytest.mark.parametrize("mode",["deflect_once","deflect_twice","draft_missing_once","transport_failure"])
def test_actual_matrix_callback_retries_deflection_and_keeps_help_without_routine_footer(monkeypatch,tmp_path,domain,mode):
    import bot
    from flowise_client import FlowiseClient
    calls=[];output=[]
    room_id="!room:demo.local"
    question=("Напиши заявление в банк об изменении условий платежа" if mode=="draft_missing_once" else "Не платят зарплату, помоги разобраться")
    good=("Кому: [организация]\nОт: [имя]\n\nПрошу проверить мои расчёты и рассмотреть изменение условий платежа по договору [номер]. "
          "По моим данным, возникли трудности из-за [причина]. Прошу письменно сообщить результат рассмотрения обращения. Дата: [дата]. [S1]"
          if mode=="draft_missing_once" else "Сохраните договор и расчётные листки. Запишите период невыплаты и ожидаемую сумму, затем попросите работодателя предоставить расчёт начислений и удержаний. [S1]")
    class Flow:
        routes={room_id:{"domain":domain,"flow_id":"synthetic","engine":"agentflow_v2"}}
        def selected(self,*args):return True
        def is_workflow(self,*args):return True
        async def run_workflow(self,http,domain,room,sender,event,draft,review):
            calls.append(draft)
            if mode=="transport_failure":raise RuntimeError("synthetic transport error")
            if mode=="deflect_twice" or len(calls)==1 and mode=="deflect_once":return "Обратитесь в юридический департамент. [S1]"
            if len(calls)==1 and mode=="draft_missing_once":return "1. Составьте заявление. 2. Сохраните копию и передайте адресату. [S1]"
            return good
    class Knowledge:
        passages=[1]
        def search(self,*args,**kwargs):return [{"id":"test:1","document_id":"test","domain":"legal","domains":["legal","finance"],"title":"Источник","text":"Работодатель предоставляет расчёт. Кредитор рассматривает обращение.","source":"https://example.org","reviewed_on":"2026-10-21","section":""}]
    class Login:pass
    class Matrix:
        user="@bot:demo.local"
        def __init__(self,*args,**kwargs):self.callback=None
        def add_event_callback(self,callback,kind):
            if kind==bot.RoomMessageText:self.callback=callback
        async def login(self,*args,**kwargs):return Login()
        async def room_send(self,**kwargs):output.append(kwargs["content"]["body"])
        async def close(self):pass
        async def sync_forever(self,**kwargs):
            await self.callback(SimpleNamespace(room_id=room_id),SimpleNamespace(sender="@user:demo.local",body=question,event_id="$case",server_timestamp=int(time.time()*1000)+1000))
    routes=tmp_path/"routes.json";routes.write_text(json.dumps({room_id:domain}))
    values={"MATRIX_HOMESERVER":"http://homeserver:8008","MATRIX_USER":"@bot:demo.local","MATRIX_PASSWORD":"dummy","LLM_ENDPOINT":"http://litellm:4000/v1","LLM_MODEL":"test-model","OFFLINE_MODE":"0","KNOWLEDGE_DIR":"synthetic","MATRIX_ROOM_ROUTES_FILE":str(routes),"HANDOFF_DB":str(tmp_path/"handoff.sqlite3"),"ELEMENT_PUBLIC_URL":"http://localhost:18081"}
    for key,value in values.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(bot,"AsyncClient",Matrix);monkeypatch.setattr(bot,"LoginResponse",Login)
    monkeypatch.setattr(bot,"KnowledgeStore",lambda _:Knowledge());monkeypatch.setattr(bot,"read_llm_key",lambda:"dummy")
    monkeypatch.setattr(bot,"department_roster",lambda _:{domain:["@employee:demo.local"]})
    monkeypatch.setattr(FlowiseClient,"from_env",lambda _:Flow())
    asyncio.run(bot.main())
    assert len(calls)==(1 if mode=="transport_failure" else 2)
    result=output[-1]
    assert "Обратитесь в юридический департамент" not in result and "Нужна проверка сотрудника?" not in result
    if mode in {"deflect_twice","transport_failure"}:
        assert "!человек" in result
        if mode=="deflect_twice":assert ("!юрдеп" if domain=="legal" else "!финдеп") in result
    else:
        assert "!человек" not in result
    if mode in {"deflect_once","draft_missing_once"}:
        assert ("Кому:" if mode=="draft_missing_once" else "Сохраните договор") in result
        assert any(item["role"]=="assistant" for item in calls[1])
    else:
        assert "Что можно подготовить" in result
        assert "расчётные листки" in result
    if mode=="transport_failure":assert "временно недоступен" in result


def test_a_real_letter_with_named_addressee_is_not_mistaken_for_a_checklist():
    answer="Арендодателю: [имя]\nОт арендатора: [имя]\n\nТребование о предоставлении расчёта\n\nПо моим данным, после возврата жилья удержан переданный мной платёж. Прошу предоставить расчёт удержанных сумм с указанием оснований, подтверждающие документы и письменно сообщить результат рассмотрения."
    assert "requested_draft_missing" not in usefulness_issues("Напиши требование арендодателю",answer)


def test_a_long_refusal_before_an_actual_draft_is_still_detected():
    answer="В предоставленных фрагментах нет норм.\n\nКому: [имя]\nОт: [имя]\n\nПрошу проверить расчёты, объяснить основания удержания средств и письменно сообщить результат рассмотрения. По моим данным, жильё передано и обязательства исполнены, что подтверждается приложенными документами."
    issues=usefulness_issues("Напиши требование арендодателю",answer)
    assert "premature_refusal" in issues and "requested_draft_missing" not in issues


@pytest.mark.parametrize("domain", ["legal","finance"])
@pytest.mark.parametrize("question", [
    "Купил неисправный телефон стоимостью 100000 тенге, хочу вернуть деньги",
    "Сколько стоит нотариальная доверенность?",
    "Как вернуть стоимость товара, купленного в кредит?",
])
def test_a_price_in_a_legal_question_does_not_trigger_request_for_a_bank(domain,question):
    from response_guidance import request_clarification
    from offline_knowledge import needs_bank
    assert not needs_bank(question)
    assert request_clarification(domain,question) is None
