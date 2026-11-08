"""Self-contained Korean reports. Rendering never calls a model or the network."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
import math
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from hedge_fund.no_cost.settings import INVESTOR_NAMES

LABELS = {"bullish": "강세", "bearish": "약세", "neutral": "중립"}
STYLES = """
:root{color-scheme:light;--ink:#172838;--muted:#607181;--line:#dbe3e8;--paper:#f4f6f7;--green:#157451;--red:#b74c42;--blue:#365f93}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 'Malgun Gothic','Apple SD Gothic Neo',sans-serif}
a{color:var(--blue);text-underline-offset:3px}header{background:#122a3a;color:#fff;padding:44px max(24px,calc((100vw - 1120px)/2)) 36px}
header p{color:#c9d7df;margin:8px 0 0}h1{font-size:32px;line-height:1.3;margin:12px 0}h2{font-size:22px;margin:0 0 18px}h3{font-size:18px;margin:0}
.eyebrow{font-size:12px;font-weight:700;letter-spacing:2px;color:#a5c8bc}.wrap{max-width:1168px;margin:auto;padding:26px 24px 48px}
.notice{border-left:4px solid #cca34e;padding:14px 18px;background:#fff8e9;border-radius:4px;margin:0 0 22px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px}.stat,.section{background:#fff;border:1px solid var(--line);border-radius:12px}
.stat{padding:18px}.stat strong{display:block;font-size:26px;margin:4px 0}.muted,small{color:var(--muted)}.section{padding:24px;margin:0 0 22px}
.topline{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.ticker{font-size:13px;font-weight:700;color:var(--muted);letter-spacing:1px}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:700;background:#edf2f7;color:var(--blue)}
.bullish{color:var(--green);background:#eaf5ee}.bearish{color:var(--red);background:#fff0ec}.neutral{color:var(--blue);background:#edf2f7}
.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:right;padding:12px 10px;border-bottom:1px solid #e7ecef;font-variant-numeric:tabular-nums}
th{font-size:12px;color:var(--muted);font-weight:600;background:#f8fafb}th:first-child,td:first-child{text-align:left}tr:last-child td{border-bottom:0}
.company-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}.company-metrics div{background:#f6f8fa;padding:12px;border-radius:8px}.company-metrics strong{display:block;font-size:18px}
.analysts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.analyst{border:1px solid var(--line);border-radius:9px;padding:16px}.analyst .topline{gap:8px}
.thesis{white-space:pre-line;overflow-wrap:anywhere;font-size:14px;margin:14px 0 0}.confidence{height:4px;background:#e9eef1;border-radius:3px;margin-top:9px;overflow:hidden}.confidence span{display:block;height:100%;background:#708d9e}
.changes{font-size:12px;color:var(--muted);margin:8px 0 0}.votes{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.subtle{font-size:13px;color:var(--muted)}
nav{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}nav a{padding:6px 12px;background:white;border:1px solid var(--line);border-radius:99px;text-decoration:none}
details{margin-top:18px}summary{cursor:pointer;color:var(--muted);font-size:13px}.sources{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;margin-top:16px}
.allocation{display:flex;height:12px;background:#e9eef1;border-radius:8px;overflow:hidden;margin:16px 0 8px}.allocation span{background:#3f7f70}
.empty{padding:16px;border:1px dashed var(--line);border-radius:8px;color:var(--muted)}footer{font-size:12px;color:var(--muted);padding:0 4px;overflow-wrap:anywhere}
@media(max-width:740px){header{padding:30px 20px}h1{font-size:25px}.wrap{padding:18px 14px}.stats,.company-metrics{grid-template-columns:repeat(2,1fr)}.analysts{grid-template-columns:1fr}.section{padding:18px}.stat strong{font-size:23px}}
@media print{body{background:white;font-size:11px}header{background:white;color:var(--ink);padding:12px}header p,.eyebrow{color:var(--muted)}.wrap{max-width:none;padding:0}.section{break-inside:avoid;border-radius:0}nav{display:none}.analyst{break-inside:avoid}.analysts{grid-template-columns:1fr}details{display:block}}
"""


def text(value) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def amount(value, currency="USD") -> str:
    n = numeric(value)
    if n is None:
        return "—"
    prefix = "$" if currency == "USD" else text(currency) + " "
    return prefix + f"{n:,.2f}"


def percent(value) -> str:
    n = numeric(value)
    return f"{n * 100:.1f}%" if n is not None else "—"


def compact_amount(value, currency="USD") -> str:
    n = numeric(value)
    if n is None:
        return "—"
    unit = "달러" if currency == "USD" else text(currency)
    for threshold, label in ((1e12, "조"), (1e8, "억"), (1e4, "만")):
        if abs(n) >= threshold:
            return f"{n / threshold:,.2f}{label} {unit}"
    return amount(n, currency)


def local_time(value) -> str:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return text(value)
        return stamp.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    except (ValueError, TypeError):
        return text(value)


def changes(result: dict, previous: dict | None) -> list[dict]:
    old = {(v["ticker"], v["investor"]): v for v in (previous or {}).get("opinions", [])}
    rows = []
    for view in result["opinions"]:
        before = old.get((view["ticker"], view["investor"]))
        if before is not None:
            rows.append({"ticker": view["ticker"], "investor": view["investor"],
                         "before_signal": before["signal"], "signal": view["signal"],
                         "confidence_delta": view["confidence"] - before["confidence"],
                         "signal_changed": view["signal"] != before["signal"]})
    return rows


def render_report(result: dict, previous: dict | None = None) -> str:
    if not isinstance(result, dict) or result.get("schema_version") != 1 or not result.get("data", {}).get("companies"):
        raise ValueError("분석 결과 JSON 형식이 올바르지 않습니다.")
    companies = result["data"]["companies"]
    views = result["opinions"]
    portfolio = result["portfolio"]
    if portfolio.get("mode") != "paper_proposal_only":
        raise ValueError("가상 포트폴리오 조정안만 보고서로 만들 수 있습니다.")
    previous_weights = (previous or {}).get("portfolio", {}).get("target_weights", {})
    delta_rows = changes(result, previous)
    delta_lookup = {(r["ticker"], r["investor"]): r for r in delta_rows}
    changed_count = sum(r["signal_changed"] for r in delta_rows)
    cache_count = sum(bool(v.get("cached")) for v in views)
    generated = result.get("generated_at_utc", result["data"].get("retrieved_at_utc"))
    nav = numeric(portfolio["reference_nav"])
    cash = numeric(portfolio["estimated_remaining_cash"])
    invested_pct = min(100, max(0, (1 - cash / nav) * 100)) if nav and cash is not None else 0
    title = ", ".join(c["ticker"] for c in companies)
    nav_links = "".join(f'<a href="#company-{i}">{text(c["ticker"])}</a>' for i, c in enumerate(companies))
    body = [f'''<header><div class="eyebrow">AI INVESTMENT COMMITTEE</div>
        <h1>{text(title)} 투자위원회 보고서</h1>
        <p>생성 {local_time(generated)} · 무료 데이터 + Codex 구독 분석</p></header>
        <main class="wrap"><div class="notice"><strong>{"테스트 데이터 · 실제 시장자료 아님. " if result['data'].get('synthetic') else ""}가상 포트폴리오 조정안</strong>입니다. 실제 주문은 전송하지 않습니다.
        AI의 확신도는 스스로 표현한 판단 강도이며, 정답 확률이나 수익 확률이 아닙니다.</div>
        <div class="stats"><div class="stat"><small>분석 종목 / 관점</small><strong>{len(companies)} / {len(views)}</strong><small>종목별 의견을 각각 확인하세요</small></div>
        <div class="stat"><small>이번 새 AI 분석</small><strong>{text(result.get("new_codex_calls", "—"))}회</strong><small>캐시 재사용 {cache_count}개</small></div>
        <div class="stat"><small>가상 자산 평가액</small><strong>{amount(nav)}</strong><small>보고서의 종가를 기준으로 계산</small></div>
        <div class="stat"><small>이전 보고서 대비 방향 변경</small><strong>{changed_count if delta_rows else "—"}</strong><small>{len(delta_rows)}개 의견 비교 가능</small></div></div>
        <nav>{nav_links}<a href="#portfolio">포트폴리오 조정안</a></nav>''']
    for index, company in enumerate(companies):
        ticker = company["ticker"]
        company_views = [v for v in views if v["ticker"] == ticker]
        counts = Counter(v["signal"] for v in company_views)
        history = company.get("annual_history", [])
        annual = history[0] if history else {}
        weight = portfolio.get("target_weights", {}).get(ticker, 0)
        previous_weight = previous_weights.get(ticker)
        weight_change = (f'이전 목표 {percent(previous_weight)} → 이번 {percent(weight)}'
                         if previous_weight is not None else "비교할 이전 목표 비중 없음")
        clamp = next((c for c in portfolio.get("risk_clamps", [])
                      if c.get("ticker") == ticker and c.get("limit") == "max_position_pct"), None)
        allocation_reason = (f'의견을 합산한 뒤 종목당 상한 {percent(clamp["after"])}을 적용했습니다.' if clamp
                             else "양수인 종합 의견을 다른 분석 종목과 비교해 비중을 계산했습니다." if weight > 0
                             else "종합 의견이 양수가 아니어서 목표 보유 비중은 0%입니다.")
        financial_currency = company.get("financial_currency") or "통화 미확인"
        body.append(f'''<section class="section" id="company-{index}"><div class="topline"><div>
            <div class="ticker">{text(ticker)} · {text(company.get("sector"))}</div><h2>{text(company.get("name", ticker))}</h2></div>
            <div><span class="pill">목표 비중 {percent(weight)}</span><div class="changes">{text(weight_change)}</div></div></div>
            <p class="subtle">종가 {amount(company["quote"]["close"], company["quote"].get("currency"))} · 주가 기준일 {text(company["quote"]["date"])}
            · 연간 재무 결산일 {text(annual.get("report_period"))}</p>
            <div class="votes"><span class="pill bullish">강세 {counts['bullish']}</span><span class="pill neutral">중립 {counts['neutral']}</span>
            <span class="pill bearish">약세 {counts['bearish']}</span></div>
            <p class="subtle">{text(allocation_reason)}</p>
            <div class="company-metrics"><div><small>연간 매출</small><strong>{compact_amount(annual.get("revenue"), financial_currency)}</strong></div>
            <div><small>연간 순이익</small><strong>{compact_amount(annual.get("net_income"), financial_currency)}</strong></div>
            <div><small>매출 전년 대비</small><strong>{percent(annual.get("revenue_growth_yoy"))}</strong></div>
            <div><small>연간 잉여현금흐름</small><strong>{compact_amount(annual.get("free_cash_flow"), financial_currency)}</strong></div></div><div class="analysts">''')
        for view in company_views:
            signal = view["signal"]
            if signal not in LABELS:
                raise ValueError("알 수 없는 투자 의견입니다.")
            confidence = numeric(view["confidence"])
            if confidence is None or not 0 <= confidence <= 100:
                raise ValueError("확신도 값이 올바르지 않습니다.")
            difference = delta_lookup.get((ticker, view["investor"]))
            change_text = (f'{LABELS[difference["before_signal"]]} → {LABELS[signal]} · '
                           f'확신도 {difference["confidence_delta"]:+g}p' if difference else "이전 보고서의 같은 관점과 비교할 수 없습니다.")
            body.append(f'''<article class="analyst"><div class="topline"><h3>{text(INVESTOR_NAMES.get(view["investor"], view["investor"]))}</h3>
                <span class="pill {signal}">{LABELS[signal]} · {confidence:g}/100</span></div>
                <div class="confidence"><span style="width:{confidence:g}%"></span></div>
                <p class="changes">{text(change_text)} · {"저장된 분석 재사용" if view.get("cached") else "새로 분석"}</p>
                <p class="thesis">{text(view["reasoning"])}</p></article>''')
        body.append('</div><details><summary>연간 재무 추이 보기</summary><div class="table-scroll"><table><thead><tr>'
                    '<th>결산일</th><th>매출</th><th>순이익</th><th>순이익률</th><th>희석 EPS</th></tr></thead><tbody>')
        for row in history:
            eps = numeric(row.get("eps_diluted"))
            eps_text = f"{eps:,.2f}" if eps is not None else "—"
            body.append(f'<tr><td>{text(row.get("report_period"))}</td><td>{amount(row.get("revenue"), financial_currency)}</td>'
                        f'<td>{amount(row.get("net_income"), financial_currency)}</td><td>{percent(row.get("net_margin"))}</td><td>{eps_text}</td></tr>')
        body.append('</tbody></table></div></details><div class="sources">')
        for i, source in enumerate(company.get("sources", []), start=1):
            try:
                parsed = urlsplit(str(source))
            except ValueError:
                continue
            if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
                body.append(f'<a href="{text(source)}" target="_blank" rel="noopener noreferrer">데이터 출처 {i}</a>')
        body.append('</div></section>')
    body.append(f'''<section class="section" id="portfolio"><h2>포트폴리오 조정안</h2>
        <p class="subtle">주가 기준일의 종가로 계산한 가상 조정안입니다. 기존 보유량은 입력한 가상 장부를 사용합니다.</p>
        <div class="allocation"><span style="width:{invested_pct:.3f}%"></span></div>
        <p class="subtle">주식 {invested_pct:.1f}% · 예상 잔여 현금 {amount(cash)}</p>
        <div class="table-scroll"><table><thead><tr><th>종목</th><th>현재 가상 보유</th><th>목표 주식 수</th><th>목표 비중</th><th>조정안</th></tr></thead><tbody>''')
    positions = result.get("paper_book", {}).get("positions")
    orders = {o["ticker"]: o for o in portfolio.get("proposed_orders", [])}
    for ticker, quantity in portfolio.get("target_positions", {}).items():
        order = orders.get(ticker)
        proposal = f'{"매수" if order["action"] == "buy" else "매도"} {order["quantity"]}주' if order else "변경 없음"
        current = positions.get(ticker, 0) if positions is not None else "기록 없음"
        body.append(f'<tr><td>{text(ticker)}</td><td>{text(current)}</td><td>{text(quantity)}</td>'
                    f'<td>{percent(portfolio["target_weights"].get(ticker))}</td><td>{text(proposal)}</td></tr>')
    body.append('''</tbody></table></div><p class="subtle">공매도·레버리지를 사용하지 않습니다. 상한으로 줄인 비중은 현금으로 남습니다.
        수수료·배당·실제 체결 가격은 이 조정안에 반영되지 않았습니다.</p></section>
        <section class="section"><h2>데이터와 판단의 범위</h2><ul>
        <li>최신 종가와 연간 재무자료를 사용합니다. 분기 TTM 자료나 당시 공시일을 보장하는 과거 데이터가 아닙니다.</li>
        <li>누락된 숫자는 —로 표시합니다. 주당 지표는 주식분할의 영향을 추가 확인해야 합니다.</li>
        <li>뉴스·금리·거시 환경·정성적 해자 조사는 현재 입력에 포함되지 않습니다.</li>
        <li>투자자 이름은 공개 투자 철학을 모사한 역할입니다. 같은 기반 AI의 의견들이므로 독립된 다섯 전문가의 검증을 뜻하지 않습니다.</li>
        </ul></section>''')
    if previous:
        body.append(f'<footer>비교 기준: 이전 보고서 생성 {local_time(previous.get("generated_at_utc", previous.get("data", {}).get("retrieved_at_utc")))} · '
                    '같은 종목·같은 관점만 비교했습니다. 의견 변경 횟수는 성과 지표가 아닙니다.</footer>')
    body.append('<footer>분석·비중 계산은 기존 구독과 무료 데이터로 실행합니다. 이 HTML 파일은 외부 스크립트나 글꼴을 불러오지 않습니다.</footer></main>')
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
            f'<title>{text(title)} · 투자위원회 보고서</title><style>{STYLES}</style></head><body>' + "".join(body) + '</body></html>')
