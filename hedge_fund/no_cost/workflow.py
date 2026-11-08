"""One-run workflow and local preferences; no hosting or extra model calls."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
import webbrowser

from hedge_fund.llm.codex_cli import CodexSubscriptionLLM
from hedge_fund.no_cost.cli import analyze, estimate_calls, portfolio_proposal, read_json, save_json, validate_packet
from hedge_fund.no_cost.data import fetch_packet, symbols
from hedge_fund.no_cost.report import render_report
from hedge_fund.no_cost.settings import load_settings, validate_settings


def previous_report(runtime_dir: Path) -> dict | None:
    for path in sorted((runtime_dir / "runs").glob("*/report.json"), reverse=True)[:100]:
        try:
            result = read_json(path)
            if result.get("schema_version") == 1 and result.get("portfolio", {}).get("mode") == "paper_proposal_only":
                return result
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return None


def write_html(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def open_report(path: Path) -> None:
    if not webbrowser.open(path.resolve().as_uri()):
        print(f"브라우저를 열지 못했습니다. 보고서 파일을 직접 여세요: {path.resolve()}")


def run_once(runtime_dir: Path, settings: dict, *, packet_path: Path | None = None,
             collector=None, client_factory=None) -> tuple[dict, Path]:
    settings = validate_settings(dict(settings))
    runtime_dir = runtime_dir.resolve()
    collector = collector or fetch_packet
    client_factory = client_factory or CodexSubscriptionLLM
    previous = previous_report(runtime_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:6]
    directory = runtime_dir / "runs" / run_id
    print("[1/3] 저장된 최신 데이터를 확인합니다." if packet_path else "[1/3] 무료 주가·연간 재무자료를 수집합니다.")
    packet = read_json(packet_path) if packet_path else collector(settings["watchlist"], runtime_dir)
    validate_packet(packet)
    if [c["ticker"] for c in packet["companies"]] != settings["watchlist"]:
        raise ValueError("저장된 데이터의 종목과 이번 관심 종목이 다릅니다.")
    save_json(directory / "packet.json", packet)
    book = (read_json(Path(settings["paper_book"])) if settings["paper_book"]
            else {"currency": "USD", "cash": settings["paper_cash"], "positions": {}})
    # Invalid portfolios must fail before consuming the subscription allowance.
    placeholders = [{"ticker": c["ticker"], "investor": i, "signal": "neutral", "confidence": 0,
                     "reasoning": "preflight"} for c in packet["companies"] for i in settings["investors"]]
    portfolio_proposal(packet, placeholders, book, settings["max_position"])
    client = client_factory(runtime_dir=runtime_dir, max_calls=settings["max_calls"])
    required = estimate_calls(packet, settings["investors"], client.model, runtime_dir)
    total = len(packet["companies"]) * len(settings["investors"])
    if required > settings["max_calls"]:
        raise ValueError(
            f"새 AI 분석 {required}회가 필요하지만 저장된 상한은 {settings['max_calls']}회입니다. "
            f"종목을 줄이거나 run --max-calls {required}처럼 이번 상한을 지정하세요. AI 호출은 시작하지 않았습니다."
        )
    print(f"[2/3] 투자자별 분석: 새 분석 {required}개 / 저장된 분석 재사용 {total - required}개")
    views = analyze(packet, settings["investors"], client, runtime_dir)
    result = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "data": packet, "opinions": views, "new_codex_calls": client.calls,
              "ai_execution": "ChatGPT-authenticated Codex CLI", "paper_book": book,
              "portfolio": portfolio_proposal(packet, views, book, settings["max_position"])}
    print("[3/3] 한국어 보고서와 이전 분석 비교를 만듭니다.")
    html = render_report(result, previous)
    # Only complete reports appear in history; failed runs keep their packet/cache for diagnosis.
    write_html(directory / "report.html", html)
    save_json(directory / "report.json", result)
    return result, directory / "report.html"


def interactive_run(runtime_dir: Path, *, open_browser: bool = True, max_calls: int | None = None,
                    tickers: str | None = None, packet_path: Path | None = None):
    settings = load_settings(runtime_dir)
    if tickers is None:
        print("\nAI 투자위원회 · 무료 데이터 + Codex 구독")
        funding = f"가상 장부 사용: {Path(settings['paper_book']).name}" if settings["paper_book"] else f"가상 자금 ${settings['paper_cash']:,.0f}"
        call_limit = max_calls if max_calls is not None else settings["max_calls"]
        print(f"{funding} · 종목당 상한 {settings['max_position']:.0%} · 새 AI 분석 상한 {call_limit}회")
        selected = input(f"분석할 종목 [{', '.join(settings['watchlist'])}] (Enter: 그대로 사용): ").strip()
        tickers = selected or ",".join(settings["watchlist"])
    settings["watchlist"] = symbols(tickers)
    saved = validate_settings(dict(settings))
    # Keep last-used watchlist; a one-run call-limit override is not silently persisted.
    if max_calls is not None:
        settings = {**settings, "max_calls": max_calls}
        validate_settings(settings)
    save_json(runtime_dir / "settings.json", saved)
    result, path = run_once(runtime_dir, settings, packet_path=packet_path)
    print(f"\n완료: {path.resolve()}\n이번 새 AI 분석: {result['new_codex_calls']}회")
    if open_browser:
        open_report(path)
    return result, path


def dispatch(args) -> None:
    if args.command == "run":
        interactive_run(args.runtime_dir, open_browser=args.open_report,
                        max_calls=args.max_calls, tickers=args.tickers, packet_path=args.packet)
    elif args.command == "settings":
        settings = load_settings(args.runtime_dir)
        if args.paper_book is not None and args.clear_paper_book:
            raise ValueError("--paper-book과 --clear-paper-book은 함께 쓸 수 없습니다.")
        changed = False
        for arg, key in (("cash", "paper_cash"), ("max_position", "max_position"), ("max_calls", "max_calls")):
            if getattr(args, arg) is not None:
                settings[key] = getattr(args, arg)
                changed = True
        if args.tickers is not None:
            settings["watchlist"] = symbols(args.tickers)
            changed = True
        if args.paper_book is not None:
            if not args.paper_book.is_file():
                raise ValueError("가상 포트폴리오 파일을 찾을 수 없습니다.")
            settings["paper_book"] = str(args.paper_book.resolve())
            changed = True
        elif args.clear_paper_book:
            settings["paper_book"] = None
            changed = True
        validate_settings(settings)
        if changed:
            save_json(args.runtime_dir / "settings.json", settings)
            print("설정을 로컬에 저장했습니다. API나 AI는 호출하지 않았습니다.")
        print(json.dumps(settings, ensure_ascii=False, indent=2))
    else:
        result = read_json(args.input)
        previous = read_json(args.previous) if args.previous else None
        path = args.out or args.input.with_suffix(".html")
        if path.resolve() == args.input.resolve() or (args.previous and path.resolve() == args.previous.resolve()):
            raise ValueError("보고서 출력 경로는 원본 JSON 파일과 달라야 합니다.")
        if result.get("mode") == "meta_forecast":
            from hedge_fund.no_cost.meta_report import render_meta_report

            write_html(path, render_meta_report(result))
        else:
            write_html(path, render_report(result, previous))
        print(f"보고서 생성 완료 (새 AI 호출 없음): {path.resolve()}")
        if args.open_report:
            open_report(path)
