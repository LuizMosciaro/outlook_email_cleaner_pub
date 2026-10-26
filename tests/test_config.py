from __future__ import annotations

from pathlib import Path

import pytest

from email_cleaner.config import ConfigError, Rule, Settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "rules.example.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_example_file() -> None:
    settings = Settings.load(EXAMPLE)
    assert settings.folders == ["inbox"]
    assert len(settings.rules) == 1
    assert settings.max_actions_per_run == 50


def test_needs_body_and_headers() -> None:
    settings = Settings.load(EXAMPLE)
    assert settings.needs_body() is False
    assert settings.needs_headers() is False
    assert settings.has_action("move_to_junk") is True
    assert settings.has_action("soft_delete") is False


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="não encontrado"):
        Settings.load(tmp_path / "nada.yaml")


def test_empty_rules(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'rules'"):
        Settings.load(write(tmp_path, "folders: [inbox]\nrules: []\n"))


def test_rules_not_a_list(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'rules'"):
        Settings.load(write(tmp_path, "folders: [inbox]\nrules: spam\n"))


def test_unknown_top_level_key(tmp_path: Path) -> None:
    text = "folders: [inbox]\nrules:\n  - name: x\n    senders: ['@a.com']\nfoo: 1\n"
    with pytest.raises(ConfigError, match="desconhecidas"):
        Settings.load(write(tmp_path, text))


def test_empty_folders(tmp_path: Path) -> None:
    text = "folders: []\nrules:\n  - name: x\n    senders: ['@a.com']\n"
    with pytest.raises(ConfigError, match="pasta"):
        Settings.load(write(tmp_path, text))


def test_rule_invalid_action() -> None:
    with pytest.raises(ConfigError, match="ação inválida"):
        Rule(name="x", action="explodir", senders=["@a.com"])


def test_rule_without_criteria() -> None:
    with pytest.raises(ConfigError, match="nenhum critério"):
        Rule(name="x")


def test_rule_regex_is_a_valid_criterion() -> None:
    rule = Rule(name="x", regex=[r"[\u0400-\u04FF]"])
    assert rule.regex == [r"[\u0400-\u04FF]"]


def test_rule_invalid_regex() -> None:
    with pytest.raises(ConfigError, match="regex inválido"):
        Rule(name="x", regex=["[a-"])


def test_rule_sender_longer_than_is_a_criterion() -> None:
    rule = Rule(name="x", sender_longer_than=40)
    assert rule.sender_longer_than == 40


def test_rule_sender_longer_than_negative() -> None:
    with pytest.raises(ConfigError, match="sender_longer_than"):
        Rule(name="x", sender_longer_than=-1)


def test_regex_rule_needs_body() -> None:
    settings = Settings(rules=[Rule(name="x", regex=["abc"])])
    assert settings.needs_body() is True


def test_rule_negative_min_age() -> None:
    with pytest.raises(ConfigError, match="min_age_days"):
        Rule(name="x", senders=["@a.com"], min_age_days=-1)


def test_rule_unknown_field() -> None:
    with pytest.raises(ConfigError, match="desconhecidos"):
        Rule.from_dict({"name": "x", "sender": ["@a.com"]})


def test_rule_missing_name() -> None:
    with pytest.raises(ConfigError, match="name"):
        Rule.from_dict({"senders": ["@a.com"]})


def test_settings_rejects_unknown_rule_key(tmp_path: Path) -> None:
    text = "rules:\n  - name: x\n    senders: ['@a.com']\n    body: spam\n"
    with pytest.raises(ConfigError, match="desconhecidos"):
        Settings.load(write(tmp_path, text))


def test_empty_yaml_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'rules'"):
        Settings.load(write(tmp_path, "---\n"))


def test_yaml_must_be_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mapeamento"):
        Settings.load(write(tmp_path, "- apenas\n- uma lista\n"))
