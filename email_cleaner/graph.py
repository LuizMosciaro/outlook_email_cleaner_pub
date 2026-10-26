"""Cliente mínimo da Microsoft Graph para leitura e exclusão de mensagens."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from typing import Any

import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite"]
MAILBOX_SETTINGS_SCOPE = "https://graph.microsoft.com/MailboxSettings.Read"

log = logging.getLogger(__name__)


class GraphError(RuntimeError):
    """Falha em chamada Graph ou na autenticação."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MessageNotFound(GraphError):
    """Mensagem já removida/movida (por outra thread ou execução)."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise GraphError(f"Variável de ambiente {name} não definida.")
    return value


def acquire_token(scopes: list[str] | None = None) -> tuple[str, str | None]:
    """Troca o refresh token por um access token. Retorna (access, novo_refresh)."""
    client_id = _require_env("MS_CLIENT_ID")
    refresh_token = _require_env("MS_REFRESH_TOKEN")
    tenant = os.getenv("MS_TENANT", "consumers").strip() or "consumers"

    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant}"
    )
    result = app.acquire_token_by_refresh_token(refresh_token, scopes=scopes or SCOPES)
    if "access_token" not in result:
        raise GraphError(
            "Falha ao autenticar: "
            f"{result.get('error')} - {result.get('error_description')}"
        )
    return result["access_token"], result.get("refresh_token")


class MailClient:
    def __init__(self, access_token: str, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------ core
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        for attempt in range(5):
            response = self.session.request(method, url, timeout=45, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                wait = int(response.headers.get("Retry-After", 2**attempt))
                log.warning("HTTP %s, retentando em %ss", response.status_code, wait)
                time.sleep(min(wait, 30))
                continue
            if response.status_code == 404:
                raise MessageNotFound(
                    f"{method} {url} -> 404: mensagem não existe mais.", 404
                )
            if response.status_code == 401:
                raise GraphError(
                    "Token inválido ou expirado ao chamar a Graph API.", 401
                )
            if response.status_code >= 400:
                raise GraphError(
                    f"{method} {url} -> {response.status_code}: {response.text[:300]}",
                    response.status_code,
                )
            return response
        raise GraphError(f"{method} {url}: tentativas esgotadas.")

    def iter_messages(
        self,
        folder: str,
        select: list[str],
        top: int = 50,
        since: str | None = None,
        max_messages: int = 5000,
    ) -> Iterator[dict[str, Any]]:
        url = f"{GRAPH}/me/mailFolders/{folder}/messages"
        params: dict[str, str] | None = {
            "$select": ",".join(select),
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
        }
        if since:
            params["$filter"] = f"receivedDateTime ge {since}"

        seen = 0
        while url and seen < max_messages:
            data = self._request("GET", url, params=params).json()
            for item in data.get("value", []):
                yield item
                seen += 1
                if seen >= max_messages:
                    return
            url = data.get("@odata.nextLink")
            params = None

    # -------------------------------------------------------------- metadata
    def list_folders(self) -> list[dict[str, Any]]:
        url = f"{GRAPH}/me/mailFolders"
        params: dict[str, str] | None = {"$top": "100", "$select": "id,displayName,parentFolderId"}
        folders: list[dict[str, Any]] = []
        while url:
            data = self._request("GET", url, params=params).json()
            folders.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
        return folders

    def list_message_rules(self, folder: str = "inbox") -> list[dict[str, Any]]:
        response = self._request(
            "GET", f"{GRAPH}/me/mailFolders/{folder}/messageRules"
        ).json()
        return response.get("value", [])

    # --------------------------------------------------------------- actions
    def permanent_delete(self, message_id: str) -> str:
        """Exclusão definitiva. Fallback: move para Lixeira e apaga de lá."""
        try:
            self._request("POST", f"{GRAPH}/me/messages/{message_id}/permanentDelete")
            return "permanentDelete"
        except MessageNotFound:
            raise
        except GraphError as exc:
            log.debug("permanentDelete indisponível (%s); usando fallback.", exc)

        moved = self._request(
            "POST",
            f"{GRAPH}/me/messages/{message_id}/move",
            json={"destinationId": "deleteditems"},
        ).json()
        new_id = moved.get("id", message_id)
        self._request("DELETE", f"{GRAPH}/me/messages/{new_id}")
        return "move+delete"

    def soft_delete(self, message_id: str) -> str:
        self._request("DELETE", f"{GRAPH}/me/messages/{message_id}")
        return "softDelete"

    def move_to_junk(self, message_id: str) -> str:
        self._request(
            "POST",
            f"{GRAPH}/me/messages/{message_id}/move",
            json={"destinationId": "junkemail"},
        )
        return "moveToJunk"

    def mark_read(self, message_id: str) -> str:
        self._request("PATCH", f"{GRAPH}/me/messages/{message_id}", json={"isRead": True})
        return "markRead"

    def apply(self, action: str, message_id: str) -> str:
        handlers = {
            "delete_permanent": self.permanent_delete,
            "soft_delete": self.soft_delete,
            "move_to_junk": self.move_to_junk,
            "mark_read": self.mark_read,
        }
        return handlers[action](message_id)
