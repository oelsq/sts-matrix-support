from evidence_scope import scope_evidence

def test_explicit_superapp_communal_rejects_other_channel_and_other_zero_row():
    candidates = [
        {"document_id": "halyk-debit", "text": "Коммунальные услуги: 60 теңге"},
        {"document_id": "halyk-superapp", "text": "Переводы 0 теңге"},
        {"document_id": "halyk-superapp", "text": "Оплата коммунальных услуг 0 теңге"},
    ]
    assert scope_evidence("finance", "Halyk SuperApp комиссия за коммунальные услуги?", candidates) == [candidates[2]]

def test_no_matching_product_evidence_does_not_substitute_other_product():
    assert scope_evidence("finance", "Халык Super App коммунальные услуги", [{"document_id":"halyk-debit","text":"60 теңге"}]) == []

def test_legal_and_general_finance_remain_unrestricted():
    evidence = [{"document_id":"banks","text":"Права заёмщика"}]
    assert scope_evidence("finance", "Права заёмщика", evidence) == evidence
    assert scope_evidence("legal", "Трудовой кодекс", evidence) == evidence

def test_pdf_line_breaks_do_not_remove_the_matching_tariff_row():
    evidence = [{"document_id":"halyk-superapp","text":"Оплата\n коммунальных   услуг\t0 теңге"}]
    assert scope_evidence("finance", "Halyk SuperApp коммунальные услуги", evidence) == evidence


def test_salary_statistics_do_not_use_wage_rules_as_actual_national_average():
    unrelated = [{"document_id": "law", "text": "Средняя заработная плата работника за год определяется работодателем."},
                 {"document_id": "nb", "text": "Выплата 100 тенге. 2026 год."}]
    assert scope_evidence("finance", "Какая средняя зарплата в Казахстане?", unrelated) == []
    bulletin = {"document_id": "statistics", "document_kind": "statistical_bulletin", "text": "Средняя месячная заработная плата за I квартал 2026 года составила 123 тенге."}
    assert scope_evidence("finance", "Какая средняя зарплата?", [bulletin]) == [bulletin]
    assert scope_evidence("finance", "Какая медианная зарплата?", [bulletin]) == []


def test_statutory_compensation_is_not_a_statistical_bulletin():
    law = {"document_id":"law", "text":"Средняя заработная плата за 2026 год для примера 123 тенге."}
    assert scope_evidence("finance", "Какая средняя зарплата в Казахстане?", [law]) == []


def test_employee_average_earnings_rules_are_not_filtered_as_national_statistics():
    evidence = [{'document_id':'labor','text':'Правила исчисления средней заработной платы работника.'}]
    assert scope_evidence('legal','Как рассчитывается средняя заработная плата при увольнении?',evidence) == evidence
    assert scope_evidence('finance','Средняя зарплата работника для отпускных?',evidence) == evidence


def test_rejected_statistics_do_not_claim_the_document_is_missing():
    from response_guidance import practical_fallback
    answer = practical_fallback('finance','Какая средняя зарплата в Казахстане?',validation_failed=True)
    assert 'материал найден' in answer and 'нет подходящего' not in answer


def test_ongoing_wage_payment_does_not_use_termination_deadline_or_unrelated_law():
    evidence=[{'id':'labor:1','document_id':'labor','section':'Статья 113. Выплата',
               'text':'Работодатель выплачивает задолженность и пеню.\n 4. При прекращении договора выплата не позднее трех рабочих дней.'},
              {'id':'labor:2','document_id':'labor','section':'Статья 114. Расчёт',
               'text':'Другие правила расчёта.'},
              {'id':'tax:1','document_id':'tax','section':'Статья 65. Налоги','text':'Налоги.'}]
    scoped=scope_evidence('finance','А как написать требование?\nПредыдущий вопрос: Начальник не отдаёт деньги за работу',evidence)
    assert len(scoped)==1 and scoped[0]['id']=='labor:1'
    assert 'прекращении' not in scoped[0]['text']
    assert 'прекращении' in evidence[0]['text']  # Shared source object is not mutated.
    terminated=scope_evidence('finance','Уволили, не отдают зарплату',evidence)
    assert 'трех рабочих дней' in terminated[0]['text']
