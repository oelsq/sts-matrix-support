from conversation import ConversationMemory


def test_three_followups_keep_user_facts_but_not_other_people_or_rooms():
    m = ConversationMemory()
    root = "Halyk SuperApp комиссия за оплату коммунальных услуг?"
    assert m.resolve("room", "alice", "finance", root) == (root, "")
    m.resolve("room", "alice", "finance", "А для пенсионеров?")
    effective, context = m.resolve("room", "alice", "finance", "И если через приложение?")
    assert "Halyk" in effective and "пенсионеров" in context
    for room, sender, domain in [("other", "alice", "finance"), ("room", "bob", "finance"), ("room", "alice", "legal")]:
        assert m.resolve(room, sender, domain, "А сколько?") == ("А сколько?", "")


def test_bank_change_and_new_problem_reset_old_topic():
    m = ConversationMemory()
    m.resolve("r", "u", "finance", "Halyk SuperApp комиссия?")
    assert m.resolve("r", "u", "finance", "А какая комиссия в Freedom?")[1] == ""
    assert "Halyk" not in m.resolve("r", "u", "finance", "И за перевод?")[0]
    m.resolve("r", "u", "legal", "А работодатель не платит зарплату?")
    effective, _ = m.resolve("r", "u", "legal", "Что дальше?")
    assert "работодатель" in effective and "Freedom" not in effective


def test_expiry_capacity_clear_and_length_bounds():
    now = [0]
    m = ConversationMemory(ttl=10, capacity=2, turns=3, clock=lambda: now[0])
    m.resolve("r", "a", "legal", "Зарплата задержана")
    now[0] = 10
    assert m.resolve("r", "a", "legal", "А дальше?")[1] == ""
    m.resolve("r", "b", "legal", "Тема b")
    m.resolve("r", "c", "legal", "Тема c")
    assert m.resolve("r", "a", "legal", "А дальше?")[1] == ""
    m.clear("r", "a")
    assert m.resolve("r", "a", "legal", "А дальше?")[1] == ""
    for _ in range(10):
        m.resolve("r", "a", "legal", "А " + "текст " * 15)
    assert len(m.resolve("r", "a", "legal", "И дальше?")[1]) <= 2800


def test_same_bank_and_same_employer_followup_retain_context():
    m = ConversationMemory()
    m.resolve("r", "u", "finance", "Halyk SuperApp комиссия за коммунальные услуги?")
    assert "SuperApp" in m.resolve("r", "u", "finance", "А в Halyk для пенсионеров?")[0]
    m.resolve("r", "u", "legal", "Работодатель задерживает зарплату два месяца")
    assert "два месяца" in m.resolve("r", "u", "legal", "А если работодатель обещает завтра?")[0]
    assert m.resolve("r", "u", "legal", "А арендодатель не возвращает деньги?")[1] == ""


def test_letter_request_can_continue_without_introductory_a():
    memory = ConversationMemory()
    memory.resolve('r','worker','finance','Начальник не отдаёт деньги за работу')
    assert 'Начальник' in memory.resolve('r','worker','finance','Напиши требование работодателю')[0]
    assert memory.resolve('r','worker','finance','Составь претензию магазину о неисправном товаре')[1] == ''
