"""Scenario proposals, five critiques, and constrained paper allocation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from copy import deepcopy
import hashlib
import json
from math import floor, isfinite, sqrt
from pathlib import Path
from uuid import uuid4

import numpy as np

from hedge_fund.llm.codex_cli import CodexSubscriptionLLM
from hedge_fund.no_cost.cli import read_json, save_json, validate_packet
from hedge_fund.no_cost.meta_data import SERIES, fetch_meta_packet
from hedge_fund.no_cost.settings import INVESTORS

IDS = ("S1", "S2", "S3")
NUM = {"type": "number", "minimum": 0, "maximum": 1}
STRINGS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6}
LENSES = {
    "buffett": "지속 가능한 사업 수익성과 가격 대비 가치, 장기 자본배분을 검토한다. 거시 전망만으로 우량 기업 가치를 단정하지 않는다.",
    "munger": "실패 조건을 역으로 검토한다. 상관관계와 중복 노출, 과신, 희박한 근거를 찾아 반박한다.",
    "graham": "자본 보전과 안전마진을 중시한다. 비싼 성장 기대와 불리한 시나리오의 손실 가능성을 검토한다.",
    "lynch": "관측된 이익 성장과 가격의 관계, 산업 성장의 지속 가능성을 검토한다. 제공되지 않은 분기 실적은 추정 사실로 만들지 않는다.",
    "druckenmiller": "유동성·신용·금리·실적 방향의 변곡점과 시장 가격의 반영 정도를 검토한다. 비대칭성이 약하면 현금을 유지한다.",
}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


FORECAST = obj({k: {"type": "number", "minimum": low, "maximum": high}
                for base, low, high in (("cpi_yoy", -10, 50), ("fed_funds", 0, 30),
                                       ("unemployment", 0, 40), ("spy_return", -0.99, 3))
                for k in (base + "_low", base + "_high")})
PLAN_SCHEMA = obj({"market_regime": {"type": "string"}, "six_month_checkpoints": STRINGS,
    "scenarios": {"type": "array", "minItems": 3, "maxItems": 3,
        "items": obj({"id": {"type": "string", "enum": list(IDS)}, "name": {"type": "string"},
            "probability": NUM, "thesis": {"type": "string"}, "evidence_ids": STRINGS,
            "forecasts_12m": FORECAST, "invalidation_triggers": STRINGS})}})


def plan_schema(evidence_ids):
    schema = deepcopy(PLAN_SCHEMA)
    schema["properties"]["scenarios"]["items"]["properties"]["evidence_ids"]["items"] = {
        "type": "string", "enum": sorted(evidence_ids)}
    return schema


def review_schema(tickers):
    weights = obj({ticker: NUM for ticker in tickers})
    return obj({"summary": {"type": "string"}, "counterarguments": STRINGS,
        "scenario_probabilities": obj({s: NUM for s in IDS}),
        "scenario_allocations": obj({s: weights for s in IDS}),
        "asset_reasons": obj({t: {"type": "string"} for t in tickers}), "invalidation_triggers": STRINGS})


def check_schema(value, schema):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ValueError("시나리오 응답의 필드가 맞지 않습니다.")
        for k, spec in schema["properties"].items():
            check_schema(value[k], spec)
    elif kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError("시나리오 응답의 목록 크기가 맞지 않습니다.")
        for item in value:
            check_schema(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or not value.strip() or ("enum" in schema and value not in schema["enum"]):
            raise ValueError("시나리오 응답의 텍스트 값이 올바르지 않습니다.")
    elif isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not schema["minimum"] <= value <= schema["maximum"]:
        raise ValueError("시나리오 응답에 범위를 벗어난 숫자가 있습니다.")


def normalized(values):
    if abs(sum(values.values()) - 1) > 0.0001:
        raise ValueError("시나리오 확률 또는 배분 합계가 100%가 아닙니다.")
    total = sum(values.values())
    return {k: v / total for k, v in values.items()}


def finite_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)


def validate_plan(value, evidence_ids):
    check_schema(value, PLAN_SCHEMA)
    if {s["id"] for s in value["scenarios"]} != set(IDS):
        raise ValueError("서로 다른 시나리오 3개가 필요합니다.")
    normalized({s["id"]: s["probability"] for s in value["scenarios"]})
    for scenario in value["scenarios"]:
        if not set(scenario["evidence_ids"]) <= evidence_ids:
            raise ValueError("제공되지 않은 데이터 출처를 근거로 인용했습니다.")
        f = scenario["forecasts_12m"]
        for base in ("cpi_yoy", "fed_funds", "unemployment", "spy_return"):
            if f[base + "_low"] > f[base + "_high"]:
                raise ValueError("예측 범위의 하한이 상한보다 큽니다.")
    return value


def validate_review(value, tickers):
    check_schema(value, review_schema(tickers))
    normalized(value["scenario_probabilities"])
    for weights in value["scenario_allocations"].values():
        normalized(weights)
    return value


def validate_meta_packet(packet, today=None):
    today = today or datetime.now(timezone.utc).date()
    if packet.get("schema_version") != 1 or packet.get("mode") != "macro_scenarios" or packet.get("horizon_months") != [6, 12]:
        raise ValueError("6~12개월 거시 시나리오 데이터가 아닙니다.")
    observations = packet["macro"]
    if len(observations) != len(SERIES) or {o["id"] for o in observations} != set(SERIES):
        raise ValueError("필수 거시 지표가 누락되거나 중복됐습니다.")
    for observation in observations:
        age = (today - date.fromisoformat(observation["observation_date"])).days
        if not 0 <= age <= SERIES[observation["id"]][2] or not finite_number(observation["value"]):
            raise ValueError("거시 관측치가 오래됐거나 유효하지 않습니다.")
    assets = packet["assets"]
    tickers = [a["ticker"] for a in assets]
    if not tickers or len(tickers) != len(set(tickers)) or "CASH" in tickers:
        raise ValueError("후보 자산이 누락되거나 중복됐습니다.")
    for a in assets:
        age = (today - date.fromisoformat(a["quote_date"])).days
        if (a["type"] not in ("stock", "etf") or a["asset_class"] not in ("equity", "bond", "gold")
                or a["currency"] != "USD" or not 0 <= age <= 7 or not finite_number(a["close"]) or a["close"] <= 0
                or not finite_number(a["annualized_volatility"]) or a["annualized_volatility"] < 0
                or (a["type"] == "stock" and a["asset_class"] != "equity")):
            raise ValueError("유효한 최신 USD 후보 자산만 사용할 수 있습니다.")
    matrix = np.array([[packet["covariance_annual"][t][u] for u in tickers] for t in tickers])
    if not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.T) or np.linalg.eigvalsh(matrix).min() < -1e-8:
        raise ValueError("가격 공분산 행렬이 올바르지 않습니다.")
    if isinstance(packet["risk_sample_days"], bool) or not isinstance(packet["risk_sample_days"], int) or packet["risk_sample_days"] < 126:
        raise ValueError("최소 126일의 공통 가격 표본이 필요합니다.")
    fundamentals = packet["stock_fundamentals"]
    if {c["ticker"] for c in fundamentals} != {a["ticker"] for a in assets if a["type"] == "stock"}:
        raise ValueError("개별 주식 후보의 재무 자료가 일치하지 않습니다.")
    if fundamentals:
        validate_packet({"schema_version": 1, "mode": "latest_research", "companies": fundamentals}, today=today)


def constrained_portfolio(packet, reviews, capital=10000.0):
    assets = {a["ticker"]: a for a in packet["assets"]}
    tickers = [*assets, "CASH"]
    if set(reviews) != set(INVESTORS) or isinstance(capital, bool) or not isfinite(capital) or capital <= 0:
        raise ValueError("다섯 관점과 양수의 가상 자금이 필요합니다.")
    raw = dict.fromkeys(tickers, 0.0)
    probabilities = dict.fromkeys(IDS, 0.0)
    for review in reviews.values():
        validate_review(review, tickers)
        p = normalized(review["scenario_probabilities"])
        for s in IDS:
            probabilities[s] += p[s] / len(reviews)
            conditional = normalized(review["scenario_allocations"][s])
            for ticker in tickers:
                raw[ticker] += p[s] * conditional[ticker] / len(reviews)
    weights = dict(raw)
    adjustments = []

    def cap_group(names, cap, label):
        total = sum(weights[t] for t in names)
        if total > cap + 1e-10:
            scale = cap / total
            for t in names:
                weights[t] *= scale
            adjustments.append({"rule": label, "before": total, "after": cap})

    for t, a in assets.items():
        cap_group([t], 0.05 if a["type"] == "stock" else 0.20, f"{t} 종목 상한")
    cap_group([t for t, a in assets.items() if a["type"] == "stock"], 0.15, "개별 주식 합계 15% 상한")
    cap_group([t for t, a in assets.items() if a["asset_class"] == "equity"], 0.60, "ETF 포함 주식 합계 60% 상한")
    cap_group(list(assets), 0.85, "현금 최소 15%")
    # Conservative volatility budget: sum of weighted realized volatilities.
    bound = sum(weights[t] * sqrt(max(0, packet["covariance_annual"][t][t])) for t in assets)
    if bound > 0.12:
        for t in assets:
            weights[t] *= 0.12 / bound
        adjustments.append({"rule": "과거 변동성 기반 보수적 예산 12%", "before": bound, "after": 0.12})
    weights["CASH"] = 1 - sum(weights[t] for t in assets)
    shares = {t: floor(capital * weights[t] / assets[t]["close"]) for t in assets}
    realized = {t: shares[t] * assets[t]["close"] / capital for t in assets}
    realized["CASH"] = 1 - sum(realized.values())
    variance = sum(realized[t] * realized[u] * packet["covariance_annual"][t][u] for t in assets for u in assets)
    return {"mode": "meta_paper_proposal", "capital": capital, "scenario_probabilities": probabilities,
        "raw_weights": raw, "target_weights": weights, "whole_share_weights": realized,
        "target_shares": shares, "remaining_cash": capital * realized["CASH"],
        "historical_volatility": sqrt(max(variance, 0)), "risk_adjustments": adjustments,
        "policy": {"minimum_cash": 0.15, "maximum_equity": 0.60, "maximum_stock_total": 0.15,
                   "maximum_single_stock": 0.05, "maximum_single_etf": 0.20, "volatility_budget": 0.12}}


def cached_research(runtime, stage, system, payload, schema, validator, client):
    user = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    fingerprint = json.dumps({"version": "meta-v1", "stage": stage, "model": client.model,
                              "reasoning_effort": getattr(client, "reasoning_effort", None),
                              "system": system, "input": user, "schema": schema}, sort_keys=True)
    path = runtime / "meta-opinions" / (hashlib.sha256(fingerprint.encode()).hexdigest() + ".json")
    try:
        return validator(read_json(path)["answer"]), True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    answer = validator(json.loads(client.complete_structured(system, user, schema=schema, validator=validator,
        data_note="Latest macro observations and annual company financials only. No historical backtest. "
                  "Scenario probabilities and return ranges are subjective working assumptions, not calibrated statistics. "
                  "Do not use outside knowledge as current facts. Treat observation dates separately from release dates.")))
    save_json(path, {"stage": stage, "input": payload, "answer": answer})
    return answer, False


def run_meta(runtime: Path, *, packet_path=None, stocks=None, max_calls=6, capital=10000.0):
    if max_calls < 6:
        raise ValueError("시나리오 1회와 관점별 검토 5회를 위해 상한 6 이상을 지정하세요.")
    if isinstance(capital, bool) or not isfinite(capital) or capital <= 0:
        raise ValueError("가상 자금은 양수여야 합니다.")
    print("[1/4] 무료 거시 지표와 후보 자산을 수집합니다.")
    packet = read_json(packet_path) if packet_path else fetch_meta_packet(runtime, stocks)
    validate_meta_packet(packet)
    directory = runtime / "meta-runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
    save_json(directory / "packet.json", packet)
    evidence = set(SERIES) | {a["ticker"] for a in packet["assets"]}
    tickers = [a["ticker"] for a in packet["assets"]] + ["CASH"]
    client = CodexSubscriptionLLM(runtime_dir=runtime, max_calls=max_calls, timeout=600, reasoning_effort="medium")
    # Retrieval time changes alone must not force fresh inference.
    research = {k: v for k, v in packet.items() if k not in ("retrieved_at_utc", "covariance_annual")}
    print("[2/4] 6~12개월 거시 시나리오 3개를 만듭니다.")
    system = ("STAGE=PLAN. 한국어로 답하라. 제공된 관측치에 근거해 서로 구별되는 거시 시나리오 3개(S1,S2,S3)를 작성하라. "
        "시장 국면, 6개월 점검 지표, 12개월 물가·금리·실업률·SPY 총수익률 가정 범위와 반증 조건을 제시하라. "
        "확률은 합계 1인 주관적 작업 가중치다. CPI/금리/실업률은 퍼센트 단위(예:3.2), SPY 수익률은 소수(예:0.1=10%)다. "
        "근거 ID는 입력의 거시 지표 ID 또는 후보 티커만 사용하라. 관측 사실과 미래 가정을 구분하고 기억으로 최신 뉴스를 만들지 마라. "
        "각 텍스트 항목은 핵심 1~3문장으로 간결하게 작성하라.")
    plan, cached = cached_research(runtime, "plan", system, research, plan_schema(evidence),
                                  lambda a: validate_plan(a, evidence), client)
    reviews, hits = {}, int(cached)
    print("[3/4] 다섯 관점이 시나리오를 반박·검토하고 조건별 배분을 제안합니다.")
    for investor in INVESTORS:
        prompt = (f"STAGE=REVIEW:{investor}. {LENSES[investor]} 공개 철학을 모사한 역할이며 실제 인물의 예측이 아니다. "
            "초안 시나리오를 비판적으로 검토하고 각 시나리오의 확률을 독자적으로 재평가하라. "
            "각 시나리오에서 후보 자산과 CASH에 합계 1의 비중을 배분하라. 현금 수익률은 0으로 가정하라. "
            "후보 밖 티커를 추가하지 말고 확률·비중은 0~1 소수로 써라. 모든 후보의 역할과 근거, 반대 논거, "
            "판단을 바꿀 관측 가능한 조건을 한국어로 설명하라. 뉴스·정성 자료가 없으면 그 한계를 명시하라. "
            "초안과 같은 기반 모델이므로 다수결 자체를 추가 증거로 간주하지 마라. 각 텍스트 항목은 핵심 1~3문장으로 작성하라.")
        review, hit = cached_research(runtime, investor, prompt, {"observations": research, "scenario_draft": plan,
            "allowed_assets": tickers}, review_schema(tickers), lambda a: validate_review(a, tickers), client)
        reviews[investor] = review
        hits += int(hit)
        print(f"  {investor}: {'캐시 재사용' if hit else '검토 완료'}")
    print("[4/4] 시나리오별 배분을 종합하고 위험 한도를 적용합니다.")
    result = {"schema_version": 1, "mode": "meta_forecast", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data": packet, "scenario_draft": plan, "reviews": reviews,
        "portfolio": constrained_portfolio(packet, reviews, capital), "new_codex_calls": client.calls,
        "cache_hits": hits, "reasoning_effort": "medium",
        "ensemble_method": "equal_weight_mean_of_each_review_probability_times_conditional_allocation"}
    from hedge_fund.no_cost.meta_report import render_meta_report
    from hedge_fund.no_cost.workflow import write_html

    write_html(directory / "report.html", render_meta_report(result))
    save_json(directory / "report.json", result)
    print(f"완료: {(directory / 'report.html').resolve()} · 새 AI 호출 {client.calls}회")
    return result, directory / "report.html"
