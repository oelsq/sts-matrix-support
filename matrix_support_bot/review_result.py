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


class ReviewedAnswer(str):
    """A string for existing callers, with explicit immutable review metadata."""
    def __new__(cls, answer, review):
        result = super().__new__(cls, answer)
        result.review = review
        return result
