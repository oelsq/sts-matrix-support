"""Immediate guidance for reported fraud, without waiting for the LLM gateway."""
import re
from agents import render

CODE_REQUEST = re.compile(r"(?:просит|просят|запрашива)\w*.*(?:смс|sms|cvv|cvc|парол)|(?:смс|sms)[ -]*код.*(?:просит|просят)", re.I)
FRAUD = re.compile(r"мошенни|безопасн\w* сч[её]т|передал\w*.*(?:код|cvv|cvc|парол)|сообщил\w*.*(?:код|cvv|cvc|парол)|(?:списал|украл|снял)\w*.*(?:деньг|карт)|(?:деньг|карт).*без моего", re.I)


PHONE_LOSS = re.compile(r"(?:укра\w*|потеря\w*|похит\w*)(?:\s+\w+){0,2}\s+телефон\w*", re.I)
DIRECT_MONEY_LOSS = re.compile(
    r"(?:списал|снял)\w*.*(?:деньг|карт|сч[её]т)|"
    r"украл\w*(?:\s+\w+){0,3}\s+(?:деньг\w*|(?:банковск\w*\s+)?карт\w*)", re.I)


def phone_loss_without_reported_bank_fraud(question):
    # A stolen phone and its SIM card are not a report that bank money was
    # stolen. Keep explicit withdrawals, disclosed codes and fraud warnings.
    if not PHONE_LOSS.search(question):return False
    financial=re.sub(r"\b(?:sim|сим)[\s\-\u2010-\u2015]*карт\w*", " ", question, flags=re.I)
    explicit=(DIRECT_MONEY_LOSS.search(financial) or CODE_REQUEST.search(question)
              or re.search(r"мошенни|безопасн\w* сч[её]т|(?:передал|сообщил)\w*.*(?:код|парол|cvv|cvc)",question,re.I))
    return not explicit


def urgent_finance_answer(question, knowledge):
    if phone_loss_without_reported_bank_fraud(question):return None
    if not (FRAUD.search(question) or CODE_REQUEST.search(question)) or knowledge is None:
        return None
    if re.search(r"безопасн\w* сч[её]т", question, re.I) or CODE_REQUEST.search(question):
        sources = [p for p in knowledge.search('finance', 'безопасный счет блокировка карты неизвестным лицам',20)
                   if p['document_id']=='nb-card-safe-account']
        warning = next((p for p in sources if 'безопасный счет' in ' '.join(p['text'].split())
                        and 'никогда не запрашивают' in ' '.join(p['text'].split())), None)
        caution = next((p for p in sources if 'не переводить денежные средства неизвестным лицам' in ' '.join(p['text'].split())), None)
        if warning and caution:
            evidence = [warning] if warning['id']==caution['id'] else [warning,caution]
            ref='[S1]' if len(evidence)==1 else '[S2]'
            introduction = ('**Не сообщайте SMS-коды, пароли и CVV/CVC звонящему.** Нацбанк предупреждает: сотрудники финансовых организаций не запрашивают такие данные. [S1]\n\n'
                            if CODE_REQUEST.search(question) else '')
            return render('finance',
                introduction + '**Не переводите деньги на предложенный «безопасный счёт».** Нацбанк предупреждает о схеме, '
                'в которой под предлогом разблокировки карты или такого перевода пытаются получить доступ к счетам. [S1]\n\n'
                '**Что сделать сейчас**\n'
                '1. Прекратите разговор; не сообщайте SMS-коды, пароли и CVV/CVC. Сотрудники финансовых организаций '
                'не запрашивают пароли и SMS-коды и не просят установить приложение удалённого доступа. [S1]\n'
                '2. Самостоятельно свяжитесь с банком по номеру на карте или в официальном приложении. '
                'Не переводите деньги неизвестным лицам. '+ref+'\n'
                '3. Если уже раскрыли данные или отправили деньги, сразу сообщите об этом банку; '
                'не ждите ответа ИИ или сотрудника в чате.\n\n'
                'Вы пока только получили предложение или уже передали данные/сделали перевод? '
                'Коды и реквизиты сюда присылать не нужно.', question,evidence)
    evidence = [p for p in knowledge.search("finance", "Что делать если предоставил свои данные мошенникам блокировку скомпрометированных банковских карт", 20)
                if p["document_id"] == "nb-faq" and "блокировку" in p["text"] and "скомпрометированных" in p["text"]][:1]
    if not evidence:
        return None
    answer = (
        "Если данные карты раскрыты, действуйте сразу — не ждите ответа в чате.\n\n"
        "**Что сделать сейчас**\n"
        "1. Свяжитесь с банком, чтобы заблокировать скомпрометированную карту — это рекомендация Нацбанка. [S1]\n"
        "2. Обратитесь с заявлением в правоохранительные органы. [S1]\n"
        "3. Используйте официальное приложение банка или известный вам номер, а не реквизиты из подозрительного сообщения.\n\n"
        "**Важно**\nНе отправляйте в этот чат PIN, CVV, пароли и SMS-коды. "
        "Бот не блокирует карту, не возвращает деньги и не подаёт заявление вместо вас.\n\n"
        "Для передачи описания ситуации сотруднику напишите !финдеп; "
        "не откладывайте обращение в банк в ожидании ответа в чате."
    )
    return render("finance", answer, question, evidence)


def legal_urgent_notice(question):
    from answer_usefulness import SERIOUS_RISK
    if not SERIOUS_RISK.search(question):
        return None
    return (
        "Если вопрос срочный, напишите !юрдеп для связи с сотрудником. "
        "Чат не подаёт процессуальные документы и не приостанавливает сроки. "
        "Не откладывайте связь с квалифицированным юристом в ожидании ИИ. "
        "Сейчас проверю доступные локальные документы по вашему вопросу."
    )
