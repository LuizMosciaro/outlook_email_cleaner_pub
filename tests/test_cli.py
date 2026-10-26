"""Testes da CLI."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from email_cleaner import cli
from email_cleaner.config import Rule, Settings
from email_cleaner.graph import GraphError, MessageNotFound
from tests.conftest import make_message


class StubClient:
    """Substitui MailClient nos testes de fluxo."""

    messages: list[dict[str, Any]] = []
    fail_scan: Exception | None = None
    fail_ids: set[str] = set()
    missing_ids: set[str] = set()
    instances: list["StubClient"] = []
    all_actions: list[tuple[str, str]] = []
    lock = threading.Lock()

    def __init__(self, token: str) -> None:
        self.token = token
        self.scan_calls: list[dict[str, Any]] = []
        self.actions: list[tuple[str, str]] = []
        StubClient.instances.append(self)

    def iter_messages(self, folder: str, **kwargs: Any):
        self.scan_calls.append({"folder": folder, **kwargs})
        if StubClient.fail_scan is not None:
            raise StubClient.fail_scan
        yield from StubClient.messages

    def apply(self, action: str, message_id: str) -> str:
        if message_id in StubClient.missing_ids:
            raise MessageNotFound(f"{message_id} já foi apagado", 404)
        if message_id in StubClient.fail_ids:
            raise GraphError("falha simulada")
        with StubClient.lock:
            self.actions.append((action, message_id))
            StubClient.all_actions.append((action, message_id))
        return f"ok:{action}"


@pytest.fixture(autouse=True)
def reset_stub() -> None:
    StubClient.messages = []
    StubClient.fail_scan = None
    StubClient.fail_ids = set()
    StubClient.missing_ids = set()
    StubClient.instances = []
    StubClient.all_actions = []


CONFIG_BASIC = """
folders: [inbox]
rules:
  - name: spam-bad-top
    action: delete_permanent
    senders: ["@bad.top"]
"""


@pytest.fixture
def run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    def _run(
        config_text: str = CONFIG_BASIC,
        argv: list[str] | None = None,
        auth: Any = ("tok", None),
        fail_auth: bool = False,
    ) -> int:
        config = tmp_path / "rules.yaml"
        config.write_text(config_text, encoding="utf-8")
        if fail_auth:
            def boom() -> None:
                raise GraphError("sem token")

            monkeypatch.setattr(cli, "acquire_token", boom)
        else:
            monkeypatch.setattr(cli, "acquire_token", lambda: auth)
        monkeypatch.setattr(cli, "MailClient", StubClient)
        return cli.main(["--config", str(config), *(argv or [])])

    return _run


class TestParseArgs:
    def test_defaults(self) -> None:
        args = cli.parse_args([])
        assert args.config == "config/rules.yaml"
        assert args.apply is False
        assert args.dry_run is False
        assert args.max_scan_messages == 5000
        assert args.workers == 1
        assert args.verbose is False

    def test_flags(self) -> None:
        args = cli.parse_args(
            [
                "-c",
                "x.yaml",
                "--apply",
                "--dry-run",
                "--folders",
                "inbox",
                "junkemail",
                "--max-actions",
                "3",
                "--max-scan-days",
                "7",
                "--max-scan-messages",
                "9",
                "--workers",
                "4",
                "--log-file",
                "l.json",
                "--run-number",
                "4",
                "--print-refresh-token",
                "-v",
            ]
        )
        assert args.folders == ["inbox", "junkemail"]
        assert args.max_actions == 3
        assert args.max_scan_days == 7
        assert args.max_scan_messages == 9
        assert args.workers == 4
        assert args.log_file == "l.json"
        assert args.run_number == 4
        assert args.print_refresh_token is True
        assert args.verbose is True


class TestBuildSelect:
    def test_minimal(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@x.com"], action="mark_read")])
        select = cli.build_select(settings)
        assert "body" not in select
        assert "internetMessageHeaders" not in select
        assert "sender" in select and "replyTo" in select

    def test_body_rule(self) -> None:
        settings = Settings(rules=[Rule(name="r", body_contains=["spam"], action="mark_read")])
        assert "body" in cli.build_select(settings)

    def test_regex_rule_needs_body(self) -> None:
        settings = Settings(rules=[Rule(name="r", regex=["[a-z]"])])
        assert "body" in cli.build_select(settings)

    def test_headers_rule(self) -> None:
        settings = Settings(
            rules=[Rule(name="r", headers_contain={"X-Test": "1"}, action="mark_read")]
        )
        assert "internetMessageHeaders" in cli.build_select(settings)

    def test_permanent_delete_needs_headers(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@x.com"])])
        assert "internetMessageHeaders" in cli.build_select(settings)


class TestEvaluate:
    NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)

    def test_allowlisted(self) -> None:
        settings = Settings(
            allow_senders=["@good.com"],
            rules=[Rule(name="r", senders=["@good.com"])],
        )
        message = make_message(sender="a@good.com", subject="qualquer")
        assert cli.evaluate(settings, message, self.NOW) is None

    def test_match(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@bad.top"])])
        result = cli.evaluate(settings, make_message(), self.NOW)
        assert result is not None
        rule, index = result
        assert rule.name == "r"
        assert index["id"] == "AAMk1"

    def test_no_match(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@outro.com"])])
        assert cli.evaluate(settings, make_message(), self.NOW) is None

    def test_match_on_real_sender_when_from_differs(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["noreply@spam.example"])])
        message = make_message(sender="spoof@example.invalid", graph_sender="noreply@spam.example")
        result = cli.evaluate(settings, message, self.NOW)
        assert result is not None and result[0].name == "r"

    def test_allowlisted_by_real_sender(self) -> None:
        settings = Settings(
            allow_senders=["@spam.example"],
            rules=[Rule(name="r", senders=["@spam.example"])],
        )
        message = make_message(sender="spoof@example.invalid", graph_sender="noreply@spam.example")
        assert cli.evaluate(settings, message, self.NOW) is None

    def test_min_age_too_recent(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@bad.top"], min_age_days=5)])
        message = make_message(received="2026-10-20T10:00:00Z")
        assert cli.evaluate(settings, message, self.NOW) is None

    def test_min_age_ok(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@bad.top"], min_age_days=5)])
        message = make_message(received="2026-10-01T10:00:00Z")
        assert cli.evaluate(settings, message, self.NOW) is not None

    def test_min_age_invalid_date(self) -> None:
        settings = Settings(rules=[Rule(name="r", senders=["@bad.top"], min_age_days=5)])
        message = make_message(received="data-invalida")
        assert cli.evaluate(settings, message, self.NOW) is None

    def test_second_rule_matches(self) -> None:
        settings = Settings(
            rules=[
                Rule(name="a", senders=["@nada.com"]),
                Rule(name="b", subject_contains=["dinheiro"]),
            ]
        )
        result = cli.evaluate(settings, make_message(), self.NOW)
        assert result is not None and result[0].name == "b"


class TestLogNumbering:
    def test_next_run_number_empty(self, tmp_path: Path) -> None:
        assert cli.next_run_number(tmp_path) == 1

    def test_next_run_number_ignores_other_files(self, tmp_path: Path) -> None:
        (tmp_path / "cleaner_3.log.json").write_text("{}", encoding="utf-8")
        (tmp_path / "cleaner_9.log.json").write_text("{}", encoding="utf-8")
        (tmp_path / "cleaner_x.log.json").write_text("{}", encoding="utf-8")
        (tmp_path / "outro.log.json").write_text("{}", encoding="utf-8")
        assert cli.next_run_number(tmp_path) == 10

    def test_resolve_fixed_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        path = cli.resolve_log_file("saida/meu.log.json")
        assert path == Path("saida/meu.log.json")
        assert path.parent.is_dir()

    def test_resolve_auto_with_run_number(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "cleaner_4.log.json").write_text("{}", encoding="utf-8")
        assert cli.resolve_log_file("auto", 42) == Path("reports/cleaner_42.log.json")

    def test_resolve_auto_uses_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("RUN_NUMBER", "77")
        assert cli.resolve_log_file("") == Path("reports/cleaner_77.log.json")

    def test_resolve_auto_sequence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("RUN_NUMBER", raising=False)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "cleaner_6.log.json").write_text("{}", encoding="utf-8")
        assert cli.resolve_log_file("default") == Path("reports/cleaner_7.log.json")


class TestWriteLog:
    def test_appends_json_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "log.json"
        cli.write_log(path, {"a": 1})
        cli.write_log(path, {"b": 2})
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert lines == [{"a": 1}, {"b": 2}]


class TestMain:
    def test_invalid_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        assert cli.main(["--config", str(tmp_path / "naoexiste.yaml")]) == 2

    def test_auth_failure(self, run_main) -> None:
        assert run_main(fail_auth=True) == 1

    def test_scan_failure(self, run_main) -> None:
        StubClient.fail_scan = GraphError("scan quebrou")
        assert run_main() == 1

    def test_dry_run(self, run_main, tmp_path: Path) -> None:
        StubClient.messages = [make_message()]
        assert run_main() == 0
        client = StubClient.instances[-1]
        assert client.actions == []
        log_file = tmp_path / "reports" / "cleaner_1.log.json"
        entry = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
        assert entry["applied"] is False
        assert entry["action"] == "delete_permanent"
        assert entry["sender"] == "spam@bad.top"

    def test_apply(self, run_main, tmp_path: Path) -> None:
        StubClient.messages = [make_message("AAMk1"), make_message("AAMk2")]
        assert run_main(argv=["--apply"]) == 0
        client = StubClient.instances[-1]
        assert client.actions == [
            ("delete_permanent", "AAMk1"),
            ("delete_permanent", "AAMk2"),
        ]
        entries = [
            json.loads(line)
            for line in (tmp_path / "reports" / "cleaner_1.log.json")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert all(entry["applied"] is True for entry in entries)
        assert entries[0]["result"] == "ok:delete_permanent"

    def test_apply_partial_error(self, run_main, tmp_path: Path) -> None:
        StubClient.messages = [make_message("AAMk1"), make_message("AAMk2")]
        StubClient.fail_ids = {"AAMk2"}
        assert run_main(argv=["--apply"]) == 0
        client = StubClient.instances[-1]
        assert client.actions == [("delete_permanent", "AAMk1")]
        entries = [
            json.loads(line)
            for line in (tmp_path / "reports" / "cleaner_1.log.json")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert entries[1]["error"] == "falha simulada"

    def test_dry_run_overrides_apply(self, run_main) -> None:
        StubClient.messages = [make_message()]
        assert run_main(argv=["--apply", "--dry-run"]) == 0
        assert StubClient.instances[-1].actions == []

    def test_max_actions_limit(self, run_main, caplog: pytest.LogCaptureFixture) -> None:
        config = CONFIG_BASIC.replace("folders: [inbox]", "folders: [inbox]\nmax_actions_per_run: 1")
        StubClient.messages = [make_message("AAMk1"), make_message("AAMk2")]
        caplog.set_level(logging.INFO)
        assert run_main(config_text=config, argv=["--apply"]) == 0
        assert len(StubClient.instances[-1].actions) == 1
        assert "ficaram para a próxima execução" in caplog.text

    def test_cli_overrides(self, run_main, tmp_path: Path) -> None:
        StubClient.messages = [make_message()]
        code = run_main(
            argv=[
                "--folders",
                "inbox",
                "junkemail",
                "--max-actions",
                "2",
                "--max-scan-days",
                "3",
                "--max-scan-messages",
                "5",
                "--log-file",
                "custom.json",
            ]
        )
        assert code == 0
        client = StubClient.instances[-1]
        assert [call["folder"] for call in client.scan_calls] == ["inbox", "junkemail"]
        assert client.scan_calls[0]["max_messages"] == 5
        assert (tmp_path / "custom.json").is_file()

    def test_print_refresh_token(self, run_main, capsys: pytest.CaptureFixture[str]) -> None:
        StubClient.messages = []
        assert run_main(argv=["--print-refresh-token"], auth=("tok", "novo-refresh")) == 0
        assert "novo-refresh" in capsys.readouterr().out

    def test_verbose(self, run_main) -> None:
        StubClient.messages = [make_message()]
        assert run_main(argv=["-v"]) == 0

    def test_no_candidates(self, run_main, caplog: pytest.LogCaptureFixture) -> None:
        StubClient.messages = [make_message(sender="amigo@bom.com")]
        caplog.set_level(logging.INFO)
        assert run_main() == 0
        assert "Encontrados 0 candidatos" in caplog.text

    def test_since_window(self, run_main) -> None:
        StubClient.messages = []
        run_main()
        since = StubClient.instances[-1].scan_calls[0]["since"]
        parsed = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert datetime.now(timezone.utc) - parsed < timedelta(days=31)


class TestThreading:
    def test_parallel_actions(self, run_main: Any) -> None:
        StubClient.messages = [make_message(f"AAMk{i}") for i in range(6)]
        assert run_main(argv=["--apply", "--workers", "3"]) == 0
        assert {mid for _, mid in StubClient.all_actions} == {f"AAMk{i}" for i in range(6)}

    def test_parallel_creates_client_per_thread(self, run_main: Any) -> None:
        StubClient.messages = [make_message(f"AAMk{i}") for i in range(4)]
        assert run_main(argv=["--apply", "--workers", "4"]) == 0
        assert len(StubClient.instances) >= 2

    def test_single_candidate_stays_sequential(self, run_main: Any) -> None:
        StubClient.messages = [make_message()]
        assert run_main(argv=["--apply", "--workers", "4"]) == 0
        assert StubClient.all_actions == [("delete_permanent", "AAMk1")]

    def test_race_already_gone(
        self, run_main: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        StubClient.messages = [make_message("AAMk1"), make_message("AAMk2")]
        StubClient.missing_ids = {"AAMk2"}
        caplog.set_level(logging.INFO)
        assert run_main(argv=["--apply", "--workers", "2"]) == 0
        log_file = tmp_path / "reports" / "cleaner_1.log.json"
        entries = [
            json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
        ]
        assert entries[1]["result"] == "already_gone"
        assert "error" not in entries[1]
        assert "Ignorado (já removido)" in caplog.text
        assert '"already_gone": 1' in caplog.text

    def test_client_factory_is_thread_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "MailClient", StubClient)
        get_client = cli.client_factory("tok")
        first = get_client()
        assert get_client() is first

    def test_process_item_without_apply(self) -> None:
        client = StubClient("tok")
        item: cli.Pending = ("inbox", "AAMk1", {"sender": "a@b.com", "subject": "assunto"}, "mark_read")
        entry = cli.process_item(client, False, item)
        assert entry["applied"] is False
        assert "result" not in entry
        assert entry["id"] == "AAMk1"
