"""Offline Korean macro portfolio report; no network or inference."""

from urllib.parse import urlsplit

from hedge_fund.no_cost.report import STYLES, amount, local_time, percent, text
from hedge_fund.no_cost.settings import INVESTOR_NAMES


def bullets(items):
    return "<ul>" + "".join(f"<li>{text(item)}</li>" for item in items) + "</ul>"


def table(headings, rows):
    return ('<div class="table-scroll"><table><thead><tr>' +
            ''.join(f'<th>{text(h)}</th>' for h in headings) + '</tr></thead><tbody>' +
            ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows) +
            '</tbody></table></div>')


def source(url):
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname not in ("fred.stlouisfed.org", "finance.yahoo.com"):
        return "출처 링크 없음"
    return f'<a href="{text(url)}" target="_blank" rel="noopener noreferrer">출처</a>'


def render_meta_report(result):
    data, plan, reviews, portfolio = (result[k] for k in ("data", "scenario_draft", "reviews", "portfolio"))
    assets = {a["ticker"]: a for a in data["assets"]}
    parts = [f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>거시 시나리오 · AI 포트폴리오</title><style>{STYLES}
.scenario{{padding:20px 0;border-top:1px solid var(--line)}}.scenario:first-of-type{{border-top:0}}
.reason{{text-align:left;white-space:normal;min-width:230px}}li{{margin:6px 0}}td small{{display:block}}
</style></head><body><header><div class="eyebrow">MACRO · FIVE PERSPECTIVES</div>
<h1>6~12개월 거시 시나리오 포트폴리오</h1>
<p>미국 주식 · ETF · 현금 | {text(local_time(result['generated_at_utc']))}</p></header>
<main class="wrap"><div class="notice">시나리오 확률과 수익률 범위는 AI의 주관적 작업 가정입니다. 검증된 예측 정확도나 수익 보장이 아닙니다.
다섯 역할은 같은 기반 모델의 관점 모사이며 실제 투자자들의 의견이 아닙니다. 실제 주문은 전송하지 않습니다.</div>
<div class="stats"><div class="stat">가상 기준 자금<strong>{amount(portfolio['capital'])}</strong></div>
<div class="stat">정수 주식 후 현금<strong>{percent(portfolio['whole_share_weights']['CASH'])}</strong></div>
<div class="stat">과거 연환산 변동성<strong>{percent(portfolio['historical_volatility'])}</strong></div>
<div class="stat">새 AI 분석 / 재사용<strong>{text(result['new_codex_calls'])} / {text(result['cache_hits'])}</strong></div></div>
<nav><a href="#scenarios">거시 시나리오</a><a href="#portfolio">목표 비중</a><a href="#reviews">다섯 관점</a><a href="#data">관측 자료</a></nav>
{'<div class="notice">합성 테스트 데이터 보고서입니다. 실제 투자 자료가 아닙니다.</div>' if data.get('synthetic') else ''}
<section id="scenarios" class="section"><h2>시장이 어느 방향으로 갈 수 있는가</h2>
<p>{text(plan['market_regime'])}</p><h3>향후 6개월 점검 항목</h3>{bullets(plan['six_month_checkpoints'])}''']
    for scenario in plan["scenarios"]:
        sid, f = scenario["id"], scenario["forecasts_12m"]
        rows = []
        for label, key, decimal in (("CPI 전년 대비", "cpi_yoy", False), ("연방기금금리", "fed_funds", False),
                                    ("실업률", "unemployment", False), ("SPY 총수익률", "spy_return", True)):
            fmt = percent if decimal else lambda v: f"{v:.1f}%"
            rows.append([text(label), fmt(f[key + "_low"]) + " ~ " + fmt(f[key + "_high"])])
        parts.append(f'<article class="scenario"><h3>{text(sid)} · {text(scenario["name"])}</h3>'
                     f'<p>초안 확률 {percent(scenario["probability"])} → 검토 평균 {percent(portfolio["scenario_probabilities"][sid])}</p>'
                     f'<p>{text(scenario["thesis"])}</p><p class="subtle">관측 근거: {text(", ".join(scenario["evidence_ids"]))}</p>'
                     + table(["12개월 시점의 조건부 가정", "범위"], rows) + '<p>시나리오를 재검토할 조건</p>'
                     + bullets(scenario["invalidation_triggers"]) + '</article>')
    parts.append('</section><section id="portfolio" class="section"><h2>조건별 배분을 종합한 가상 포트폴리오</h2>'
                 '<p class="subtle">각 관점의 ‘시나리오 확률 × 해당 시나리오 배분’을 먼저 더한 뒤, 다섯 관점을 동일 비중으로 평균합니다. '
                 '이후 한도를 적용하고 초과분은 현금으로 둡니다. 현금 수익률 가정은 0%입니다.</p>')
    rows = []
    for ticker in [*assets, "CASH"]:
        a = assets.get(ticker)
        rows.append([text(ticker), "ETF" if a and a["type"] == "etf" else "개별 주식" if a else "현금",
                     percent(portfolio["raw_weights"][ticker]), percent(portfolio["target_weights"][ticker]),
                     percent(portfolio["whole_share_weights"][ticker]), text(portfolio["target_shares"].get(ticker, "—")),
                     amount(a["close"]) if a else "—"])
    parts.append(table(["후보", "유형", "검토 종합", "한도 적용", "정수 주식 후", "목표 주수", "기준 종가"], rows))
    parts.append(f'<p>남는 현금: <strong>{amount(portfolio["remaining_cash"])}</strong>. 위 주수는 가상 총자금을 새로 배분하는 목표 수량입니다.</p>'
                 '<p class="subtle">연구용 기본 한도: 현금 최소 15%, ETF 포함 주식 합계 최대 60%, 개별 주식 합계 15%, '
                 '개별 주식당 5%, ETF당 20%. 과거 공통 표본의 가중 변동성 합계 12%를 예산으로 사용합니다. '
                 '과거 변동성은 미래 손실 한도가 아니며, 개인 위험 성향에 맞춘 수치도 아닙니다. ETF 내부 보유 종목까지 투시한 중복 계산은 포함하지 않습니다.</p>')
    parts.append(bullets([f'{a["rule"]}: {percent(a["before"])} → {percent(a["after"])}' for a in portfolio["risk_adjustments"]])
                 if portfolio["risk_adjustments"] else '<p>추가 비중 축소 없이 기본 한도를 충족했습니다.</p>')
    parts.append('</section><section id="reviews" class="section"><h2>다섯 관점의 반박과 조건별 배분</h2>')
    for investor, review in reviews.items():
        parts.append(f'<article class="scenario"><h3>{text(INVESTOR_NAMES[investor])}</h3><p>{text(review["summary"])}</p>'
                     '<p>반대 논거</p>' + bullets(review["counterarguments"]))
        parts.append(table(["시나리오", "확률", *assets, "CASH"], [[text(sid), percent(review["scenario_probabilities"][sid]),
                     *[percent(review["scenario_allocations"][sid][t]) for t in [*assets, "CASH"]]] for sid in ("S1", "S2", "S3")]))
        parts.append('<details><summary>자산별 판단 근거</summary>' + ''.join(
            f'<p><strong>{text(t)}</strong> · {text(reason)}</p>' for t, reason in review["asset_reasons"].items()) + '</details>'
            '<details><summary>판단을 바꿀 조건</summary>' + bullets(review["invalidation_triggers"]) + '</details></article>')
    parts.append('</section><section id="data" class="section"><h2>관측 사실과 출처</h2>'
                 f'<p class="subtle">수집: {text(local_time(data["retrieved_at_utc"]))}. 아래 날짜는 관측 기준일이며 발표일과 다릅니다. '
                 'FRED의 최신 수정치를 사용하므로 과거 시점 백테스트에 사용할 수 없습니다.</p>')
    rows = [[text(o["id"]), text(o["name"]), text(o["observation_date"]), f'{o["value"]:,.3f}', text(o["unit"]),
             percent(o.get("yoy_change")), source(o["source"])] for o in data["macro"]]
    parts.append(table(["ID", "지표", "관측일", "수준", "단위", "수준의 전년 대비 변화", "출처"], rows))
    parts.append('<h3 style="margin-top:24px">제한된 연구 후보군</h3><p class="subtle">전체 미국 시장을 검색한 결과가 아닙니다. '
                 '3·6·12개월 수익률은 조정 가격 기준 과거 수익률입니다.</p>')
    rows = [[text(t), text(a["name"]), text(a["quote_date"]), amount(a["close"]),
             *[percent(a["returns"].get(n)) for n in ("63", "126", "252")], source(a["source"])] for t, a in assets.items()]
    parts.append(table(["후보", "이름", "종가 기준일", "종가", "약 3개월", "약 6개월", "약 12개월", "출처"], rows))
    parts.append(bullets(data["limitations"]) + f'<p class="subtle">공통 위험 표본 {text(data["risk_sample_days"])}거래일. '
                 '개별 기업의 최신 분기 공시·뉴스·ETF 구성 내역은 별도로 검증하지 않았습니다.</p></section>'
                 '<footer>기존 구독의 포함 사용량과 무료 공개 자료로 생성했습니다. 한도 소진 시 중단하며 유료 API로 전환하지 않습니다.</footer></main></body></html>')
    return ''.join(parts)
