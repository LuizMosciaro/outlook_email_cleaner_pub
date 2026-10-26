"""CLI do limpador de spam."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .graph import GraphError, MailClient, MessageNotFound, acquire_token
from .rules import build_index, is_allowlisted, rule_matches

log = logging.getLogger("email_cleaner")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="email-cleaner",
        description="Apaga permanentemente e-mails que casam com regras (Microsoft Graph).",
    )
    parser.add_argument("-c", "--config", default="config/rules.yaml")
    parser.add_argument(
        "--apply", action="store_true", help="executa as ações (sem isso, apenas simula)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="força simulação mesmo com --apply"
    )
    parser.add_argument("--folders", nargs="+", help="sobrescreve as pastas do YAML")
    parser.add_argument("--max-actions", type=int, help="limite de ações por execução")
    parser.add_argument("--max-scan-days", type=int, help="janela de varredura em dias")
    parser.add_argument("--max-scan-messages", type=int, default=5000)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="threads paralelas para executar as ações (1 = sequencial)",
    )
    parser.add_argument(
        "--log-file", help="caminho fixo do log (padrão: reports/cleaner_<n>.log.json)"
    )
    parser.add_argument(
        "--run-number",
        type=int,
        help="número da execução do log (padrão: próxima sequência em reports/)",
    )
    parser.add_argument(
        "--print-refresh-token",
        action="store_true",
        help="imprime o refresh token rotacionado (para atualizar o secret)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def build_select(settings: Settings) -> list[str]:
    fields = [
        "id",
        "subject",
        "from",
        "sender",
        "replyTo",
        "receivedDateTime",
        "bodyPreview",
        "isRead",
        "parentFolderId",
    ]
    if settings.needs_body():
        fields.append("body")
    if settings.needs_headers() or settings.has_action("delete_permanent"):
        fields.append("internetMessageHeaders")
    return fields


def evaluate(
    settings: Settings, message: dict[str, Any], now: datetime
) -> tuple[Any, dict[str, Any]] | None:
    index = build_index(message)
    if any(is_allowlisted(settings.allow_senders, addr) for addr in index["senders"]):
        return None

    for rule in settings.rules:
        if not rule_matches(rule, index):
            continue
        if rule.min_age_days:
            raw = message.get("receivedDateTime") or ""
            try:
                received = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if now - received < timedelta(days=rule.min_age_days):
                continue
        return rule, index
    return None


LOG_NAME_RE = re.compile(r"^cleaner_(\d+)\.log\.json$")


def next_run_number(directory: Path) -> int:
    """Próximo número livre com base nos logs já existentes."""
    numbers = [
        int(match.group(1))
        for path in directory.glob("cleaner_*.log.json")
        if (match := LOG_NAME_RE.match(path.name))
    ]
    return max(numbers, default=0) + 1


def resolve_log_file(configured: str, run_number: int | None = None) -> Path:
    """Resolve o log em reports/cleaner_<run_number>.log.json quando não fixado."""
    if configured and configured.strip().casefold() not in {"auto", "default"}:
        path = Path(configured)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    directory = Path("reports")
    directory.mkdir(parents=True, exist_ok=True)
    number = (
        run_number
        or int(os.getenv("RUN_NUMBER", "0") or 0)
        or next_run_number(directory)
    )
    return directory / f"cleaner_{number}.log.json"


def write_log(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def client_factory(token: str) -> Callable[[], MailClient]:
    """Uma sessão HTTP por thread (requests.Session não é thread-safe)."""
    local = threading.local()

    def get_client() -> MailClient:
        client = getattr(local, "client", None)
        if client is None:
            client = MailClient(token)
            local.client = client
        return client

    return get_client


Pending = tuple[str, str, dict[str, Any], str]


def process_item(client: MailClient, applied: bool, item: Pending) -> dict[str, Any]:
    """Executa (ou apenas descreve) uma ação, isolando erros por mensagem."""
    folder, message_id, index, action = item
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "folder": folder,
        "sender": index["sender"],
        "subject": index["subject"][:160],
        "action": action,
        "applied": applied,
        "id": message_id,
    }
    if not applied:
        return entry
    try:
        entry["result"] = client.apply(action, message_id)
    except MessageNotFound as exc:
        # Corrida: outra thread/execução já apagou ou moveu a mensagem.
        entry["result"] = "already_gone"
        entry["note"] = str(exc)
    except GraphError as exc:
        entry["error"] = str(exc)
    return entry


def run_actions(
    get_client: Callable[[], MailClient],
    applied: bool,
    pending: list[Pending],
    workers: int = 1,
) -> list[dict[str, Any]]:
    """Processa a fila; `workers > 1` usa multithreading mantendo a ordem."""
    if workers > 1 and len(pending) > 1:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(pending)), thread_name_prefix="cleaner"
        ) as pool:
            return list(pool.map(lambda item: process_item(get_client(), applied, item), pending))
    return [process_item(get_client(), applied, item) for item in pending]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_dotenv()

    try:
        settings = Settings.load(args.config)
    except ConfigError as exc:
        log.error("Configuração inválida: %s", exc)
        return 2

    if args.folders:
        settings.folders = args.folders
    if args.max_actions:
        settings.max_actions_per_run = args.max_actions
    if args.max_scan_days:
        settings.max_scan_days = args.max_scan_days

    log_path = resolve_log_file(args.log_file or settings.log_file, args.run_number)

    # A decisão vem só da CLI: --apply aplica, --dry-run (ou ausência de --apply) simula.
    applied = args.apply and not args.dry_run
    settings.dry_run = not applied
    mode = "APPLY" if applied else "DRY-RUN"
    log.info("Modo %s | pastas=%s | regras=%d", mode, settings.folders, len(settings.rules))
    log.info("Auditoria: %s", log_path)
    if not applied:
        log.info("Nada será alterado; use --apply para executar de verdade.")

    try:
        token, rotated = acquire_token()
        if rotated and args.print_refresh_token:
            print(rotated)
    except GraphError as exc:
        log.error("%s", exc)
        return 1

    get_client = client_factory(token)
    client = get_client()
    select = build_select(settings)
    since = (
        datetime.now(timezone.utc) - timedelta(days=settings.max_scan_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "acted": 0,
        "errors": 0,
        "already_gone": 0,
        "by_rule": {},
        "by_action": {},
    }
    pending: list[Pending] = []

    try:
        for folder in settings.folders:
            log.info("Varrendo pasta '%s' desde %s", folder, since)
            for message in client.iter_messages(
                folder,
                select=select,
                top=settings.page_size,
                since=since,
                max_messages=args.max_scan_messages,
            ):
                stats["scanned"] += 1
                result = evaluate(settings, message, datetime.now(timezone.utc))
                if not result:
                    continue
                rule, index = result
                stats["matched"] += 1
                stats["by_rule"][rule.name] = stats["by_rule"].get(rule.name, 0) + 1
                if len(pending) >= settings.max_actions_per_run:
                    continue
                pending.append((folder, index["id"], index, rule.action))
    except GraphError as exc:
        log.error("Erro ao varrer mensagens: %s", exc)
        return 1

    skipped = stats["matched"] - len(pending)
    log.info("Encontrados %d candidatos", len(pending))
    if skipped > 0:
        log.warning(
            "%d mensagens ficaram para a próxima execução (max_actions_per_run=%d).",
            skipped,
            settings.max_actions_per_run,
        )

    if applied and pending:
        log.info(
            "Executando %d ação(ões) com %d thread(s).", len(pending), max(1, args.workers)
        )

    entries = run_actions(get_client, applied, pending, args.workers)

    for entry in entries:
        if "error" in entry:
            stats["errors"] += 1
            log.warning("Falha em %s (%s): %s", entry["sender"], entry["action"], entry["error"])
        elif entry.get("result") == "already_gone":
            stats["already_gone"] += 1
            log.info("Ignorado (já removido): %s | %s", entry["sender"], entry["subject"][:80])
        elif applied:
            stats["acted"] += 1
            action = entry["action"]
            stats["by_action"][action] = stats["by_action"].get(action, 0) + 1
        write_log(log_path, entry)
        if not args.verbose:
            log.info(
                "[%s] %s | %s | %s",
                "ERRO" if "error" in entry else ("SIM" if not applied else "OK"),
                entry["action"],
                entry["sender"] or "?",
                entry["subject"][:80],
            )

    log.info("Resumo: %s", json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
