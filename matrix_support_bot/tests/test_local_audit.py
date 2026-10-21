"""Failure-oriented checks from the October local audit (no real LLM calls)."""
from pathlib import Path
import importlib.util
import pytest
import yaml
from agents import unsupported_quantities
from retrieval_intents import explicit_article
from offline_knowledge import KnowledgeStore

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture(scope="module")
def corpus():
    return KnowledgeStore(ROOT / "offline_knowledge")

@pytest.mark.parametrize("source,claim,unsupported", [
    ("Срок пять лет. Комиссия 0 тенге.", "Комиссия 5%.", True),
    ("Ставка 5%.", "Плата 5 тенге.", True),
    ("Срок 5 дней.", "Срок 5 месяцев.", True),
    ("От 18 до 27 лет.", "Комиссия 27%.", True),
    ("Срок 10 календарных дней.", "Срок 10 рабочих дней.", True),
    ("Срок 10 рабочих дней.", "Срок 10 календарных дней.", True),
    ("Срок 10 рабочих дней.", "Срок 10 дней.", False),
    ("Комиссия 0 теңге.", "Комиссия ноль тенге.", False),
    ("Предел 20 миллионов тенге.", "Предел 20 000 000 тенге.", False),
    ("От восемнадцати до двадцати семи лет.", "От 18 до 27 лет.", False),
    ("Комиссия 0,5%.", "Комиссия 0.5 процента.", False),
    ("Срок 12 месяцев.", "Ставка 12%.", True),
    ("Сумма 1600 МРП.", "Сумма 1600 тенге.", True),
])
def test_numbers_require_matching_units(source, claim, unsupported):
    assert unsupported_quantities(claim, "", [{"id":"s", "text":source}], {"s"}) is unsupported

@pytest.mark.parametrize("question,doc,section", [
    ("Объясни статью 113 ТК РК", "labor", "Статья 113."),
    ("Что означает статья 188 УК РК?", "criminal", "Статья 188."),
    ("Поясни ст. 148 ГПК Казахстана", "civil-procedure", "Статья 148."),
    ("Статья 64 УПК", "criminal-procedure", "Статья 64."),
    ("Статья 1072-3 ГК, особенная часть", "civil-special", "Статья 1072-3."),
])
def test_named_article_does_not_pull_other_acts(corpus, question, doc, section):
    evidence=corpus.search("legal", question, 8)
    assert evidence
    assert all(p["document_id"]==doc and p["section"].startswith(section) for p in evidence)

def test_missing_or_ambiguous_article_is_not_invented(corpus):
    assert not corpus.search("legal", "Поясни статью 99999 Трудового кодекса", 8)
    assert explicit_article("Объясни статью 113") is None
    assert explicit_article("Сравни статью 113 ТК и УК") is None

@pytest.mark.parametrize("question,forbidden", [
    ("Хозяин квартиры поднял арендную плату", "Статья 338-4."),
    ("Купил квартиру и регистрирую право собственности", "Статья 15."),
])
def test_colloquial_routing_has_negative_controls(corpus,question,forbidden):
    assert all(not p.get("section", "").startswith(forbidden) for p in corpus.search("legal",question,3))

@pytest.mark.parametrize("question", ["В банке просят сообщить SMS-код", "Сотрудник банка просит СМС-код по телефону"])
def test_sms_request_is_helped_before_model_reply(corpus,question):
    from urgent_help import urgent_finance_answer
    answer=urgent_finance_answer(question,corpus)
    assert answer and "не сообщайте" in answer.casefold()
    assert "Не удалось" not in answer
    assert urgent_finance_answer("Что такое SMS-уведомления о платеже?",corpus) is None


def preflight_module():
    spec=importlib.util.spec_from_file_location("offline_preflight",ROOT/"scripts/offline_preflight.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize("url,accepted", [
    ("http://local-model:8000/v1",True), ("http://192.168.1.20:8000/v1",True),
    ("http://127.0.0.1:8000/v1",True), ("http://[::1]:8000/v1",True),
    ("https://llm.nitec.kz/v1",False), ("https://api.openai.com/v1",False),
    ("http://0.0.0.0:8000/v1",False), ("http://169.254.169.254/v1",False),
    ("http://user:pass@local-model/v1",False), ("file:///model",False),
    ("http://local-model.evil.example/v1",False),
])
def test_offline_upstream_boundary(url,accepted):
    assert preflight_module().local_url(url) is accepted


def test_offline_profile_requires_actual_model_and_rejects_online_config():
    validate=preflight_module().validate
    env={"OFFLINE_MODE":"1","LOCAL_LLM_BASE_URL":"http://local-model:8000/v1","LOCAL_LLM_MODEL":"openai/preloaded-model"}
    config=yaml.safe_load((ROOT/"config/litellm/offline.yaml").read_text())
    assert not validate(config,env)
    assert validate(config,{**env,"LOCAL_LLM_MODEL":"openai/REPLACE_WITH_SERVED_MODEL"})
    assert validate(config,{**env,"LEGAL_LLM_MODEL":"external-alias"})
    assert validate(config,{**env,"OFFLINE_MODE":"0"})
    assert validate(yaml.safe_load((ROOT/"config/litellm/config.yaml").read_text()),{**env,"LLM_PROVIDER_BASE_URL":"https://llm.nitec.kz/v1"})
    assert validate({**config,"router_settings":{"fallbacks":[{"legal-chat":["external"]}]}},env)


def test_product_pricing_scope_does_not_hide_general_legal_rights(corpus):
    from evidence_scope import scope_evidence
    question="В Halyk SuperApp списали незаконную комиссию. Как оспорить?"
    evidence=corpus.search("finance",question,8)
    assert evidence and any(p["document_id"]!="halyk-superapp" for p in evidence)
    samples=[{"document_id":"banks","text":"Права клиента"},{"document_id":"halyk-superapp","text":"Тариф"}]
    assert scope_evidence("finance",question,samples)==samples


def test_offline_start_does_not_require_nitec_and_does_not_pull(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(ROOT/"scripts"))
    spec=importlib.util.spec_from_file_location("setup_audit",ROOT/"scripts/setup.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    env={"OFFLINE_MODE":"1","LOCAL_LLM_BASE_URL":"http://local-model:8000/v1","LOCAL_LLM_MODEL":"openai/preloaded-model"}
    monkeypatch.setattr(module,"settings",lambda:env)
    calls=[]
    monkeypatch.setattr(module,"compose",lambda *args:calls.append(args))
    monkeypatch.setattr(module,"provision",lambda:calls.append(("provision",)))
    module.start()
    assert "--pull" in calls[0] and "never" in calls[0] and "--no-build" in calls[0]
    assert calls[-1] == ("provision",)
    monkeypatch.setattr(module,"settings",lambda:{**env,"LOCAL_LLM_BASE_URL":"https://external.example/v1"})
    with pytest.raises(RuntimeError,match="Offline preflight failed"):
        module.start()


def test_offline_compose_provisioning_uses_overlay_without_network_pulls(monkeypatch,tmp_path):
    spec=importlib.util.spec_from_file_location("setup_compose_audit",ROOT/"scripts/setup.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    envfile=tmp_path/".env";envfile.write_text("OFFLINE_MODE=1\n")
    monkeypatch.setattr(module,"ENV",envfile)
    calls=[]
    monkeypatch.setattr(module.subprocess,"run",lambda command,**kw:calls.append(command))
    module.compose("run","--rm","bot")
    module.compose("up","-d","bot")
    assert all("compose.offline.yml" in command for command in calls)
    assert "never" in calls[0] and "never" in calls[1] and "--no-build" in calls[1]
