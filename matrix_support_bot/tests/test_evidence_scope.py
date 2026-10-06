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
