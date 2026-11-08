from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pandas as pd
import pytest

from hedge_fund.llm.codex_cli import CodexSubscriptionLLM
from hedge_fund.no_cost.cli import save_json
from hedge_fund.no_cost import meta
from hedge_fund.no_cost.meta_data import SERIES, fred_download, macro_from_csv
from hedge_fund.no_cost.meta_report import render_meta_report, source
from hedge_fund.no_cost.settings import INVESTORS


def draft():
    return {"market_regime": "Synthetic test", "six_month_checkpoints": ["test"], "scenarios": [
        {"id": sid, "name": sid, "probability": p, "thesis": "Synthetic", "evidence_ids": ["UNRATE"],
         "forecasts_12m": {f"{base}_{end}": value for base, value in
             (("cpi_yoy", 3), ("fed_funds", 4), ("unemployment", 4), ("spy_return", .1)) for end in ("low", "high")},
         "invalidation_triggers": ["test"]} for sid, p in zip(meta.IDS, (.5, .3, .2))]}


def review(tickers, weights=None):
    weights = weights or {t: 1 / len(tickers) for t in tickers}
    return {"summary": "Synthetic", "counterarguments": ["test"],
            "scenario_probabilities": dict(zip(meta.IDS, (.5, .3, .2))),
            "scenario_allocations": {s: dict(weights) for s in meta.IDS},
            "asset_reasons": {t: "test" for t in tickers}, "invalidation_triggers": ["test"]}


@pytest.fixture
def packet():
    today = date.today().isoformat()
    assets = [{"ticker": t, "name": t, "type": "etf", "asset_class": "equity" if t != "IEF" else "bond",
               "close": 103, "quote_date": today, "currency": "USD", "annualized_volatility": .2,
               "returns": {"63": .03}, "source": f"https://finance.yahoo.com/quote/{t}/history/"}
              for t in ("SPY", "QQQ", "IWM", "IEF")]
    return {"schema_version": 1, "mode": "macro_scenarios", "synthetic": True, "horizon_months": [6, 12],
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "assets": assets, "stock_fundamentals": [],
            "macro": [{"id": s, "name": s, "unit": "%", "value": 4, "observation_date": today,
                       "yoy_change": None, "source": f"https://fred.stlouisfed.org/series/{s}"} for s in SERIES],
            "covariance_annual": {a["ticker"]: {b["ticker"]: .04 if a == b else 0 for b in assets} for a in assets},
            "risk_sample_days": 300, "limitations": ["Synthetic test"]}


def test_csv_and_mixed_frequency_zip_share_parser():
    today = date.today()
    dates = [today - timedelta(days=400), today - timedelta(days=2), today + timedelta(days=5)]
    frame = pd.DataFrame({s: [100, 110, 999] for s in SERIES}, index=[str(d) for d in dates])
    raw = frame.to_csv(index_label="observation_date")
    memory = BytesIO()
    with ZipFile(memory, "w") as archive:
        for s in SERIES:
            archive.writestr(s + ".csv", frame[[s]].to_csv(index_label="observation_date"))
    for content in (raw, fred_download(raw.encode()), fred_download(memory.getvalue())):
        observations = macro_from_csv(content, today)
        assert len(observations) == 10
        assert observations[0]["value"] == 110
        assert observations[0]["yoy_change"] == pytest.approx(.1)
        assert observations[1]["yoy_change"] is None
    with pytest.raises(ValueError, match="필수"):
        macro_from_csv(frame.drop(columns="DFF").to_csv(), today)
    with pytest.raises(ValueError, match="오래"):
        macro_from_csv(raw, today + timedelta(days=200))


@pytest.mark.parametrize("mutation", ["stale", "nan_vol", "negative_cov", "missing_macro", "stock_without_financials"])
def test_invalid_market_packets_stop(packet, mutation):
    meta.validate_meta_packet(packet)
    if mutation == "stale":
        packet["assets"][0]["quote_date"] = "2020-01-01"
    elif mutation == "nan_vol":
        packet["assets"][0]["annualized_volatility"] = float("nan")
    elif mutation == "negative_cov":
        packet["covariance_annual"]["SPY"]["SPY"] = -.1
    elif mutation == "missing_macro":
        packet["macro"].pop()
    else:
        packet["assets"][0]["type"] = "stock"
    with pytest.raises(ValueError):
        meta.validate_meta_packet(packet)


def test_no_invented_evidence_ranges_or_allocations():
    plan = draft()
    schema = meta.plan_schema(set(SERIES))
    assert "enum" not in schema["properties"]["six_month_checkpoints"]["items"]
    assert "enum" not in schema["properties"]["scenarios"]["items"]["properties"]["invalidation_triggers"]["items"]
    meta.validate_plan(plan, set(SERIES))
    plan["scenarios"][0]["evidence_ids"] = ["INVENTED"]
    with pytest.raises(ValueError):
        meta.check_schema(plan, meta.plan_schema(set(SERIES)))
    with pytest.raises(ValueError):
        meta.validate_plan(plan, set(SERIES))
    plan = draft()
    plan["scenarios"][0]["forecasts_12m"]["cpi_yoy_low"] = 10
    with pytest.raises(ValueError):
        meta.validate_plan(plan, set(SERIES))
    for bad in (.9, -1, float("nan"), True):
        r = review(["SPY", "CASH"])
        r["scenario_allocations"]["S1"]["SPY"] = bad
        with pytest.raises(ValueError):
            meta.validate_review(r, ["SPY", "CASH"])
    r = review(["SPY", "CASH"])
    r["scenario_allocations"]["S1"]["TSLA"] = 0
    with pytest.raises(ValueError):
        meta.validate_review(r, ["SPY", "CASH"])


def test_joint_probabilities_are_combined_before_averaging(packet):
    tickers = [a["ticker"] for a in packet["assets"]] + ["CASH"]
    reviews = {}
    for index, investor in enumerate(INVESTORS):
        r = review(tickers, {t: float(t == "CASH") for t in tickers})
        r["scenario_probabilities"] = {"S1": 1 if index < 2 else 0, "S2": 0 if index < 2 else 1, "S3": 0}
        r["scenario_allocations"]["S1"] = {t: float(t == ("SPY" if index < 2 else "CASH")) for t in tickers}
        reviews[investor] = r
    p = meta.constrained_portfolio(packet, reviews)
    assert p["raw_weights"]["SPY"] == pytest.approx(.4)  # marginal-product mistake gives .16
    assert p["target_weights"]["SPY"] == .2
    assert p["target_shares"]["SPY"] == 19
    assert sum(p["whole_share_weights"].values()) == pytest.approx(1)
    assert p["remaining_cash"] == pytest.approx(10000 - 19 * 103)


def test_risk_caps_and_volatility_budget(packet):
    for a in packet["assets"]:
        if a["ticker"] != "IEF":
            a["type"] = "stock"
    tickers = [a["ticker"] for a in packet["assets"]] + ["CASH"]
    reviews = {i: review(tickers, {t: 0 if t == "CASH" else .25 for t in tickers}) for i in INVESTORS}
    p = meta.constrained_portfolio(packet, reviews)
    assert sum(p["target_weights"][t] for t in ("SPY", "QQQ", "IWM")) <= .15 + 1e-9
    assert p["target_weights"]["SPY"] <= .05
    assert p["target_weights"]["IEF"] <= .2
    assert p["whole_share_weights"]["CASH"] >= .15
    for t in tickers[:-1]:
        packet["covariance_annual"][t][t] = 4
    p = meta.constrained_portfolio(packet, reviews)
    assert sum(p["target_weights"][t] * 2 for t in tickers[:-1]) <= .12 + 1e-9
    assert p["historical_volatility"] <= .12 + 1e-9


def test_combined_equity_and_cash_limits_include_etfs(packet):
    for t in ("X1", "X2", "X3", "X4"):
        packet["assets"].append({**packet["assets"][0], "ticker": t})
    packet["assets"][-1]["asset_class"] = "gold"
    tickers = [a["ticker"] for a in packet["assets"]]
    packet["covariance_annual"] = {t: {u: .0001 if t == u else 0 for u in tickers} for t in tickers}
    # 6 equity ETFs plus bond and gold; high risk-independent weights trigger both group caps.
    weights = {a["ticker"]: .12 if a["asset_class"] == "equity" else .14 for a in packet["assets"]} | {"CASH": 0}
    reviews = {i: review(tickers + ["CASH"], weights) for i in INVESTORS}
    p = meta.constrained_portfolio(packet, reviews)
    assert sum(p["target_weights"][a["ticker"]] for a in packet["assets"] if a["asset_class"] == "equity") <= .6 + 1e-9
    assert p["target_weights"]["CASH"] >= .15 - 1e-9
    assert {a["rule"] for a in p["risk_adjustments"]} >= {"ETF 포함 주식 합계 60% 상한", "현금 최소 15%"}


def test_candidates_preserve_joint_weights_rank_and_risk_limits(packet):
    tickers = [a["ticker"] for a in packet["assets"]] + ["CASH"]
    reviews = {i: review(tickers) for i in INVESTORS}
    for index, r in enumerate(reviews.values()):
        r["scenario_probabilities"] = {"S1": .1 if index < 2 else .6, "S2": .9 if index < 2 else .4, "S3": 0}
        r["scenario_allocations"]["S1"] = {t: .2 if t == "CASH" else .8 if t == ("SPY" if index < 2 else "IEF") else 0 for t in tickers}
    result = {"data": packet, "reviews": reviews, "scenario_draft": draft(),
              "portfolio": meta.constrained_portfolio(packet, reviews)}
    saved = deepcopy(result)
    candidates = meta.scenario_candidates(result)
    assert result == saved
    assert [c["id"] for c in candidates] == ["S2", "S1", "S3"]
    assert [c["leading"] for c in candidates] == [True, False, False]
    assert candidates[1]["portfolio"]["raw_weights"]["SPY"] == pytest.approx(.08)
    for ticker in tickers:
        reconstructed = sum(c["probability"] * c["portfolio"]["raw_weights"][ticker] for c in candidates)
        assert reconstructed == pytest.approx(result["portfolio"]["raw_weights"][ticker])
    for c in candidates:
        weights = c["portfolio"]["target_weights"]
        assert sum(weights.values()) == pytest.approx(1)
        assert weights["CASH"] >= .15 - 1e-9
        assert all(0 <= weights[t] <= .2 + 1e-9 for t in tickers[:-1])
    for r in reviews.values():
        r["scenario_probabilities"] = {"S1": .5, "S2": .5, "S3": 0}
    assert sum(c["leading"] for c in meta.scenario_candidates(result)) == 2


def test_end_to_end_cache_and_offline_report(packet, tmp_path, monkeypatch):
    class Client:
        model = "synthetic-offline"
        def __init__(self, **kwargs):
            self.calls = 0
        def complete_structured(self, system, user, *, schema, validator, data_note):
            self.calls += 1
            answer = draft() if "STAGE=PLAN" in system else review(json.loads(user)["allowed_assets"])
            return json.dumps(validator(answer))

    monkeypatch.setattr(meta, "CodexSubscriptionLLM", Client)
    path = tmp_path / "packet.json"
    save_json(path, packet)
    first, html = meta.run_meta(tmp_path, packet_path=path)
    assert first["new_codex_calls"] == 6 and first["cache_hits"] == 0
    assert html.is_file() and html.with_suffix(".json").is_file()
    second, _ = meta.run_meta(tmp_path, packet_path=path)
    assert second["new_codex_calls"] == 0 and second["cache_hits"] == 6
    first["scenario_draft"]["market_regime"] = '<script>alert("x")</script>'
    first["scenario_draft"]["scenarios"][0]["name"] = '<script>alert("x")</script>'
    html = render_meta_report(first)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "6~12개월" in html and "워런 버핏" in html and "검증된 예측" in html and "합성 테스트" in html
    assert html.index('id="candidates"') < html.index('id="scenarios"')
    assert 'class="candidate-card leading" data-scenario="S1"' in html
    assert source("javascript:alert(1)") == "출처 링크 없음"


def test_custom_schema_keeps_shared_cost_guards(tmp_path, monkeypatch):
    client = CodexSubscriptionLLM(runtime_dir=tmp_path, executable="codex", max_calls=1, reasoning_effort="medium")
    client._authenticated = True
    checked = []
    monkeypatch.setattr(client, "check_allowance", lambda: checked.append(True))
    schema = meta.obj({"value": {"type": "number", "minimum": 0, "maximum": 1}})
    def run(command, **kwargs):
        assert "--ignore-user-config" in command
        assert 'forced_login_method="chatgpt"' in command
        assert 'model_reasoning_effort="medium"' in command
        assert json.loads(Path(command[command.index("--output-schema") + 1]).read_text()) == schema
        Path(command[command.index("--output-last-message") + 1]).write_text('{"value":0.5}')
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    validator = lambda value: (meta.check_schema(value, schema), value)[1]
    assert json.loads(client.complete_structured("test", "{}", schema=schema, validator=validator, data_note="test"))["value"] == .5
    with pytest.raises(RuntimeError, match="call limit"):
        client.complete("test", "{}")
    assert checked == [True] and client.calls == 1
