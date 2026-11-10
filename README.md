# STS Matrix Support

Два отдельных ИИ-помощника для Казахстана: ЮрАгент и ФинАгент.

**Element → Synapse → Python-бот → поиск по локальным документам → Qwen через LiteLLM → Element.**

Основной режим — один прямой вызов модели. Инструкции: `matrix_support_bot/agents/free_common.md`, `free_legal.md`, `free_finance.md`; реализация: `free_reply.py`. Расчёты выполняются кодом. База документов помогает ответу, но её отсутствие не блокирует полезное объяснение. Веса модели здесь не переобучаются.

## Основные файлы

| Путь | Назначение |
|---|---|
| `matrix_support_bot/bot.py` | Принимает сообщения, команды, выбирает специалиста и отправляет ответ |
| `matrix_support_bot/free_reply.py` | Прямой запрос модели, настройки генерации и оформление ответа |
| `matrix_support_bot/llm_settings.py` | Модель и ключ шлюза для каждой роли |
| `matrix_support_bot/offline_knowledge.py` | Загрузка и поиск по локальной базе |
| `offline_knowledge/manifest.json` | Документы, источники, редакции и даты проверки |
| `matrix_support_bot/conversation.py` | Контекст уточнений конкретного пользователя |
| `matrix_support_bot/human_handoff.py` | Выбор и смена сотрудников департамента |
| `matrix_support_bot/matrix_inbox.py`, `matrix_delivery.py` | Очередь, повтор доставки, защита от дубликатов |
| `matrix_support_bot/matrix_crypto.py`, `matrix_verification.py` | Шифрование и проверка устройств |
| `matrix_support_bot/private_rooms.py`, `state_cipher.py` | Личные комнаты и защищённое состояние |
| `config/` | Настройки инфраструктуры и модели |
| `docker-compose.yml` | Запуск сервисов |
| `compose.offline.yml`, `compose.semantic.yml`, `compose.secure.yml` | Дополнительные профили закрытой сети, смыслового поиска и E2EE |
| `scripts/` | Установка, обновление знаний, тестирование |
| `matrix_support_bot/tests/` | Автоматические проверки |
| `support-frontend/` | Отдельная демонстрационная панель тикетов |
| `docs/` | Руководства; исторические этапы проверок находятся в `reports/` |

## Новая установка

Нужны Docker Compose v2 и Python 3. Для внешнего провайдера нужен свой ключ, для закрытой сети — локальный сервер модели и заранее загруженные зависимости.

```bash
python3 scripts/setup.py init
# Настройте .env по docs/OFFLINE.md либо укажите собственный NITEC_API_KEY.
python3 scripts/setup.py start
```

Element: http://localhost:8081; панель: http://localhost:8080; API: http://localhost:8010/health. Пароли создаются локально в `runtime/accounts.txt`; секреты не публикуются в Git. Не меняйте MATRIX_SERVER_NAME существующей установки. `docker compose down` сохраняет данные, `down -v` удаляет тома.

## Команды

`!помощь` — возможности; `!новый` — новая тема; `!база` — знания; `!сотрудники` — сотрудники; `!человек`, `!юрдеп`, `!финдеп` — помощь человека; `!юрдеп2`, `!финдеп3` — выбор сотрудника; `!другой` — смена. Оценка ответа: `!оценка полезно`, `!оценка неполно`, `!оценка ошибка`, `!оценка не помогло`.

## Проверки

```bash
python -m pytest -q
python scripts/evaluate_answers.py --output /tmp/sts-retrieval.json
```

Модель, инструкции, локальные документы и параметры текущего прямого режима сохранены. Исторические отчёты отражают прошлые версии и не являются конфигурацией запуска. Новые функции проверяются отдельно; автотесты не заменяют экспертную проверку юридических выводов.

Подробнее: [закрытая сеть](docs/OFFLINE.md), [знания](docs/knowledge.md), [прямой режим](docs/free-mode.md), [шифрование](docs/encryption.md), [доступ к VM](docs/vm-access.md).
