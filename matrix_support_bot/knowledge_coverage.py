"""User-facing scope of the local corpus; file count is not a quality score."""
GROUPS = [
 ('Основные права и обращения в госорганы', {'constitution','administrative-procedure','state-services','access-information'}),
 ('Семья, алименты, дети', {'family','child-rights'}),
 ('Работа, увольнение, зарплата', {'labor','court-labor'}),
 ('Покупки и защита потребителей', {'consumer-law','court-consumer'}),
 ('Договоры, наследство, жильё и недвижимость', {'civil-general','civil-special','housing','real-estate-registration'}),
 ('Суды, жалобы и исполнение решений', {'civil-procedure','criminal-procedure','administrative-offences','enforcement','judicial-system','court-civil-judgment'}),
 ('Уголовное право', {'criminal','criminal-executive'}),
 ('Банки, кредиты, долги и страхование', {'banks','microfinance','collectors','bankruptcy','deposit-guarantee','insurance'}),
 ('Налоги, социальные выплаты и пенсии', {'tax','social'}),
 ('Медицина и образование', {'health','medical-insurance','education'}),
 ('Земля, экология, вода и лес', {'land','environment','water','forest'}),
 ('Гражданство, документы, миграция и воинская служба', {'citizenship','identity-documents','migration','military-service'}),
 ('Бизнес, госзакупки и госслужба', {'entrepreneur','limited-liability','public-procurement','state-service','anticorruption'}),
]

def coverage_answer(knowledge):
    if knowledge is None:
        return 'Локальная база документов пока не подключена.'
    present = {p['document_id'] for p in knowledge.passages}
    rows = [label for label, identifiers in GROUPS if identifiers & present]
    return ('**Какие темы есть в базе**\n\n' + '\n'.join('- '+label for label in rows)
            + '\n\nЗадайте конкретный вопрос: ответ будет основан на найденных положениях со ссылками на источник. '
              'Наличие отрасли в списке не означает, что загружены все её законы, правила и судебные разъяснения. '
              'Для событий прошлых лет может понадобиться другая редакция.')
