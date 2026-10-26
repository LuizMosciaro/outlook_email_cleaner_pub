"""Carregamento e validação da configuração YAML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_ACTIONS = {
    "delete_permanent",  # exclui definitivamente (bypassa Lixeira)
    "soft_delete",       # move para a Lixeira
    "move_to_junk",      # move para Lixo Eletrônico
    "mark_read",         # apenas marca como lido
}

RULE_FIELDS = {
    "name",
    "action",
    "senders",
    "subject_contains",
    "body_contains",
    "headers_contain",
    "regex",
    "sender_longer_than",
    "require_all",
    "min_age_days",
}


class ConfigError(ValueError):
    """Configuração inválida."""


@dataclass(slots=True)
class Rule:
    name: str
    action: str = "delete_permanent"
    senders: list[str] = field(default_factory=list)
    subject_contains: list[str] = field(default_factory=list)
    body_contains: list[str] = field(default_factory=list)
    headers_contain: dict[str, str] = field(default_factory=dict)
    regex: list[str] = field(default_factory=list)
    # casa quando algum endereço do remetente (from/sender/replyTo) for maior que N
    sender_longer_than: int = 0
    require_all: bool = False
    min_age_days: int = 0

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ConfigError(
                f"Regra '{self.name}': ação inválida '{self.action}'. "
                f"Use uma de: {sorted(VALID_ACTIONS)}"
            )
        if not (
            self.senders
            or self.subject_contains
            or self.body_contains
            or self.headers_contain
            or self.regex
            or self.sender_longer_than
        ):
            raise ConfigError(f"Regra '{self.name}' não possui nenhum critério.")
        if self.sender_longer_than < 0:
            raise ConfigError(
                f"Regra '{self.name}': sender_longer_than não pode ser negativo."
            )
        for pattern in self.regex:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(
                    f"Regra '{self.name}': regex inválido '{pattern}' ({exc})"
                ) from exc
        if self.min_age_days < 0:
            raise ConfigError(f"Regra '{self.name}': min_age_days não pode ser negativo.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        unknown = set(data) - RULE_FIELDS
        if unknown:
            raise ConfigError(
                f"Regra '{data.get('name', '?')}': campos desconhecidos {sorted(unknown)}"
            )
        if not data.get("name"):
            raise ConfigError("Toda regra precisa de um campo 'name'.")
        return cls(**data)


@dataclass(slots=True)
class Settings:
    folders: list[str] = field(default_factory=lambda: ["inbox"])
    allow_senders: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    max_actions_per_run: int = 200
    max_scan_days: int = 30
    page_size: int = 50
    # Vazio ou "auto" => reports/cleaner_<run_number>.log.json (numeração automática)
    log_file: str = ""
    dry_run: bool = True

    def needs_body(self) -> bool:
        return any(rule.body_contains or rule.regex for rule in self.rules)

    def needs_headers(self) -> bool:
        return any(rule.headers_contain for rule in self.rules)

    def has_action(self, action: str) -> bool:
        return any(rule.action == action for rule in self.rules)

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"Arquivo de configuração não encontrado: {file}")
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError("A configuração deve ser um mapeamento YAML.")

        raw_rules = raw.pop("rules", None) or []
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ConfigError("A configuração precisa de uma lista 'rules' não vazia.")

        allowed = {f for f in cls.__dataclass_fields__ if f != "rules"}
        unknown = set(raw) - allowed
        if unknown:
            raise ConfigError(f"Chaves desconhecidas na configuração: {sorted(unknown)}")

        settings = cls(**raw, rules=[Rule.from_dict(item) for item in raw_rules])
        if not settings.folders:
            raise ConfigError("Informe ao menos uma pasta em 'folders'.")
        return settings
