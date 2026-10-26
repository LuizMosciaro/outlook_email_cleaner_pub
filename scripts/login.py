"""Gera o refresh token via device code flow (rodar uma vez, localmente).

Uso: python scripts/login.py
Requer MS_CLIENT_ID no .env (app registration com 'Allow public client flows' e
permissão delegada Mail.ReadWrite).
"""

from __future__ import annotations

import os
import sys

import msal
from dotenv import load_dotenv

# MSAL adiciona openid/profile/offline_access automaticamente (passar = ValueError).
SCOPES = [
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/MailboxSettings.Read",
]


def main() -> int:
    load_dotenv()
    client_id = os.getenv("MS_CLIENT_ID", "").strip()
    if not client_id:
        print("Defina MS_CLIENT_ID no .env", file=sys.stderr)
        return 2
    tenant = os.getenv("MS_TENANT", "consumers").strip() or "consumers"

    app = msal.PublicClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant}"
    )
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"Falha ao iniciar device flow: {flow}", file=sys.stderr)
        return 1

    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "refresh_token" not in result:
        print(f"Falha: {result.get('error')} - {result.get('error_description')}", file=sys.stderr)
        return 1

    token = result["refresh_token"]
    print("\nMS_REFRESH_TOKEN=" + token)
    print(f"\nConta: {result.get('id_token_claims', {}).get('preferred_username', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
