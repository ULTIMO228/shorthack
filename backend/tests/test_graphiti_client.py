"""Тесты ленивого клиента Graphiti: без Neo4j-конфига — тихий no-op (AGENTS.md)."""

from __future__ import annotations

import asyncio

from app import graphiti_client


def test_not_configured_without_env(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert graphiti_client.graphiti_configured() is False


def test_configured_with_env(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    assert graphiti_client.graphiti_configured() is True


def test_get_graphiti_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert asyncio.run(graphiti_client.get_graphiti()) is None


def test_add_episode_noop_without_client(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    asyncio.run(graphiti_client.add_episode("тест", "тело"))  # не падает, ничего не пишет


def test_close_without_client_ok():
    graphiti_client._client = None
    asyncio.run(graphiti_client.close_graphiti())  # не падает
