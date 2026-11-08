from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pandas as pd
import pytest

from hedge_fund.cost_policy import PaidServiceDisabled
from hedge_fund.data.client import FDClient
from hedge_fund.data.wallet import payment_session
from hedge_fund.llm.client import ChatLLM, JevLLM, make_llm
from hedge_fund.llm.codex_cli import CodexSubscriptionLLM, subscription_env, validate_answer, validate_included_allowance
from hedge_fund.no_cost.cli import analyze, portfolio_proposal, validate_packet
from hedge_fund.no_cost.data import annual_rows, symbols


@pytest.fixture
def packet():
    today = date.today()
    return {"schema_version": 1, "mode": "latest_research", "synthetic": True,
            "retrieved_at_utc": today.isoformat() + "T00:00:00+00:00",
            "limitations": ["Synthetic fixture, not market data."],
            "companies": [{"ticker": "TEST", "quote": {"close": 100.0, "currency": "USD", "date": today.isoformat()},
                           "annual_history": [{"period_type": "annual", "report_period": (today - timedelta(days=90)).isoformat()}]}]}


def test_paid_network_paths_are_blocked_before_io(monkeypatch):
    request = Mock(side_effect=AssertionError("network must not be reached"))
    monkeypatch.setattr("requests.Session.request", request)
    chat = Mock()
    with pytest.raises(PaidServiceDisabled):
        FDClient(api_key="test-key").get_prices("AAPL", "2026-01-01", "2026-01-02")
    with pytest.raises(PaidServiceDisabled):
        FDClient(wallet_key="not-a-real-key")
    with pytest.raises(PaidServiceDisabled):
        payment_session("not-a-real-key")
    with pytest.raises(PaidServiceDisabled):
        ChatLLM("test", chat).complete("system", "data")
    with pytest.raises(PaidServiceDisabled):
        JevLLM("test-key").complete("system", "data")
    with pytest.raises(PaidServiceDisabled):
        make_llm("gpt-6-sol")
    request.assert_not_called()
    chat.invoke.assert_not_called()


def test_default_factory_is_subscription_only(monkeypatch):
    monkeypatch.delenv("HEDGE_FUND_LLM_MODEL", raising=False)
    assert isinstance(make_llm(), CodexSubscriptionLLM)


def test_credentials_are_not_forwarded(monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "X402_WALLET_PRIVATE_KEY", "OPENAI_BASE_URL"):
        monkeypatch.setenv(name, "not-a-real-secret")
    env = subscription_env()
    assert not any(value == "not-a-real-secret" for value in env.values())


def test_api_key_auth_refused_without_model_call(tmp_path, monkeypatch):
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, "Logged in using an API key", ""))
    monkeypatch.setattr(subprocess, "run", runner)
    llm = CodexSubscriptionLLM(runtime_dir=tmp_path, executable="codex")
    with pytest.raises(RuntimeError, match="subscription login"):
        llm.complete("system", "data")
    assert runner.call_count == 1 and llm.calls == 0


def test_codex_command_forces_subscription_and_limits_calls(tmp_path, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "Logged in using ChatGPT")
        assert 'forced_login_method="chatgpt"' in command
        assert "features.shell_tool=false" in command
        assert "features.apps=false" in command
        assert "--ignore-user-config" in command
        assert kwargs["cwd"].is_relative_to(tmp_path)
        assert "ANNUAL" in kwargs["input"]
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps({"signal": "bullish", "confidence": 80, "reasoning": "Fixture"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    llm = CodexSubscriptionLLM(runtime_dir=tmp_path, max_calls=1, executable="codex")
    monkeypatch.setattr(llm, "check_allowance", lambda: None)
    assert json.loads(llm.complete("system", "data"))["confidence"] == 80
    with pytest.raises(RuntimeError, match="call limit"):
        llm.complete("system", "data")
    assert len(commands) == 2


def test_codex_failure_does_not_retry(tmp_path, monkeypatch):
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, "", "quota exceeded"))
    monkeypatch.setattr(subprocess, "run", runner)
    llm = CodexSubscriptionLLM(runtime_dir=tmp_path, executable="codex")
    llm._authenticated = True
    monkeypatch.setattr(llm, "check_allowance", lambda: None)
    with pytest.raises(RuntimeError, match="No paid fallback"):
        llm.complete("system", "data")
    assert runner.call_count == 1 and llm.calls == 1


@pytest.mark.parametrize("confidence", [True, float("nan"), float("inf"), -1, 101, "80"])
def test_invalid_answers_rejected(confidence):
    with pytest.raises(ValueError):
        validate_answer({"signal": "bullish", "confidence": confidence, "reasoning": "test"})


def test_annual_periods_are_aligned_and_missing_values_stay_unknown():
    periods = pd.to_datetime(["2025-12-31", "2024-12-31"])
    income = pd.DataFrame({periods[0]: {"Total Revenue": 120, "Net Income": 12, "Diluted EPS": 2},
                           periods[1]: {"Total Revenue": 100, "Net Income": 10, "Diluted EPS": 1}})
    balance = pd.DataFrame({periods[0]: {"Stockholders Equity": 60, "Total Debt": 15},
                            periods[1]: {"Stockholders Equity": 40, "Total Debt": 10}})
    # A mismatched cashflow period must not be borrowed for 2025.
    cash = pd.DataFrame({periods[1]: {"Operating Cash Flow": 20, "Capital Expenditure": -5}})
    rows = annual_rows(income, balance, cash)
    assert rows[0]["period_type"] == "annual"
    assert rows[0]["revenue_growth_yoy"] == pytest.approx(0.2)
    assert rows[0]["roe_average_equity"] == pytest.approx(0.24)
    assert rows[0]["free_cash_flow"] is None
    assert rows[1]["free_cash_flow"] == 15


def test_growth_does_not_cross_missing_years():
    income = pd.DataFrame({pd.Timestamp("2025-12-31"): {"Total Revenue": 120, "Net Income": 12},
                           pd.Timestamp("2023-12-31"): {"Total Revenue": 100, "Net Income": 10}})
    assert annual_rows(income, pd.DataFrame(), pd.DataFrame())[0]["revenue_growth_yoy"] is None


def test_fetch_uses_pretty_statement_labels(tmp_path, monkeypatch):
    import yfinance
    from hedge_fund.no_cost.data import fetch_packet

    period = pd.Timestamp(date.today() - timedelta(days=90))
    stock = Mock()
    stock.history.return_value = pd.DataFrame({"Close": [100.0]}, index=pd.to_datetime([date.today()]))
    stock.get_info.return_value = {"currency": "USD", "financialCurrency": "USD"}
    stock.get_income_stmt.return_value = pd.DataFrame({period: {"Total Revenue": 120, "Net Income": 12}})
    stock.get_balance_sheet.return_value = pd.DataFrame()
    stock.get_cashflow.return_value = pd.DataFrame()
    monkeypatch.setattr(yfinance, "Ticker", lambda ticker: stock)
    monkeypatch.setattr(yfinance, "set_tz_cache_location", lambda path: None)
    result = fetch_packet(["TEST"], tmp_path)
    stock.get_income_stmt.assert_called_once_with(pretty=True)
    stock.get_balance_sheet.assert_called_once_with(pretty=True)
    stock.get_cashflow.assert_called_once_with(pretty=True)
    assert result["companies"][0]["annual_history"][0]["revenue"] == 120
    validate_packet(result)


def test_packet_freshness_currency_and_historical_checks(packet):
    validate_packet(packet)
    old = deepcopy(packet)
    old["companies"][0]["quote"]["date"] = "2020-01-01"
    with pytest.raises(ValueError, match="stale"):
        validate_packet(old)
    old = deepcopy(packet)
    old["companies"][0]["quote"]["currency"] = "KRW"
    with pytest.raises(ValueError, match="USD"):
        validate_packet(old)
    old["mode"] = "backtest"
    with pytest.raises(ValueError, match="historical"):
        validate_packet(old)


def test_cached_analysis_and_original_risk_math(packet, tmp_path):
    llm = Mock(model="fake-subscription")
    llm.complete.return_value = json.dumps({"signal": "bullish", "confidence": 80, "reasoning": "Synthetic test only"})
    views = analyze(packet, ["buffett"], llm, tmp_path)
    result = portfolio_proposal(packet, views, {"currency": "USD", "cash": 10000, "positions": {}}, 0.2)
    assert result["target_positions"] == {"TEST": 20}
    assert result["estimated_remaining_cash"] == 8000
    assert result["target_weights"]["TEST"] == 0.2
    assert result["mode"] == "paper_proposal_only"
    again = analyze(packet, ["buffett"], llm, tmp_path)
    assert again[0]["cached"] and llm.complete.call_count == 1


def test_invalid_book_rejected(packet):
    view = [{"ticker": "TEST", "investor": "buffett", "signal": "neutral", "confidence": 0, "reasoning": "fixture"}]
    with pytest.raises(ValueError, match="Include held"):
        portfolio_proposal(packet, view, {"currency": "USD", "cash": 10, "positions": {"UNSEEN": 3}}, 0.2)
    with pytest.raises(ValueError, match="whole shares"):
        portfolio_proposal(packet, view, {"currency": "USD", "cash": 10, "positions": {"TEST": -1}}, 0.2)
    with pytest.raises(ValueError, match="one valid opinion"):
        portfolio_proposal(packet, view + view, {"currency": "USD", "cash": 10, "positions": {}}, 0.2)


def test_symbols_are_validated():
    assert symbols("aapl,MSFT,AAPL") == ["AAPL", "MSFT"]
    with pytest.raises(ValueError):
        symbols("../../secret")


@pytest.mark.parametrize("balance,has_credits,used,passes", [
    ("0", False, 15, True), ("10", True, 15, False), ("0", False, 100, False),
    ("0", False, 95, False), (None, False, 15, False), ("0", None, 15, False),
])
def test_included_allowance_only(balance, has_credits, used, passes):
    result = {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": used},
              "secondary": None, "credits": {"balance": balance, "hasCredits": has_credits, "unlimited": False}}}}
    if passes:
        validate_included_allowance(result)
    else:
        with pytest.raises(RuntimeError):
            validate_included_allowance(result)
