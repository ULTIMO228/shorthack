# Phase 1 — Data Model: ядро (001)

Хранилище: SQLite, SQLAlchemy 2.0, `Base.metadata.create_all()` при старте. Все таблицы — префикс ядра; старая таблица `links` удаляется.

## Таблицы

### users — Пользователь
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| email | String, unique, not null | домен `@misis.ru`/`@edu.misis.ru` (проверка при login) |
| full_name | String, not null | |
| group_name | String, null | группа студента / подразделение сотрудника |
| role | String, not null | `student` / `staff` / `operator` |

Связи: 1→N requests, 1→N cert_orders, 1→N sessions.

### sessions — Сессия (FR-001..003 фичи 002, гостевой режим FR-016)
| Поле | Тип | Правила |
|---|---|---|
| id | String PK (token) | `secrets.token_urlsafe(32)` (LESSONS L003) |
| user_id | FK users.id, **null** | null = гостевая сессия (без почты МИСИС) |
| created_at | DateTime | |

Гостевая сессия создаётся автоматически при первом обращении без входа; после логина гостевая сессия привязывается к user_id (диалог сохраняется).

### requests — Обращение
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| user_id | FK users.id, **null** | null для гостя; заказ справок требует not null (FR-043) |
| session_id | FK sessions.id, not null | сессия автора (пользовательская или гостевая) — защита диалога и дедупликация |
| channel | String, not null | `web` / `telegram` |
| raw_text | Text, not null | исходник (доступен оператору, FR-002) |
| masked_text | Text, not null | мат замаскирован (`*`) |
| lang | String(2) | `ru` / `en` / прочие ISO-2 |
| translation | Text, null | перевод на русский, если lang ≠ ru (FR-003) |
| duplicate_of_ticket_id | FK tickets.id, null | заполнено = дубль (FR-014) |
| created_at | DateTime | |

### subtasks — Подзадача (результат сплиттера и классификатора)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| request_id | FK requests.id, not null | |
| position | Integer | порядок в обращении |
| summary | Text | суть одной строкой |
| service | String, null | `site` / `lms` / `wifi_guest` / `wifi_edu` / `wifi_corp` / `account` / `certs` / `other` |
| category | String | `availability` / `access` / `howto` / `cert_order` / `incident_report` / `other` |
| route | String | `auto_check` / `kb` / `cert_order` / `escalate` |
| route_reason | Text | обоснование маршрута (FR-012) |
| priority | String | `critical` / `high` / `medium` / `low` (после арбитража FR-011) |
| confidence | Float | 0..1 от классификатора (FR-013) |
| status | String | жизненный цикл FR-015 |

Связи: 1→N service_checks. Уникальность однотипности (service, category) — правило Q1.

### tickets — Заявка (агрегат по обращению)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| request_id | FK requests.id, not null | |
| status | String, not null | **новая → в работе → ждёт ответа пользователя → решена → закрыта** (FR-015) |
| escalated | Boolean, default false | эскалация — признак, не статус (Q2) |
| incident_id | FK incidents.id, null | привязка к инциденту (FR-052) |
| dialog_rounds | Integer, default 0 | лимит 2 (FR-025) |
| created_at / updated_at | DateTime | |

Переходы статусов: `новая`→`в работе` (первая реакция агента); `в работе`→`ждёт ответа пользователя` (задан уточняющий вопрос); `ждёт ответа пользователя`→`в работе` (ответ получен); любой→`решена` (выдан финальный ответ/извещение); `решена`→`закрыта` (оператор/автомат). Молчание пользователя переходов не вызывает (Q3).

### services — Сервис (статус-борд, FR-024)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| name | String, unique | `misis.ru` / `newlms.misis.ru` / `MISIS-Guest` / `MISIS-EDU` / `MISIS-CORP` |
| check_type | String | `real` / `emulated` |
| state | String | `up` / `down` |
| updated_at | DateTime | |

### service_checks — Результат проверки (FR-022/023)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| service_id | FK services.id | |
| subtask_id | FK subtasks.id, null | |
| ok | Boolean | «доступен» = HTTP 2xx/3xx ≤ 5 c (Q4) |
| http_code | Integer, null | |
| latency_ms | Integer, null | |
| checked_at | DateTime | |

### kb_articles — Статья базы знаний (FR-030)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| kind | String | `document` / `template` |
| title | String | |
| body | Text | |
| topic | String | `wifi` / `password` / `lms` / `certs` / `support` / `other` |
| embedding | Text (JSON) | 256 float; null пока не проиндексирована |
| source | String | `seed` / `operator` (FR-063) |
| confirmed | Boolean | черновик от LLM → true после подтверждения оператором |

### cert_orders — Заказ справки (FR-040..043)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| user_id | FK users.id, not null | только авторизованный (FR-043) |
| cert_type | String, not null | `payments` / `callup` / `medical` / `study` / `military` |
| status | String, not null | **не обработана → обрабатывается → готова и ждёт выдачи → забрана** (FR-042) |
| created_at / updated_at | DateTime | |

Переходы — только вперёд по цепочке; смена — оператором (админка, фича 002).

### incidents — Инцидент (FR-050..053)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| service | String | |
| category | String | |
| window_start | DateTime | начало окна детекции (15 мин, настраивается) |
| request_count | Integer | |
| status | String | `active` / `resolved` |
| notified_at | DateTime, null | уведомление дежурным ≤ 1 мин (SC-005) |
| broadcast_at | DateTime, null | массовый ответ (FR-053) |

### events — Журнал действий (FR-061)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| ticket_id | FK tickets.id | |
| actor | String | `user` / `agent` / `tool:<name>` / `operator` / `system` |
| action | String | `classified` / `tool_call` / `tool_result` / `reaction_sent` / `status_change` / `escalated` / … |
| payload | Text (JSON) | детали (результаты проверок, confidence и т.п.) |
| created_at | DateTime | |

### tg_links — Привязка Telegram (фича 003)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| chat_id | BigInteger, unique | |
| user_id | FK users.id, null | null до привязки |
| state | String | `awaiting_email` / `awaiting_confirm` / `idle` / `dialog:<request_id>` |
| confirm_code | String(6), null | код привязки (эмуляция) |
| created_at | DateTime | |

### outbound_messages — Исходящие в бот (фича 003)
| Поле | Тип | Правила |
|---|---|---|
| id | Integer PK | |
| chat_id | BigInteger | пользователю или дежурному |
| text | Text | |
| status | String | `pending` / `sent` / `failed` |
| created_at | DateTime | |

## Ключевые правила валидации (из спеки)

- Однотипность = совпадение `(service, category)` в subtasks (Q1) — используется детектором и дедупликацией. Дедупликация: у авторизованного — по user_id; у гостя — по session_id в пределах гостевой сессии (FR-016). Детектор инцидентов считает distinct авторов по `COALESCE(user_id, session_id)`.
- `subtasks.confidence < 0.6` (настраивается) → route=`escalate` независимо от модели (FR-013).
- Приоритет после арбитража: подтверждённый сбой (`services.state=down` при проверке) → `critical`; активный инцидент по (service, category) → не ниже `high` (FR-011).
- Номер заявки для показа: `SUP-2026-<id:04d>` (генерируется из id, не хранится).
