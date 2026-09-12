# STACK (MISIS Support Assistant) — densecode

## core_stack
backend: FastAPI 0.115+, Python 3.12, Uvicorn
orm_db: SQLAlchemy 2.0, SQLite (shorthack.db via Base.metadata.create_all + seed)
ai_orchestration: LangGraph 1.x (StateGraph)
llm_provider: Yandex AI Studio (YandexGPT + Yandex Embeddings, 256-dim) via httpx
frontend: Next.js 16 (App Router), React 19, TypeScript, Lucide Icons, pure CSS tokens
graph_db_optional: Neo4j 5.26 + Graphiti Core (lazy client: sqlite-only when no neo4j env)
testing: pytest 9+, pytest-cov, Starlette TestClient (264 tests passed)

## external_apis_and_timeouts
yandex_chat_timeout: 25.0s
yandex_embed_timeout: 10.0s
http_check_timeout: 5.0s (FR-022, FR-023: misis.ru, newlms.misis.ru)
bot_internal_auth: Authorization: Bearer {BOT_INTERNAL_TOKEN}

## environment_variables
YANDEX_API_KEY: api key for Yandex Cloud AI
YANDEX_FOLDER_ID: cloud folder id
YANDEX_MODEL: yandexgpt (default: yandexgpt/latest)
YANDEX_EMBED_MODEL: text-search-query / text-search-doc
BOT_INTERNAL_TOKEN: token for internal bot polling/ack
NEO4J_URI: optional bolt uri for Graphiti
NEO4J_USER: optional neo4j user
NEO4J_PASSWORD: optional neo4j password
OPENAI_API_KEY: optional openai key for Graphiti LLM extraction

## constraints
no_migrations: SQLite DDL via create_all on startup
auth_sessions: cookie session_id (db: sessions table, 30-day expiry)
max_tool_calls_per_request: 3 (FR-021)
max_reply_rounds: 2 (FR-015)
wave_window_minutes: 15 (FR-050)
wave_threshold_distinct_authors: 3 (FR-050)
