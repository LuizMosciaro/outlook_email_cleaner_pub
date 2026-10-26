"""Testes do motor de regras."""

from __future__ import annotations

import pytest

from email_cleaner.config import Rule
from email_cleaner.rules import (
    build_index,
    is_allowlisted,
    normalize,
    rule_matches,
    sender_matches,
    strip_html,
)
from tests.conftest import make_message


class TestStripHtml:
    def test_empty_returns_empty(self) -> None:
        assert strip_html("") == ""

    def test_removes_tags_and_scripts(self) -> None:
        raw = "<html><style>.a{color:red}</style><script>alert(1)</script><p>Olá</p></html>"
        assert "Olá" in strip_html(raw)
        assert "alert" not in strip_html(raw)
        assert "color" not in strip_html(raw)

    def test_unescapes_entities(self) -> None:
        assert strip_html("a & b") == "a & b"


class TestNormalize:
    def test_empty(self) -> None:
        assert normalize("") == ""

    def test_none_like(self) -> None:
        assert normalize(None) == ""  # type: ignore[arg-type]

    def test_removes_accents_and_case(self) -> None:
        assert normalize("  Promoção   IMPERDÍVEL ") == "promocao imperdivel"

    def test_non_string(self) -> None:
        assert normalize(123) == "123"  # type: ignore[arg-type]


class TestSenderMatches:
    @pytest.mark.parametrize(
        ("pattern", "address"),
        [
            ("fulano@x.com", "FULANO@X.COM"),
            ("@dominio.com", "qualquer@dominio.com"),
            ("dominio.com", "qualquer@dominio.com"),
            ("*@*.top", "spam@bad.top"),
            ("spam?@bad.top", "spam1@bad.top"),
        ],
    )
    def test_matches(self, pattern: str, address: str) -> None:
        assert sender_matches(pattern, address) is True

    @pytest.mark.parametrize(
        ("pattern", "address"),
        [
            ("", "a@b.com"),
            ("a@b.com", ""),
            ("fulano@x.com", "outro@x.com"),
            ("@b.com", "a@bb.com"),
            ("b.com", "a@notb.com"),
        ],
    )
    def test_does_not_match(self, pattern: str, address: str) -> None:
        assert sender_matches(pattern, address) is False


class TestBuildIndex:
    def test_html_body_is_cleaned(self) -> None:
        message = make_message(body="<div>Compre <b>AGORA</b></div>")
        index = build_index(message)
        assert index["body"] == "compre agora"
        assert index["sender"] == "spam@bad.top"
        assert index["subject"] == "ganhe dinheiro agora"
        assert index["id"] == "AAMk1"

    def test_body_preview_fallback(self) -> None:
        index = build_index(make_message())
        assert index["body"] == "preview"

    def test_missing_fields(self) -> None:
        index = build_index({})
        assert index["id"] is None
        assert index["sender"] == ""
        assert index["senders"] == []
        assert index["subject"] == ""
        assert index["body"] == ""
        assert index["headers"] == {}

    def test_senders_include_from_sender_and_reply_to(self) -> None:
        message = make_message(
            sender="forjado@evil.top",
            graph_sender="noreply@spam.com",
            reply_to=["reply@esp.com", "NOREPLY@spam.com"],
        )
        index = build_index(message)
        assert index["sender"] == "forjado@evil.top"
        assert index["senders"] == ["forjado@evil.top", "noreply@spam.com", "reply@esp.com"]

    def test_sender_only_message(self) -> None:
        index = build_index({"sender": {"emailAddress": {"address": "A@B.com"}}})
        assert index["senders"] == ["a@b.com"]
        assert index["sender"] == "a@b.com"

    def test_headers_aggregated_and_normalized(self) -> None:
        message = make_message(
            headers=[
                {"name": "List-Unsubscribe", "value": "<mailto:SAIR@X.com>"},
                {"name": "list-unsubscribe", "value": "<http://x.com/u>"},
                {"name": "X-Empty", "value": None},
                {"value": "sem nome"},
            ]
        )
        index = build_index(message)
        assert index["headers"]["list-unsubscribe"] == "<mailto:sair@x.com> <http://x.com/u>"
        assert index["headers"][""] == "sem nome"

    def test_headers_without_normalization(self) -> None:
        message = make_message(headers=[{"name": "X-Test", "value": "VALOR"}])
        index = build_index(message, normalize_headers=False)
        assert index["headers"]["x-test"] == "VALOR"


class TestRuleMatches:
    @pytest.fixture
    def index(self) -> dict:
        return build_index(
            make_message(
                sender="promo@bad.top",
                subject="Promoção IMPERDÍVEL",
                body="<p>clique para ganhar</p>",
                headers=[{"name": "List-Unsubscribe", "value": "<mailto:x@y.com>"}],
            )
        )

    def test_no_criteria_returns_false(self) -> None:
        rule = Rule(name="vazia", senders=["x@y.com"], body_contains=["x"])
        rule.senders = []
        rule.body_contains = []
        assert rule_matches(rule, build_index({})) is False

    def test_sender_match(self, index: dict) -> None:
        assert rule_matches(Rule(name="r", senders=["@bad.top"]), index) is True

    def test_matches_real_sender_when_from_is_spoofed(self) -> None:
        index = build_index(
            make_message(sender="spoof@example.invalid", graph_sender="noreply@spam.example")
        )
        rule = Rule(name="r", senders=["@spam.example"])
        assert rule_matches(rule, index) is True

    def test_subject_match_accent_insensitive(self, index: dict) -> None:
        rule = Rule(name="r", subject_contains=["promocao imperdivel"])
        assert rule_matches(rule, index) is True

    def test_subject_empty_keyword_ignored(self, index: dict) -> None:
        rule = Rule(name="r", subject_contains=["", "promocao"], senders=["@bad.top"])
        assert rule_matches(rule, index) is True

    def test_body_match(self, index: dict) -> None:
        assert rule_matches(Rule(name="r", body_contains=["ganhar"]), index) is True

    def test_headers_match(self, index: dict) -> None:
        rule = Rule(
            name="r",
            headers_contain={"List-Unsubscribe": "mailto:"},
        )
        assert rule_matches(rule, index) is True

    def test_regex_matches_cyrillic_subject(self) -> None:
        index = build_index(make_message(subject="Привет, купи сейчас"))
        rule = Rule(name="r", regex=[r"[\u0400-\u04FF]"])
        assert rule_matches(rule, index) is True

    def test_regex_matches_cjk_body(self) -> None:
        index = build_index(make_message(body="<p>限时优惠 立即购买</p>"))
        rule = Rule(name="r", regex=[r"[\u3040-\u30FF]", r"[\u4E00-\u9FFF]"])
        assert rule_matches(rule, index) is True

    def test_regex_no_match(self, index: dict) -> None:
        rule = Rule(name="r", regex=[r"[\u0400-\u04FF]", r"[\u4E00-\u9FFF]"])
        assert rule_matches(rule, index) is False

    def test_sender_longer_than_matches(self) -> None:
        index = build_index(
            make_message(sender="x" * 41 + "@example.invalid")
        )
        assert rule_matches(Rule(name="r", sender_longer_than=40), index) is True

    def test_sender_longer_than_no_match(self, index: dict) -> None:
        assert rule_matches(Rule(name="r", sender_longer_than=40), index) is False

    def test_sender_longer_than_uses_real_sender(self) -> None:
        index = build_index(make_message(sender="a@b.co", graph_sender="x" * 40 + "@y.com"))
        assert rule_matches(Rule(name="r", sender_longer_than=40), index) is True

    def test_headers_missing_key(self, index: dict) -> None:
        rule = Rule(name="r", headers_contain={"Precedence": "bulk"})
        assert rule_matches(rule, index) is False

    def test_any_semantics(self, index: dict) -> None:
        rule = Rule(name="r", senders=["@bad.top"], subject_contains=["inexistente"])
        assert rule_matches(rule, index) is True

    def test_all_semantics(self, index: dict) -> None:
        rule = Rule(
            name="r",
            senders=["@bad.top"],
            subject_contains=["inexistente"],
            require_all=True,
        )
        assert rule_matches(rule, index) is False

    def test_all_semantics_matching(self, index: dict) -> None:
        rule = Rule(
            name="r",
            senders=["@bad.top"],
            body_contains=["ganhar"],
            require_all=True,
        )
        assert rule_matches(rule, index) is True


class TestAllowlist:
    def test_empty_list(self) -> None:
        assert is_allowlisted([], "a@b.com") is False

    def test_match(self) -> None:
        assert is_allowlisted(["@b.com"], "a@b.com") is True

    def test_no_match(self) -> None:
        assert is_allowlisted(["@c.com"], "a@b.com") is False
