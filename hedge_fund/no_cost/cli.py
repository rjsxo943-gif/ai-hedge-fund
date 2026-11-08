"""Default entry point: free latest data and subscription-only analysis."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from math import floor, isfinite
from pathlib import Path
import sys

from hedge_fund.llm.codex_cli import CodexSubscriptionLLM, validate_answer
from hedge_fund.no_cost.data import fetch_packet, symbols
from hedge_fund.no_cost.settings import INVESTORS


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def validate_packet(packet: dict, *, today: date | None = None) -> None:
    today = today or datetime.now(timezone.utc).date()
    if packet.get("mode") != "latest_research" or packet.get("schema_version") != 1:
        raise ValueError("Only schema-v1 latest-research packets are supported; no historical backtests.")
    if not packet.get("companies"):
        raise ValueError("The data packet has no companies")
    seen = set()
    for company in packet["companies"]:
        ticker = company["ticker"]
        if ticker in seen or symbols(ticker) != [ticker]:
            raise ValueError("Duplicate or invalid ticker in data packet")
        seen.add(ticker)
        close = company["quote"]["close"]
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not isfinite(close) or close <= 0:
            raise ValueError(f"Invalid price for {ticker}")
        age = (today - date.fromisoformat(company["quote"]["date"])).days
        if not 0 <= age <= 7:
            raise ValueError(f"{ticker}: quote is stale or future-dated; fetch a fresh packet")
        if company["quote"].get("currency") != "USD":
            raise ValueError(f"{ticker}: the first paper-portfolio version supports USD quotes only")
        history = company.get("annual_history", [])
        if not history or any(p.get("period_type") != "annual" for p in history):
            raise ValueError(f"{ticker}: annual statement history is required")
        newest = max(date.fromisoformat(p["report_period"]) for p in history)
        if not 0 <= (today - newest).days <= 550:
            raise ValueError(f"{ticker}: latest annual financials are stale or future-dated")


def opinion_inputs(packet: dict, investors: list[str], model: str, runtime_dir: Path):
    from hedge_fund.signals import ALPHA_MODEL_REGISTRY

    for company in packet["companies"]:
        user = json.dumps({"company": company, "limitations": packet.get("limitations", [])},
                          ensure_ascii=False, sort_keys=True, allow_nan=False)
        for investor in investors:
            # Read the original persona prompt without instantiating its API client.
            persona = object.__new__(ALPHA_MODEL_REGISTRY[investor])
            system = persona.get_system_prompt()
            key = hashlib.sha256((model + system + user).encode()).hexdigest()
            cache = runtime_dir / "opinions" / f"{key}.json"
            yield company["ticker"], investor, system, user, cache


def cached_answer(path: Path):
    try:
        return validate_answer(read_json(path)["answer"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def estimate_calls(packet: dict, investors: list[str], model: str, runtime_dir: Path) -> int:
    return sum(cached_answer(cache) is None for _, _, _, _, cache in opinion_inputs(packet, investors, model, runtime_dir))


def analyze(packet: dict, investors: list[str], llm, runtime_dir: Path) -> list[dict]:
    views = []
    for ticker, investor, system, user, cache in opinion_inputs(packet, investors, llm.model, runtime_dir):
        answer = cached_answer(cache)
        if answer is not None:
            cached = True
        else:
            print(f"Analyzing {ticker} as {investor} using Codex subscription...", file=sys.stderr)
            answer = validate_answer(json.loads(llm.complete(system, user)))
            save_json(cache, {"model": llm.model, "investor": investor, "ticker": ticker,
                              "system": system, "data": json.loads(user), "answer": answer})
            cached = False
        views.append({"ticker": ticker, "investor": investor, **answer, "cached": cached})
    return views


def portfolio_proposal(packet: dict, views: list[dict], book: dict, max_position: float) -> dict:
    from hedge_fund.models import Signal
    from hedge_fund.portfolio.construction import blend_signals
    from hedge_fund.risk.limits import RiskLimits, apply_limits

    if not isfinite(max_position) or not 0 < max_position <= 1:
        raise ValueError("max-position must be between 0 and 1")
    if book.get("currency") != "USD":
        raise ValueError("Paper portfolio must use USD")
    cash = book.get("cash")
    if isinstance(cash, bool) or not isinstance(cash, (int, float)) or not isfinite(cash) or cash < 0:
        raise ValueError("Paper cash must be finite and nonnegative")
    positions = book.get("positions", {})
    prices = {c["ticker"]: c["quote"]["close"] for c in packet["companies"]}
    for ticker, quantity in positions.items():
        if ticker not in prices:
            raise ValueError(f"Include held ticker {ticker} in the analysis before rebalancing")
        if type(quantity) is not int or quantity < 0:
            raise ValueError("Paper positions must be nonnegative whole shares")
    names = {v["investor"] for v in views}
    expected = {(ticker, investor) for ticker in prices for investor in names}
    if not expected or len(views) != len(expected) or {(v["ticker"], v["investor"]) for v in views} != expected:
        raise ValueError("Every requested analyst must return one valid opinion per ticker")
    signals = []
    for view in views:
        validate_answer({key: view[key] for key in ("signal", "confidence", "reasoning")})
        sign = {"bullish": 1, "neutral": 0, "bearish": -1}[view["signal"]]
        signals.append(Signal(model_name=view["investor"], ticker=view["ticker"], date=packet["retrieved_at_utc"][:10],
                              value=sign * view["confidence"] / 100, reasoning=view["reasoning"]))
    blend = blend_signals(signals, dict.fromkeys(names, 1.0), 1.0, mode="long_only",
                          investment_approaches=dict.fromkeys(names, "long_only"))
    bounded = apply_limits(blend.weights, RiskLimits(max_position_pct=max_position, max_gross_exposure=1.0))
    nav = cash + sum(prices[t] * q for t, q in positions.items())
    if not isfinite(nav) or nav <= 0:
        raise ValueError("Paper portfolio equity must be positive and finite")
    targets = {t: floor(nav * w / prices[t]) for t, w in bounded.weights.items()}
    orders = [{"ticker": t, "action": "buy" if targets[t] > positions.get(t, 0) else "sell",
               "quantity": abs(targets[t] - positions.get(t, 0)), "reference_close": prices[t],
               "reference_date": next(c["quote"]["date"] for c in packet["companies"] if c["ticker"] == t)}
              for t in sorted(targets) if targets[t] != positions.get(t, 0)]
    return {"mode": "paper_proposal_only", "currency": "USD", "reference_nav": nav,
            "convictions": blend.convictions, "target_weights": bounded.weights,
            "target_positions": targets, "proposed_orders": orders,
            "estimated_remaining_cash": nav - sum(targets[t] * prices[t] for t in targets),
            "risk_clamps": [c.model_dump() for c in bounded.clamps],
            "limitations": ["Long only, whole shares, no leverage; capped allocation stays in cash.",
                            "Reference closes are for a proposal, not guaranteed execution prices.",
                            "No orders are submitted and no broker account is connected."]}


def main() -> None:
    parser = argparse.ArgumentParser(prog="aihf", description="No extra cost: free latest data + ChatGPT-authenticated Codex.")
    parser.add_argument("--runtime-dir", type=Path, default=Path(".runtime"))
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check subscription login without a model call")
    run = commands.add_parser("run", help="종목 입력 → 무료 수집 → 분석 → 한국어 HTML 보고서")
    run.add_argument("--tickers", help="AAPL,MSFT; 생략하면 입력받습니다")
    run.add_argument("--packet", type=Path, help="새 수집 대신 저장한 최신 데이터 사용")
    run.add_argument("--max-calls", type=int, help="이번 실행의 새 Codex 호출 상한")
    run.add_argument("--open-report", action="store_true", help="완료 후 로컬 보고서를 브라우저에서 열기")
    settings = commands.add_parser("settings", help="관심 종목과 가상 자금·호출 상한 저장")
    settings.add_argument("--tickers")
    settings.add_argument("--cash", type=float)
    settings.add_argument("--max-position", type=float)
    settings.add_argument("--max-calls", type=int)
    settings.add_argument("--paper-book", type=Path)
    settings.add_argument("--clear-paper-book", action="store_true")
    render = commands.add_parser("report", help="저장된 분석 JSON을 HTML로 변환; AI 호출 없음")
    render.add_argument("--input", type=Path, required=True)
    render.add_argument("--previous", type=Path)
    render.add_argument("--out", type=Path)
    render.add_argument("--open-report", action="store_true")
    fetch = commands.add_parser("fetch", help="fetch free latest data; no AI calls")
    fetch.add_argument("--tickers", required=True)
    fetch.add_argument("--out", type=Path, default=Path(".runtime/packet.json"))
    analysis = commands.add_parser("analyze", help="analyze a saved packet and propose a paper portfolio")
    analysis.add_argument("--packet", type=Path, required=True)
    analysis.add_argument("--investors", default=",".join(INVESTORS))
    analysis.add_argument("--max-calls", type=int, default=5, help="max new Codex calls per run (default 5)")
    analysis.add_argument("--max-position", type=float, default=0.20)
    analysis.add_argument("--paper-book", type=Path, help="JSON {currency: USD, cash: number, positions: {ticker: whole shares}}")
    analysis.add_argument("--out", type=Path, default=Path(".runtime/report.json"))
    args = parser.parse_args()
    if args.command is None:
        from hedge_fund.no_cost.workflow import interactive_run

        try:
            interactive_run(args.runtime_dir)
        except (RuntimeError, ValueError, OSError, KeyError, TypeError, EOFError) as exc:
            print(f"중단: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    try:
        if args.command in ("run", "settings", "report"):
            from hedge_fund.no_cost.workflow import dispatch

            dispatch(args)
        elif args.command == "doctor":
            client = CodexSubscriptionLLM(runtime_dir=args.runtime_dir)
            client.check_auth()
            client.check_allowance()
            print("OK: ChatGPT login, included quota below 95%, zero credit balance. Paid APIs blocked. No model or data call made.")
        elif args.command == "fetch":
            packet = fetch_packet(symbols(args.tickers), args.runtime_dir)
            validate_packet(packet)
            save_json(args.out, packet)
            print(f"Saved free latest-data packet: {args.out}")
        else:
            packet = read_json(args.packet)
            validate_packet(packet)
            investors = args.investors.split(",")
            if not investors or len(investors) != len(set(investors)) or any(i not in INVESTORS for i in investors):
                raise ValueError(f"Choose unique investors from {','.join(INVESTORS)}")
            total = estimate_calls(packet, investors, CodexSubscriptionLLM.model, args.runtime_dir)
            if total > args.max_calls:
                raise ValueError(f"This requests {total} new calls. Limit is {args.max_calls}; reduce tickers/investors or explicitly raise --max-calls.")
            book = read_json(args.paper_book) if args.paper_book else {"currency": "USD", "cash": 10000.0, "positions": {}}
            placeholders = [{"ticker": c["ticker"], "investor": i, "signal": "neutral", "confidence": 0,
                             "reasoning": "preflight"} for c in packet["companies"] for i in investors]
            portfolio_proposal(packet, placeholders, book, args.max_position)
            llm = CodexSubscriptionLLM(runtime_dir=args.runtime_dir, max_calls=args.max_calls)
            views = analyze(packet, investors, llm, args.runtime_dir)
            result = {"schema_version": 1, "data": packet, "opinions": views,
                      "new_codex_calls": llm.calls, "ai_execution": "ChatGPT-authenticated Codex CLI",
                      "paper_book": book,
                      "portfolio": portfolio_proposal(packet, views, book, args.max_position)}
            save_json(args.out, result)
            print(f"Saved analysis and paper proposal: {args.out} (new Codex calls: {llm.calls})")
    except (RuntimeError, ValueError, OSError, KeyError, TypeError, EOFError) as exc:
        label = "중단" if args.command in ("run", "settings", "report") else "Stopped"
        print(f"{label}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
