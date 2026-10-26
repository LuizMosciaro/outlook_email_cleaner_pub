"""Testes do cliente Graph e da autenticação."""

from __future__ import annotations

from typing import Any

import pytest

from email_cleaner import graph
from email_cleaner.graph import (
    GRAPH,
    SCOPES,
    GraphError,
    MailClient,
    MessageNotFound,
    _require_env,
    acquire_token,
)
from tests.conftest import FakeResponse, FakeSession


class FakeMsalApp:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.init_args: tuple[str, str] | None = None
        self.token_calls: list[tuple[str, list[str] | None]] = []

    def acquire_token_by_refresh_token(
        self, refresh_token: str, scopes: list[str] | None = None
    ) -> dict[str, Any]:
        self.token_calls.append((refresh_token, scopes))
        return self.result


@pytest.fixture
def msal_app(monkeypatch: pytest.MonkeyPatch) -> FakeMsalApp:
    app = FakeMsalApp({"access_token": "abc", "refresh_token": "novo"})
    created: list[FakeMsalApp] = []

    def factory(client_id: str, authority: str | None = None) -> FakeMsalApp:
        app.init_args = (client_id, authority or "")
        created.append(app)
        return app

    monkeypatch.setattr(graph.msal, "PublicClientApplication", factory)
    monkeypatch.setattr(graph, "_CREATED", created, raising=False)
    return app


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MS_CLIENT_ID", "client-123")
    monkeypatch.setenv("MS_REFRESH_TOKEN", "refresh-123")
    monkeypatch.delenv("MS_TENANT", raising=False)


class TestRequireEnv:
    def test_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALGUMA", raising=False)
        with pytest.raises(GraphError, match="ALGUMA"):
            _require_env("ALGUMA")

    def test_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALGUMA", "   ")
        with pytest.raises(GraphError):
            _require_env("ALGUMA")

    def test_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALGUMA", " valor ")
        assert _require_env("ALGUMA") == "valor"


class TestAcquireToken:
    def test_missing_client_id(self, monkeypatch: pytest.MonkeyPatch, env: None) -> None:
        monkeypatch.delenv("MS_CLIENT_ID")
        with pytest.raises(GraphError, match="MS_CLIENT_ID"):
            acquire_token()

    def test_missing_refresh_token(self, monkeypatch: pytest.MonkeyPatch, env: None) -> None:
        monkeypatch.delenv("MS_REFRESH_TOKEN")
        with pytest.raises(GraphError, match="MS_REFRESH_TOKEN"):
            acquire_token()

    def test_success_default_tenant(self, env: None, msal_app: FakeMsalApp) -> None:
        access, rotated = acquire_token()
        assert (access, rotated) == ("abc", "novo")
        assert msal_app.init_args == ("client-123", "https://login.microsoftonline.com/consumers")
        assert msal_app.token_calls == [("refresh-123", SCOPES)]

    def test_success_custom_tenant_and_scopes(
        self, monkeypatch: pytest.MonkeyPatch, env: None, msal_app: FakeMsalApp
    ) -> None:
        monkeypatch.setenv("MS_TENANT", "contoso.onmicrosoft.com")
        acquire_token(scopes=["Custom.Scope"])
        assert msal_app.init_args == (
            "client-123",
            "https://login.microsoftonline.com/contoso.onmicrosoft.com",
        )
        assert msal_app.token_calls == [("refresh-123", ["Custom.Scope"])]

    def test_blank_tenant_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, env: None, msal_app: FakeMsalApp
    ) -> None:
        monkeypatch.setenv("MS_TENANT", "  ")
        acquire_token()
        assert msal_app.init_args == ("client-123", "https://login.microsoftonline.com/consumers")

    def test_failure(self, env: None, monkeypatch: pytest.MonkeyPatch) -> None:
        app = FakeMsalApp({"error": "invalid_grant", "error_description": "expirado"})
        monkeypatch.setattr(graph.msal, "PublicClientApplication", lambda *a, **k: app)
        with pytest.raises(GraphError, match="invalid_grant - expirado"):
            acquire_token()


class TestClientInit:
    def test_sets_headers(self, fake_session: FakeSession) -> None:
        MailClient("token-x", session=fake_session)
        assert fake_session.headers["Authorization"] == "Bearer token-x"

    def test_creates_session(self) -> None:
        client = MailClient("token-y")
        assert client.session.headers["Authorization"] == "Bearer token-y"


class TestRequest:
    def test_success(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {"ok": True})
        client = MailClient("t", session=fake_session)
        assert client._request("GET", "http://x").json() == {"ok": True}
        assert fake_session.calls[0]["kwargs"]["timeout"] == 45

    def test_unauthorized(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(401)
        client = MailClient("t", session=fake_session)
        with pytest.raises(GraphError, match="Token inválido"):
            client._request("GET", "http://x")

    def test_bad_request(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(400, {"erro": "ruim"})
        client = MailClient("t", session=fake_session)
        with pytest.raises(GraphError, match="400"):
            client._request("GET", "http://x")

    def test_retries_then_succeeds(
        self, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph.time, "sleep", lambda _s: None)
        fake_session.responses = [
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(503),
            FakeResponse(200, {"ok": 1}),
        ]
        client = MailClient("t", session=fake_session)
        assert client._request("GET", "http://x").json() == {"ok": 1}
        assert len(fake_session.calls) == 3

    def test_exhausted(self, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph.time, "sleep", lambda _s: None)
        fake_session.responses = [FakeResponse(500) for _ in range(5)]
        client = MailClient("t", session=fake_session)
        with pytest.raises(GraphError, match="tentativas esgotadas"):
            client._request("GET", "http://x")
        assert len(fake_session.calls) == 5


class TestIterMessages:
    def test_pagination_and_filter(self, fake_session: FakeSession) -> None:
        fake_session.responses = [
            FakeResponse(200, {"value": [{"id": "1"}], "@odata.nextLink": "http://next"}),
            FakeResponse(200, {"value": [{"id": "2"}]}),
        ]
        client = MailClient("t", session=fake_session)
        ids = [
            m["id"]
            for m in client.iter_messages(
                "inbox", select=["id"], top=10, since="2026-01-01T00:00:00Z"
            )
        ]
        assert ids == ["1", "2"]
        first = fake_session.calls[0]["kwargs"]["params"]
        assert first["$filter"] == "receivedDateTime ge 2026-01-01T00:00:00Z"
        assert first["$top"] == "10"
        assert fake_session.calls[1]["kwargs"]["params"] is None
        assert fake_session.calls[0]["url"] == f"{GRAPH}/me/mailFolders/inbox/messages"

    def test_without_since(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {"value": [{"id": "1"}]})
        client = MailClient("t", session=fake_session)
        list(client.iter_messages("inbox", select=["id"]))
        assert "$filter" not in fake_session.calls[0]["kwargs"]["params"]

    def test_max_messages_mid_page(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(
            200, {"value": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        )
        client = MailClient("t", session=fake_session)
        ids = [m["id"] for m in client.iter_messages("inbox", ["id"], max_messages=2)]
        assert ids == ["1", "2"]

    def test_max_messages_on_later_page(self, fake_session: FakeSession) -> None:
        fake_session.responses = [
            FakeResponse(200, {"value": [{"id": "1"}], "@odata.nextLink": "http://next"}),
            FakeResponse(200, {"value": [{"id": "2"}, {"id": "3"}, {"id": "4"}]}),
        ]
        client = MailClient("t", session=fake_session)
        ids = [m["id"] for m in client.iter_messages("inbox", ["id"], max_messages=3)]
        assert ids == ["1", "2", "3"]

    def test_empty_folder(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {"value": []})
        client = MailClient("t", session=fake_session)
        assert list(client.iter_messages("inbox", ["id"])) == []


class TestMetadata:
    def test_list_folders_paginates(self, fake_session: FakeSession) -> None:
        fake_session.responses = [
            FakeResponse(200, {"value": [{"id": "1"}], "@odata.nextLink": "http://next"}),
            FakeResponse(200, {"value": [{"id": "2"}]}),
        ]
        client = MailClient("t", session=fake_session)
        assert client.list_folders() == [{"id": "1"}, {"id": "2"}]
        assert fake_session.calls[1]["kwargs"]["params"] is None

    def test_list_message_rules(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {"value": [{"id": "r1"}]})
        client = MailClient("t", session=fake_session)
        assert client.list_message_rules("junkemail") == [{"id": "r1"}]
        assert fake_session.calls[0]["url"].endswith("/mailFolders/junkemail/messageRules")

    def test_list_message_rules_empty(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {})
        client = MailClient("t", session=fake_session)
        assert client.list_message_rules() == []


class TestActions:
    def test_permanent_delete_direct(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(204, {})
        client = MailClient("t", session=fake_session)
        assert client.permanent_delete("AAMk1") == "permanentDelete"
        assert fake_session.calls[0]["method"] == "POST"
        assert fake_session.calls[0]["url"].endswith("/me/messages/AAMk1/permanentDelete")

    def test_permanent_delete_fallback(self, fake_session: FakeSession) -> None:
        fake_session.responses = [
            FakeResponse(405, {"erro": "nao suportado"}),
            FakeResponse(201, {"id": "novo-id"}),
            FakeResponse(204, {}),
        ]
        client = MailClient("t", session=fake_session)
        assert client.permanent_delete("AAMk1") == "move+delete"
        assert fake_session.calls[1]["kwargs"]["json"] == {"destinationId": "deleteditems"}
        assert fake_session.calls[2]["url"].endswith("/me/messages/novo-id")

    def test_permanent_delete_fallback_without_new_id(self, fake_session: FakeSession) -> None:
        fake_session.responses = [
            FakeResponse(405, {}),
            FakeResponse(201, {}),
            FakeResponse(204, {}),
        ]
        client = MailClient("t", session=fake_session)
        assert client.permanent_delete("AAMk1") == "move+delete"
        assert fake_session.calls[2]["url"].endswith("/me/messages/AAMk1")

    def test_soft_delete(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(204, {})
        client = MailClient("t", session=fake_session)
        assert client.soft_delete("AAMk1") == "softDelete"
        assert fake_session.calls[0]["method"] == "DELETE"

    def test_move_to_junk(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(201, {})
        client = MailClient("t", session=fake_session)
        assert client.move_to_junk("AAMk1") == "moveToJunk"
        assert fake_session.calls[0]["kwargs"]["json"] == {"destinationId": "junkemail"}

    def test_mark_read(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(200, {})
        client = MailClient("t", session=fake_session)
        assert client.mark_read("AAMk1") == "markRead"
        assert fake_session.calls[0]["kwargs"]["json"] == {"isRead": True}

    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            ("delete_permanent", "permanentDelete"),
            ("soft_delete", "softDelete"),
            ("move_to_junk", "moveToJunk"),
            ("mark_read", "markRead"),
        ],
    )
    def test_apply_dispatch(
        self, fake_session: FakeSession, action: str, expected: str
    ) -> None:
        fake_session.default = FakeResponse(200, {})
        client = MailClient("t", session=fake_session)
        assert client.apply(action, "AAMk1") == expected

    def test_apply_unknown_action(self, fake_session: FakeSession) -> None:
        client = MailClient("t", session=fake_session)
        with pytest.raises(KeyError):
            client.apply("explodir", "AAMk1")


class TestMessageNotFound:
    def test_404_raises_message_not_found(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(404, {"error": "not found"})
        client = MailClient("t", session=fake_session)
        with pytest.raises(MessageNotFound) as exc:
            client.soft_delete("AAMk1")
        assert exc.value.status == 404

    def test_permanent_delete_does_not_fallback(
        self, fake_session: FakeSession
    ) -> None:
        fake_session.default = FakeResponse(404, {})
        client = MailClient("t", session=fake_session)
        with pytest.raises(MessageNotFound):
            client.permanent_delete("AAMk1")
        assert len(fake_session.calls) == 1

    def test_generic_error_keeps_status(self, fake_session: FakeSession) -> None:
        fake_session.default = FakeResponse(400, {})
        client = MailClient("t", session=fake_session)
        with pytest.raises(GraphError) as exc:
            client.move_to_junk("AAMk1")
        assert exc.value.status == 400

    def test_error_without_status(self) -> None:
        assert GraphError("falha").status is None
