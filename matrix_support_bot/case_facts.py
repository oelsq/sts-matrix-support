"""Bounded fact hints from user text; they do not establish legal classification."""
import re


def case_facts(question,previous=''):
    current,_,inherited=question.partition('\nПредыдущий вопрос:')
    previous=previous or inherited
    q=(current+'\n'+previous).casefold();facts={};questions=[];kind='general'
    from term_definitions import definition_term
    if definition_term(current):
        return {'kind':'reference','user_reported_facts':{},'clarifications':[],
                'instruction':'Запрошено значение термина. Само упоминание договора не сообщает, что пользователь его заключил.'}
    role_text=current.casefold() if re.search(r'гпх|подряд|оказани.{0,12}услуг|трудов.{0,15}договор|без договора',current,re.I) else q
    from retrieval_intents import colloquial_work_payment
    if colloquial_work_payment(q) or re.search(r'зарплат|начальник|работодател|ден[ье]г.*за работу|оплат.*(?:работ|услуг)|подряд|гпх|оказани.{0,12}услуг',q):
        kind='work_payment'
        civil=bool(re.search(r'гпх|договор\w*\s+подряда|договор\w*\s+(?:на\s+)?оказани\w*\s+услуг',role_text))
        no_written=bool(re.search(r'без\s+(?:оформлен|договора)|нет\s+трудов\w*\s+договора|трудов\w*\s+договор\w*\s+нет',role_text))
        labour=bool(re.search(r'(?:есть|заключ[её]н|оформлен|по)\s+трудов\w*\s+договор',role_text)) and not no_written
        if re.search(r'чем.{0,60}отлич|разниц|сравн',current,re.I):civil=False;labour=False;no_written=False
        facts['contract_reported']='mixed' if civil and labour else 'civil' if civil else 'labour' if labour else 'no_written_contract' if no_written else 'unknown'
        if facts['contract_reported']=='unknown':questions.append('У вас трудовой договор, договор на оказание услуг или работа без письменного оформления?')
        facts['termination_reported']=bool(re.search(r'уволили|уволился|после увольнения|договор расторгнут',q))
        if re.search(r'рассчита|посчита|сколько.*пен',q):questions.append('Какие сумма задолженности, дата ожидаемой выплаты и дата фактической оплаты?')
    elif re.search(r'(?:телефон|товар|покупк).*(?:слом|дефект|неисправ)|(?:вернут|возврат).*(?:товар|телефон)',q):
        kind='consumer_defect'
        warranty_text=current.casefold() if re.search(r'гаранти',current,re.I) else q
        facts['warranty_reported']='absent' if re.search(r'нет гарантии|гарантии нет|без гарантии',warranty_text) else 'present' if re.search(r'на гарантии|гарантия есть|есть гарантия',warranty_text) else 'unknown'
        if facts['warranty_reported']=='unknown':questions.append('Есть ли гарантия и когда обнаружился недостаток?')
        if not re.search(r'купил\w*\s+\d|\d.{0,15}(?:дн|месяц|недел)|\d{4}-\d{2}-\d{2}',q):questions.append('Когда вы купили товар и какое требование хотите предъявить продавцу?')
    return {'kind':kind,'user_reported_facts':facts,'clarifications':questions[:2],
            'instruction':'Это сведения пользователя и пробелы, а не правовая квалификация. При неизвестном основании формулируй вывод условно, предложи безопасное действие и задай только существенные вопросы. Отсутствие письменного договора само по себе не доказывает ГПХ.'}
