"""Conservative bank-APR explanations requiring the actual calculation clauses."""
import re
from agents import render
from response_guidance import is_document_request


def checked_gesv_answer(domain,question,evidence):
    if (is_document_request(question) or not re.search(r'гэ[сc]в|эффективн.*ставк',question,re.I)
            or re.search(r'\bмфо\b|микрокредит|рассчита|посчита|вычисл|переплат|какая.*ставк.*(?:halyk|kaspi|freedom)',question,re.I)
            or not re.search(r'что такое|чем.*отлич|разниц|объясн|включ|учитыв|из чего|состав|как.*сравн',question,re.I)):
        return None
    def find(point,phrase):
        return next((p for p in evidence if p['document_id']=='gesv-rules-2026'
                     and p.get('section','').startswith('Пункт '+point+'.')
                     and phrase in ' '.join(p['text'].split())),None)
    base=find('12','включаются платежи клиента по основному долгу и вознаграждению')
    excluded=find('15','включая неустойку и иные виды штрафных санкций')
    physical=find('14','размеры и сроки выплаты, которых известны на дату заключения договора')
    if not all([base,excluded,physical]):return None
    sources=[base,excluded,physical]
    text=('Для банковского займа ГЭСВ отражает годовую стоимость с учётом платежей по основному долгу, вознаграждению '
          'и комиссий и иных платежей, включаемых по правилам расчёта. Она не сводится к одной объявленной процентной ставке. [S1]\n\n'
          '**Важно**\n'
          '- Не любые расходы входят в ГЭСВ: платежи из-за нарушения условий договора, включая неустойку и другие штрафные санкции, исключены. [S2]\n'
          '- Для заёмщика — физического лица учитываются предусмотренные договором комиссии и платежи, размеры и сроки которых известны при заключении договора. '
          'Для платежей, факт взимания которых заранее неизвестен, правила предусматривают перерасчёт при фактической оплате или введении платежа во время обслуживания займа. [S3]')
    if re.search(r'страхов',question,re.I):
        insurance=next((p for p in evidence if p['document_id']=='bank-fees-2026'
                        and 'договоров добровольного страхования' in ' '.join(p['text'].split())
                        and 'влияние на условия' in ' '.join(p['text'].split())),None)
        # Never silently answer only the inherited definition when the current
        # question asks about an insurance cost whose clause was not retrieved.
        if insurance is None:return None
        sources.append(insurance)
        text=('**Для страховки нужно проверить её связь с займом и условия расчёта.** '
              'Нельзя автоматически считать любую страховку включённой в ГЭСВ: в переданном перечне охвачены платежи '
              'по добровольному страхованию, заключение которого влияет на условия предоставления или изменения займа '
              'либо вытекает из условий займа. Сам перечень взимаемых платежей не заменяет правила расчёта ГЭСВ. [S4]\n\n'
              +text)
    text+='\n\nСохраните договор и график платежей. Уточните, речь о банковском займе или микрокредите МФО: это объяснение относится к банковским займам.'
    return render(domain,text,question,sources)
