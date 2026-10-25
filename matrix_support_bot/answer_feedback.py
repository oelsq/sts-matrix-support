"""Opt-in category feedback; never copy a question, answer or person into feedback."""
CATEGORIES = {'полезно': 'helpful', 'неполно': 'incomplete', 'ошибка': 'suspected_error', 'не помогло': 'unhelpful'}
USAGE = ('Оцените свой последний доставленный ответ в этом чате: '
         '`!оценка полезно`, `!оценка неполно`, `!оценка ошибка` или `!оценка не помогло`. '
         'Текст вопроса и ответа в оценку не копируется. Это обратная связь пользователя, а не проверка нормы специалистом.')


def feedback_reply(store, room, sender, command):
    parts = command.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != '!оценка':
        return None
    category = CATEGORIES.get(' '.join(parts[1].casefold().split()) if len(parts)>1 else '')
    if category is None:
        return USAGE
    status = store.rate_latest_answer(room, sender, category)
    if status == 'no_answer':
        return 'Пока нет вашего доставленного ответа для оценки в этом чате. Сначала задайте вопрос агенту.'
    if status == 'unchanged':
        return 'Эта оценка уже учтена один раз.'
    message = 'Оценка сохранена.' if status == 'recorded' else 'Предыдущая оценка этого ответа заменена новой.'
    if category in {'suspected_error', 'incomplete', 'unhelpful'}:
        message += ' Напишите отдельным сообщением, какая часть ответа требует исправления; личные данные добавлять не нужно.'
    return message
