"""Клиент Graphiti (Zep) — темпоральный knowledge graph на Neo4j.

Ленивая инициализация: без заданных NEO4J_URI/NEO4J_PASSWORD и запущенного
Neo4j приложение работает в обычном режиме (SQLite), граф просто не подключается.
"""

import os

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

_client: Graphiti | None = None


def graphiti_configured() -> bool:
    return bool(os.getenv("NEO4J_URI") and os.getenv("NEO4J_PASSWORD"))


async def get_graphiti() -> Graphiti | None:
    """Вернуть общий клиент Graphiti, создав его при первом вызове.

    Возвращает None, если Neo4j не сконфигурирован — вызывающий код
    обязан обрабатывать этот случай.
    """
    global _client
    if _client is not None:
        return _client
    if not graphiti_configured():
        return None
    _client = Graphiti(
        uri=os.environ["NEO4J_URI"],
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
    )
    await _client.build_indices_and_constraints()
    return _client


async def add_episode(name: str, body: str, source_description: str = "shorthack") -> None:
    """Добавить эпизод в граф знаний (например, событие клика или новую ссылку)."""
    client = await get_graphiti()
    if client is None:
        return
    await client.add_episode(
        name=name,
        episode_body=body,
        source=EpisodeType.message,
        source_description=source_description,
    )


async def close_graphiti() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
