# PROJECT MAP (MISIS Support Assistant) — densecode

## status: core_mvp_complete (2026-09-12)
all_tests: 264 passed, 0 failed
quickstart: 8/8 scenarios verified

## backend_app_map
| file | role |
|---|---|
| main.py | FastAPI app lifecycle, CORS, include_router(auth/requests/certs/admin/internal), seed_on_startup |
| agent.py | LangGraph pipeline: normalize->split->classify->triggers->check_duplicate->incident_check->execute_route |
| auth.py | session tokens, roles (student/operator/teacher), get_current_user, get_operator (401/403) |
| certs.py | catalog of 5 cert types, status chain (новая->в обработке->готова->выдана), order_certificate |
| db.py | engine (SQLite), sessionmaker, get_session dependency |
| events.py | audit trail: log_event(ticket_id, actor, action, payload), immutable log |
| incidents.py | wave detection (15m window, 3 distinct users), active incident, broadcast, simulate_wave |
| kb.py | RAG retrieve (top-3, cos_sim threshold 0.5), keyword fallback, article indexer |
| llm.py | Yandex AI Studio integration (chat, chat_structured, embed 256-dim, LLMUnavailable) |
| metrics.py | FR-062: auto_closed_pct, avg_first_reaction_sec, incidents, requests, escalations_open |
| models.py | SQLAlchemy 2.0 ORM models: User, Session, Request, Subtask, Ticket, Service, Check, Incident, Event, CertOrder, KbArticle |
| schemas.py | Pydantic v2 schemas: ClassifierResult, ToolOut, StatusBoardItem, MetricsOut, etc. |
| seed.py | idempotent startup seed: 5 users, 5 services, KB documents |
| tickets.py | ticket lifecycle (открыта->в работе->решена->закрыта), dedup, ticket_number SUP-2026-XXXX |
| tools.py | tool registry: check_site, check_lms, check_wifi, kb_agent, summarize, draft_kb_article, ADMIN_INVOKABLE |
| triggers.py | force-escalation triggers (complaints, legal), priority escalator, confidence threshold 0.6 |
| routers/ | auth.py, requests.py, certs.py, admin.py, internal.py |

## pipeline_flow
POST /api/requests -> agent.run_pipeline
  -> normalize (mask profanity/PII, detect lang, translate)
  -> split (break composite requests into subtasks)
  -> classify (service, category, priority, confidence)
  -> triggers (force escalation on complaint/legal or confidence<0.6)
  -> check_duplicate (dedup against open tickets in session)
  -> incident_check (fast outage_notice if mass incident active, skip checks/LLM)
  -> execute_route:
       auto_check -> HTTP/emulated check -> outage_notice or clarification
       kb -> kb_agent (reformulate, subqueries, retrieval, strict answer)
       cert_order -> node_cert_order -> order_certificate (auth_required if guest)
       escalate -> node_escalate -> summarize -> escalation_package -> duty notify

## admin_endpoints
- GET /api/admin/queue -> priority-sorted escalation list
- GET /api/admin/escalations/{id} -> full FR-060 escalation card
- POST /api/admin/escalations/{id}/close -> close ticket + optional kb_draft
- GET /api/admin/incidents -> incident list + POST broadcast + POST resolve
- GET /api/admin/certs/orders -> certificate orders + PATCH status
- GET /api/admin/status-board -> all services with last check
- PATCH /api/admin/services/{id} -> toggle emulated service (409 for real)
- GET /api/admin/tools -> tools registry with params schema
- POST /api/admin/tools/{name}/invoke -> run diagnostics / simulate_wave
- GET /api/admin/metrics -> 6 quality metrics (FR-062)
