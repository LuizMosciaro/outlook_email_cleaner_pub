"""Fixtures e dublês de rede."""

from __future__ import annotations

import json
from typing import Any

import pytest


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = {"value": []} if payload is None else payload
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self) -> Any:
        return self._payload


class FakeSession:
    """Session que devolve respostas pré-programadas e registra as chamadas."""

    def __init__(self, responses: list[FakeResponse] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.default = FakeResponse()

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if self.responses:
            return self.responses.pop(0)
        return self.default


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


def make_message(
    message_id: str = "AAMk1",
    sender: str = "spam@bad.top",
    subject: str = "ganhe dinheiro agora",
    received: str = "2026-10-01T10:00:00Z",
    body: str | None = None,
    headers: list[dict[str, str]] | None = None,
    graph_sender: str | None = None,
    reply_to: list[str] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": message_id,
        "subject": subject,
        "from": {"emailAddress": {"address": sender, "name": sender}},
        "receivedDateTime": received,
        "bodyPreview": "preview",
        "isRead": False,
    }
    if graph_sender is not None:
        message["sender"] = {"emailAddress": {"address": graph_sender, "name": graph_sender}}
    if reply_to is not None:
        message["replyTo"] = [{"emailAddress": {"address": a, "name": a}} for a in reply_to]
    if body is not None:
        message["body"] = {"contentType": "html", "content": body}
    if headers is not None:
        message["internetMessageHeaders"] = headers
    return message
