"""One source-bound explanation of an explicit employment/contract comparison."""
import re


def employment_comparison(domain,question,evidence):
    from response_guidance import is_document_request
    if domain not in {'legal','finance'} or is_document_request(question):return None
    if (not re.search(r'трудов\w*.{0,15}договор',question,re.I)
            or not re.search(r'услуг|гпх|подряд|гражданск',question,re.I)
            or not re.search(r'отлич|разниц|сравн',question,re.I)
            or re.search(r'могу ли|признать|переквалифиц|взыск|увол|\bсуд\w*',question,re.I)):return None
    for passage in evidence or []:
        text=' '.join(passage.get('text','').split()).casefold().replace('ё','е')
        if (passage.get('document_id')!='labor' or not re.match(r'Статья 27\.',str(passage.get('section','')))
                or not all(part in text for part in ['одного из следующих условий','квалификации','лично','трудовому распорядку','заработной платы за труд'])):continue
        from agents import render
        answer=(
            'Главное отличие — **условия работы, а не только название договора**. '
            'Сохранённая статья Трудового кодекса выделяет наличие **одного из перечисленных признаков** трудового договора. [S1]\n\n'
            '- Выполнение работы по определённой квалификации, специальности, профессии или должности. [S1]\n'
            '- Личное выполнение работы с подчинением трудовому распорядку. [S1]\n'
            '- Получение заработной платы за труд. [S1]\n\n'
            'Для сравнения с договором услуг или подряда проверьте пункты об обязанностях, организации работы и оплате. '
            'Не делайте вывод только по заголовку «ГПХ». '
            'Если хотите разобрать свой договор, приведите эти пункты без личных данных: применимость правила к вашей ситуации требует проверки обстоятельств, а не автоматического вывода по одному слову.')
        return render(domain,answer,question,[passage])
    return None
