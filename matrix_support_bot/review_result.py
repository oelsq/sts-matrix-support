"""Preserve verified provenance and answer completeness through rendering."""
from dataclasses import dataclass


def source_text(source):
    metadata='\n'.join(label+': '+str(source[key]) for key,label in [
        ('title','Название документа'),('section','Раздел документа'),
        ('version','Редакция или версия документа')] if source.get(key))
    return source['text']+('\nМетаданные проверенной карточки документа:\n'+metadata if metadata else '')


@dataclass(frozen=True)
class ReviewDetails:
    # Original source alias and exact literal quote, already validated by the client.
    quotes: tuple[tuple[str, str], ...]
    partial: bool = False
    question_type: str = ''
    retrieval_hints: tuple[str, ...] = ()
    omitted_topics: tuple[str, ...] = ()
    user_context: tuple[str, ...] = ()
    gap_subject: str = ''


OMITTED_TOPICS = {
    'deadline': 'Точный срок пока не подтверждён; его нужно сверить для вашей ситуации.',
    'amount': 'Точная сумма, ставка или комиссия пока не подтверждена.',
    'rule': 'Правовое основание отдельного требования ещё нужно проверить.',
    'conditions': 'Условия и исключения для окончательного вывода ещё нужно уточнить.',
}


def completeness_note(review):
    """Explain only the gap, after the useful part; never echo a rejected claim."""
    if review is None or not review.partial:
        return ''
    details = [OMITTED_TOPICS[key] for key in review.omitted_topics if key in OMITTED_TOPICS]
    scoped={
        'image':{'rule':'Отдельное основание использования изображения не подтверждено; не считайте его разрешением на публикацию.','conditions':'Условия конкретного исключения для использования изображения ещё нужно проверить.'},
        'wage':{'deadline':'Отдельный срок выплаты или ответа ещё нужно сверить для вашего вида отношений.','amount':'Точная сумма долга или пени без исходных данных не установлена.'},
        'apr':{'amount':'Точная ставка или сумма расходов здесь не установлена.','conditions':'Включение отдельного расхода в ГЭСВ зависит от условий займа и применимых правил.'},
    }.get(getattr(review,'gap_subject',''),{})
    details=[scoped.get(key,OMITTED_TOPICS[key]) for key in review.omitted_topics if key in OMITTED_TOPICS]
    if not details:
        details = ['Неподтверждённые детали исключены; выше сохранена самостоятельная проверенная часть ответа.']
    return '\n\n**Ответ подтверждён частично**\n' + ' '.join(details)


class ReviewedAnswer(str):
    """A string for existing callers, with explicit immutable review metadata."""
    def __new__(cls, answer, review):
        result = super().__new__(cls, answer)
        result.review = review
        return result
