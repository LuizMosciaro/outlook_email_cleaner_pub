"""Motor de regras (normalização e casamento de padrões)."""

from __future__ import annotations

import re
from functools import lru_cache

import fnmatch
import html
import re
import unicodedata
from typing import Any

from .config import Rule

SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(content: str) -> str:
    """Remove tags/entidades para que palavras-chave não fiquem quebradas."""
    if not content:
        return ""
    text = SCRIPT_STYLE_RE.sub(" ", content)
    text = TAG_RE.sub(" ", text)
    return html.unescape(text)


def normalize(text: str) -> str:
    """Remove acentos, baixa caixa e colapsa espaços."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def sender_matches(pattern: str, address: str) -> bool:
    """Padrões aceitos: 'fulano@x.com', '@dominio.com', 'dominio.com' ou '*@*.top'."""
    pattern = (pattern or "").strip().casefold()
    address = (address or "").strip().casefold()
    if not pattern or not address:
        return False
    if pattern.startswith("@"):
        return address.endswith(pattern)
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatchcase(address, pattern)
    if "@" in pattern:
        return address == pattern
    return address.endswith("@" + pattern)


def build_index(message: dict[str, Any], normalize_headers: bool = True) -> dict[str, Any]:
    """Extrai os campos relevantes de uma mensagem Graph."""
    headers: dict[str, str] = {}
    for header in message.get("internetMessageHeaders") or []:
        name = (header.get("name") or "").casefold()
        value = header.get("value") or ""
        headers[name] = f"{headers[name]} {value}".strip() if name in headers else value
    if normalize_headers:
        headers = {key: normalize(value) for key, value in headers.items()}

    body = (message.get("body") or {}).get("content") or ""
    body = strip_html(body) or (message.get("bodyPreview") or "")

    addresses: list[str] = []
    # `from` pode ser forjado; o remetente real costuma vir em `sender`/`replyTo`.
    for holder in [message.get("from"), message.get("sender"), *(message.get("replyTo") or [])]:
        address = ((holder or {}).get("emailAddress") or {}).get("address") or ""
        address = address.strip().casefold()
        if address and address not in addresses:
            addresses.append(address)

    return {
        "id": message.get("id"),
        "sender": addresses[0] if addresses else "",
        "senders": addresses,
        "subject": normalize(message.get("subject") or ""),
        "body": normalize(body),
        "headers": headers,
    }


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def rule_matches(rule: Rule, index: dict[str, Any]) -> bool:
    checks: list[bool] = []

    if rule.senders:
        checks.append(
            any(
                sender_matches(pattern, address)
                for pattern in rule.senders
                for address in index["senders"]
            )
        )
    if rule.subject_contains:
        checks.append(
            any(normalize(k) in index["subject"] for k in rule.subject_contains if k)
        )
    if rule.body_contains:
        checks.append(any(normalize(k) in index["body"] for k in rule.body_contains if k))
    if rule.regex:
        haystack = f"{index['subject']} {index['body']}"
        checks.append(any(_compiled(p).search(haystack) for p in rule.regex))
    if rule.sender_longer_than:
        checks.append(
            any(len(addr) > rule.sender_longer_than for addr in index["senders"])
        )
    if rule.headers_contain:
        matched = True
        for name, needle in rule.headers_contain.items():
            value = index["headers"].get(name.casefold(), "")
            if normalize(needle) not in value:
                matched = False
                break
        checks.append(matched)

    if not checks:
        return False
    return all(checks) if rule.require_all else any(checks)


def is_allowlisted(patterns: list[str], address: str) -> bool:
    return any(sender_matches(p, address) for p in patterns)
