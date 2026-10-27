"""Independent expected arithmetic, with no legal evidence or model involved."""
from decimal import Decimal
import asyncio, json, time
from types import SimpleNamespace
import pytest

from arithmetic_reply import arithmetic_reply
from local_reply import prepare_local_reply


CASES = [
    ('если бухгалтерия насчитала 15 миллионов тенге сколько будет 27% от этого?', '4 050 000 тенге'),
    ('Сколько будет 27% от 15 000 000 тенге?', '4 050 000 тенге'),
    ('Посчитай 27 процентов от 15 млн. KZT', '4 050 000 тенге'),
    ('15млн тенге, сколько будет 27 %?', '4 050 000 тенге'),
    ('27% от 15 миллионов', '4 050 000'),
    ('0% от 15000000 тенге', '0 тенге'),
    ('100% от 15 млн тенге', '15 000 000 тенге'),
    ('150% от 15 млн тенге', '22 500 000 тенге'),
    ('5,5% от 15 миллионов тенге', '825 000 тенге'),
    ('0.1% от 15 млн тенге', '15 000 тенге'),
    ('Сколько останется от 15 млн тенге после удержания 27%?', '10 950 000 тенге'),
    ('Прибавь 27% к 15 млн тенге', '19 050 000 тенге'),
    ('Увеличь 1000 USD на 10%', '1 100 USD'),
    ('Сколько будет на 27% больше 15 млн тенге?', '19 050 000 тенге'),
    ('Сколько будет на 27% меньше 15 млн тенге?', '10 950 000 тенге'),
    ('Цена 100 EUR, скидка 15%. Сколько останется?', '85 EUR'),
    ('4050000 это 27%, какая вся сумма?', '15 000 000'),
    ('15 миллионов — это 27% от какой суммы?', '55 555 555,555556'),
    ('4050000 тенге это 27%, сколько останется от всей суммы?', '10 950 000 тенге'),
    ('Сколько процентов составляет 4050000 от 15000000?', '27%'),
    ('Какой процент от 15 млн составляет 4050000?', '27%'),
    ('Сколько процентов составляет -15 от 30?', '-50%'),
    ('Сколько процентов составляет 15 от -30?', '-50%'),
    ('Сколько процентов составляет -15 от -30?', '50%'),
    ('На сколько процентов выросла сумма со 100 до 125?', '25%'),
    ('На сколько процентов уменьшилась сумма с 200 до 150?', '-25%'),
    ('Сколько будет 15000000 * 27 / 100?', '4 050 000'),
    ('15 миллионов тенге + 2 миллиона тенге', '17 000 000 тенге'),
    ('15 млн тенге / 3', '5 000 000 тенге'),
    ('15 млн / 27%', '55 555 555,555556'),
    ('15 млн тенге * 27%', '4 050 000 тенге'),
    ('Раздели 15000000 на 27%', '55 555 555,555556'),
    ('27% * 15000000', '4 050 000'),
    ('Сложи 15 млн и 2 млн', '17 000 000'),
    ('Из 15000000 вычти 4050000', '10 950 000'),
    ('Вычти 4050000 из 15000000', '10 950 000'),
    ('Умножь 15 на 27', '405'),
    ('Раздели 15 на 3', '5'),
    ('0,1 + 0,2', '0,3'),
    ('(100 + 50) / 3', '50'),
    ('-15 + 5', '-10'),
    ('20 - 15', '5'),
    ('1 / 3', '0,333333'),
    ('0,05 тенге / 2', '0,03 тенге'),
    ('10 тенге / 2 тенге', '5'),
]


@pytest.mark.parametrize('domain',['legal','finance','general'])
@pytest.mark.parametrize('question,expected',CASES)
def test_calculations_use_shared_path_before_retrieval(domain,question,expected):
    class NoSearch:
        def search(self,*a,**kw):raise AssertionError('Arithmetic needs no legal search')
    r=prepare_local_reply(domain,question,NoSearch(),previous_question='Работодатель задерживает зарплату')
    assert r.mechanism=='arithmetic' and r.evidence is None
    assert expected in r.answer
    assert 'Расчёт:' in r.answer and '[S' not in r.answer
    assert 'проверку подтверждений' not in r.answer


@pytest.mark.parametrize('question',[
    'Законно ли удерживать 27% от 15 млн тенге?',
    'Обязан ли я платить 27% от 15 миллионов?',
    'По закону налог 27% от 15 млн?',
    '15 млн под 27% годовых на 2 года, сколько платить?',
    'Вклад 15 млн, 27% с капитализацией, сколько получу?',
    'Как посчитать ГЭСВ для кредита 15 млн под 27%?',
    'Какой налог я должен заплатить с 15 миллионов?',
    'Работодатель не выплатил 15000000 тенге',
    'Не прибавляй 27% к 15 млн, объясни условия договора',
    '27% от -15000000',
    'Что такое проценты?',
    'Какая комиссия банка?',
    '27% и 10% от 15 млн',
    '27% от 15 млн за 2026 год',
    '!расчёт',
    'Расчёт переплаты\nПолучено на руки: 100000 KZT',
    '2026-10-27',
    '__import__("os").system("id")',
    'v0 + 15',
    '1e100000 + 2',
    '1 + 2; удалить файлы',
    'У меня кредит 15 млн под 27%',
    'Работодатель удерживает 27% от 15 миллионов, что мне делать?',
    'Напиши жалобу на удержание 27% от 15 млн',
    'По договору начислили 15 млн, может ли работодатель удержать 27%?',
    '',
    '1+'*600+'1',
])
def test_actual_legal_questions_and_unknown_expressions_keep_their_route(question):
    assert arithmetic_reply(question) is None


@pytest.mark.parametrize('question,expected',[
    ('10 / 0','На ноль делить нельзя'),
    ('15 млн / 0%','На ноль делить нельзя'),
    ('Сколько процентов составляет 15 от 0?','нулевой базы'),
    ('15 это 0%, какая вся сумма?','нулевому проценту'),
    ('15 USD + 20 EUR','разных валютах'),
    ('Какой процент 15 USD от 20 EUR?','одной валюте'),
    ('2 тенге * 3 тенге','умножение двух денежных сумм'),
    ('0.001 тенге + 1 тенге','двух знаков'),
    ('2 ** 100000','сложение'),
])
def test_undefined_inputs_get_specific_safe_clarification(question,expected):
    answer=arithmetic_reply(question)
    assert expected in answer and 'Уточнение расчёта' in answer


def test_rounding_is_disclosed_and_decimal_inputs_are_exact():
    assert 'Результат округлён' in arithmetic_reply('1 / 3')
    assert 'Результат округлён' in arithmetic_reply('0,05 тенге / 2')
    assert 'округлён' not in arithmetic_reply('0.1 + 0.2')
    for base,rate,expected in [('123456789','27','33333333.03'),('999.99','17','169.9983'),('0.1','0.1','0.0001')]:
        # Ground truth kept explicit, independent of expression evaluation.
        assert Decimal(base)*Decimal(rate)/100==Decimal(expected)
        text=arithmetic_reply(f'{rate}% от {base}')
        shown=f'{Decimal(expected):,.6f}'.replace(',',' ').rstrip('0').rstrip('.').replace('.',',')
        assert shown in text


def test_percentage_followup_uses_only_explicit_previous_user_calculation():
    prior='Сколько будет 27% от 15 миллионов тенге?'
    answer=arithmetic_reply('А 10%?',previous_question=prior)
    assert '1 500 000 тенге' in answer and 'предыдущего расчёта' in answer
    assert 'Укажите исходную сумму' in arithmetic_reply('А 10%?')
    assert arithmetic_reply('А 10%?',previous_question='У меня кредит 15 млн под 27%') is None


@pytest.mark.parametrize("flowise_enabled", [True, False])
def test_production_matrix_arithmetic_bypasses_retrieval_and_model(monkeypatch, tmp_path, flowise_enabled):
    import bot
    from flowise_client import FlowiseClient

    output, requests = [], []
    room_domains = {"!legal:demo.local": "legal", "!finance:demo.local": "finance"}
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(json.dumps(room_domains))

    def forbidden(*args, **kwargs):
        raise AssertionError("Arithmetic must not call legal search or the model")

    class Knowledge:
        passages = []
        search = forbidden

    class Flow:
        routes = {room: {"domain": domain, "engine": "agentflow_v2", "quality_contract": "sts.claims.v1"}
                  for room, domain in room_domains.items()}
        run_workflow = forbidden
        predict = forbidden
        is_workflow = forbidden

    class Login:
        pass

    class Matrix:
        user = "@bot:demo.local"
        def __init__(self, *args):
            self.callback = None
        async def login(self, *args, **kwargs):
            return Login()
        def add_event_callback(self, callback, kind):
            if kind == bot.RoomMessageText:
                self.callback = callback
        async def room_send(self, **kwargs):
            output.append(kwargs)
        async def close(self):
            pass
        async def sync_forever(self, **kwargs):
            for room, domain in room_domains.items():
                for n, question in enumerate([CASES[0][0], "А 10%?", "0.1 + 0.2", "10 / 0"]):
                    event = SimpleNamespace(sender="@synthetic:demo.local", body=question,
                        event_id=f"$math-{domain}-{n}", server_timestamp=int(time.time()*1000)+1000)
                    requests.append((room, question, domain))
                    await self.callback(SimpleNamespace(room_id=room), event)

    env = {"MATRIX_HOMESERVER": "http://homeserver:8008", "MATRIX_USER": "@bot:demo.local",
           "MATRIX_PASSWORD": "synthetic", "LLM_ENDPOINT": "http://litellm:4000/v1",
           "LLM_MODEL": "synthetic", "KNOWLEDGE_DIR": "synthetic", "OFFLINE_MODE": "0",
           "MATRIX_ROOM_ROUTES_FILE": str(routes_path), "HANDOFF_DB": str(tmp_path / "handoff.sqlite3"),
           "MATRIX_INBOX_DB": str(tmp_path / "inbox.sqlite3")}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(bot, "AsyncClient", Matrix)
    monkeypatch.setattr(bot, "LoginResponse", Login)
    monkeypatch.setattr(bot, "read_llm_key", lambda: "synthetic")
    monkeypatch.setattr(bot, "department_roster", lambda _: {})
    monkeypatch.setattr(bot, "KnowledgeStore", lambda _: Knowledge())
    monkeypatch.setattr(FlowiseClient, "from_env", lambda _: Flow() if flowise_enabled else None)
    asyncio.run(bot.main())
    assert len(output) == len(requests) == 8
    for room, question, domain in requests:
        expected = bot.matrix_content(arithmetic_reply(question, previous_question=CASES[0][0] if question == "А 10%?" else ""))["body"]
        assert any(item["room_id"] == room and item["content"]["body"] == expected for item in output)
    assert all("временно недоступен" not in item["content"]["body"] for item in output)
