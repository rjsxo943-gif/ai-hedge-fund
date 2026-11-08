from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from types import SimpleNamespace

import pytest

from hedge_fund.no_cost.cli import analyze, estimate_calls, read_json, save_json
from hedge_fund.no_cost.report import changes, render_report
from hedge_fund.no_cost.settings import defaults, load_settings, validate_settings
from hedge_fund.no_cost.workflow import interactive_run, previous_report, run_once


@pytest.fixture
def packet():
    today = date.today()
    return {"schema_version": 1, "mode": "latest_research", "synthetic": True,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "limitations": ["Synthetic fixture"],
            "companies": [{"ticker": "TEST", "name": "테스트 기업", "sector": "테스트 업종",
                           "financial_currency": "USD",
                           "quote": {"close": 100, "currency": "USD", "date": today.isoformat()},
                           "annual_history": [{"period_type": "annual", "report_period": (today - timedelta(days=90)).isoformat(),
                                               "revenue": 120, "net_income": 12, "free_cash_flow": None}],
                           "sources": ["https://example.com/financials"]}]}


class FakeClient:
    model = "offline-test-model"

    def __init__(self, **kwargs):
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return json.dumps({"signal": "bullish", "confidence": 80, "reasoning": "테스트 근거입니다. 실제 투자 판단이 아닙니다."})


def settings():
    return {**defaults(), "watchlist": ["TEST"]}


def test_full_workflow_and_cache_create_reports_without_network(packet, tmp_path):
    config = settings()
    result, html_path = run_once(tmp_path, config, collector=lambda tickers, runtime: packet, client_factory=FakeClient)
    assert result["new_codex_calls"] == 5
    assert html_path.is_file()
    assert read_json(html_path.with_suffix(".json"))["portfolio"]["target_positions"] == {"TEST": 20}
    html = html_path.read_text(encoding="utf-8")
    assert "테스트 기업" in html and "워런 버핏" in html and "스탠리 드러켄밀러" in html
    assert "매수 20주" in html and "가상 포트폴리오 조정안" in html
    assert "이전 보고서의 같은 관점과 비교할 수 없습니다" in html
    result2, path2 = run_once(tmp_path, config, collector=lambda tickers, runtime: packet, client_factory=FakeClient)
    assert result2["new_codex_calls"] == 0 and all(v["cached"] for v in result2["opinions"])
    assert path2 != html_path
    assert "강세 → 강세" in path2.read_text(encoding="utf-8")
    assert previous_report(tmp_path)["generated_at_utc"] == result2["generated_at_utc"]


def test_budget_failure_makes_no_model_calls_or_complete_report(packet, tmp_path):
    class MustNotCall(FakeClient):
        def complete(self, *args):
            pytest.fail("Budget failure must precede model inference")

    with pytest.raises(ValueError, match="새 AI 분석 5회"):
        run_once(tmp_path, {**settings(), "max_calls": 1}, collector=lambda *args: packet, client_factory=MustNotCall)
    assert previous_report(tmp_path) is None
    assert not list(tmp_path.glob("runs/*/report.json"))


def test_invalid_book_fails_before_inference(packet, tmp_path):
    book = tmp_path / "book.json"
    save_json(book, {"currency": "USD", "cash": 100, "positions": {"UNKNOWN": 2}})
    with pytest.raises(ValueError, match="Include held"):
        run_once(tmp_path, {**settings(), "paper_book": str(book)}, collector=lambda *args: packet,
                 client_factory=lambda **kw: pytest.fail("Should fail before constructing a client"))


def test_corrupt_cache_is_counted_and_rebuilt(packet, tmp_path):
    llm = FakeClient()
    analyze(packet, ["buffett"], llm, tmp_path)
    assert estimate_calls(packet, ["buffett"], llm.model, tmp_path) == 0
    cache = next((tmp_path / "opinions").glob("*.json"))
    cache.write_text("{broken", encoding="utf-8")
    assert estimate_calls(packet, ["buffett"], llm.model, tmp_path) == 1
    views = analyze(packet, ["buffett"], llm, tmp_path)
    assert llm.calls == 2 and not views[0]["cached"]


def test_settings_and_interactive_watchlist_stay_local(tmp_path, monkeypatch):
    from hedge_fund.no_cost import workflow

    monkeypatch.setattr("builtins.input", lambda prompt: "msft, aapl")
    captured = {}

    def fake_run(runtime, config, **kwargs):
        captured.update(config)
        return {"new_codex_calls": 0}, tmp_path / "report.html"

    monkeypatch.setattr(workflow, "run_once", fake_run)
    interactive_run(tmp_path, open_browser=False, max_calls=8)
    assert captured["watchlist"] == ["MSFT", "AAPL"] and captured["max_calls"] == 8
    saved = load_settings(tmp_path)
    assert saved["watchlist"] == ["MSFT", "AAPL"]
    assert saved["max_calls"] == defaults()["max_calls"]


@pytest.mark.parametrize("key,value", [("max_calls", 0), ("max_calls", True), ("paper_cash", float("nan")),
                                      ("max_position", 1.5), ("watchlist", ["aapl"]), ("investors", ["wrong"])])
def test_invalid_settings_rejected(key, value):
    with pytest.raises(ValueError):
        validate_settings({**defaults(), key: value})


def test_report_changes_only_compare_matching_investors():
    current = {"opinions": [{"ticker": "TEST", "investor": "buffett", "signal": "bullish", "confidence": 80},
                            {"ticker": "TEST", "investor": "lynch", "signal": "neutral", "confidence": 40}]}
    old = {"opinions": [{"ticker": "TEST", "investor": "buffett", "signal": "neutral", "confidence": 60},
                        {"ticker": "OTHER", "investor": "lynch", "signal": "bearish", "confidence": 80}]}
    rows = changes(current, old)
    assert len(rows) == 1 and rows[0]["signal_changed"] and rows[0]["confidence_delta"] == 20


def test_report_escapes_model_content_and_loads_no_external_assets(packet, tmp_path):
    result, _ = run_once(tmp_path, settings(), collector=lambda *args: packet, client_factory=FakeClient)
    result["opinions"][0]["reasoning"] = '<script src="https://evil.example/"></script><img src=x onerror=alert(1)>'
    result["data"]["companies"][0]["name"] = '<b onclick="alert(1)">unsafe</b>'
    result["data"]["companies"][0]["sources"].append("javascript:alert(1)")
    html = render_report(result)
    assert "&lt;script" in html and "&lt;b onclick=" in html
    assert 'href="javascript:' not in html

    class Assets(HTMLParser):
        tags = []

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "img", "link", "iframe"):
                self.tags.append(tag)

    parser = Assets()
    parser.feed(html)
    assert parser.tags == []
    assert "default-src" in html


def test_saved_packet_must_match_requested_watchlist(packet, tmp_path):
    source = tmp_path / "packet.json"
    save_json(source, packet)
    with pytest.raises(ValueError, match="관심 종목이 다릅니다"):
        run_once(tmp_path, {**settings(), "watchlist": ["AAPL"]}, packet_path=source, client_factory=FakeClient)


def test_export_command_never_calls_ai_and_protects_input(packet, tmp_path, monkeypatch):
    from hedge_fund.llm.codex_cli import CodexSubscriptionLLM
    from hedge_fund.no_cost.workflow import dispatch

    result, _ = run_once(tmp_path, settings(), collector=lambda *args: packet, client_factory=FakeClient)
    source = tmp_path / "saved.json"
    save_json(source, result)
    monkeypatch.setattr(CodexSubscriptionLLM, "complete", lambda *args: pytest.fail("Export must not call AI"))
    args = SimpleNamespace(command="report", input=source, previous=None, out=None, open_report=False)
    dispatch(args)
    assert source.with_suffix(".html").is_file()
    args.out = source
    with pytest.raises(ValueError, match="원본 JSON"):
        dispatch(args)
    assert read_json(source)["schema_version"] == 1
