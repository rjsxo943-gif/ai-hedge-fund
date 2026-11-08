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


def render_candidates(result):
    from hedge_fund.no_cost.meta import scenario_candidates

    candidates = scenario_candidates(result)
    assets = {a["ticker"]: a for a in result["data"]["assets"]}
    tied = sum(c["leading"] for c in candidates) > 1
    parts = ['<section id="candidates" class="candidate-section"><div class="topline"><h2>먼저 볼 포트폴리오 후보</h2>'
             '<span class="pill">6~12개월 · 가상 배분안</span></div>'
             '<p class="subtle">다섯 관점의 평균 시나리오 확률순입니다. 각 배분안은 해당 시나리오를 전제로 하며, '
             '모든 후보에 동일한 위험 한도를 적용했습니다.</p><div class="candidate-grid">']
    groups = (("equity", "주식", "#247b62"), ("bond", "국채", "#577bba"),
              ("gold", "금", "#c49a44"), ("cash", "현금", "#a5b3bd"))
    for candidate in candidates:
        p = candidate["portfolio"]
        weights = p["target_weights"]
        group_weights = {key: sum(weights[t] for t, a in assets.items() if a["asset_class"] == key)
                         for key in ("equity", "bond", "gold")}
        group_weights["cash"] = weights["CASH"]
        badge = ("공동 우선 검토" if tied else "가장 유력한 시나리오 후보") if candidate["leading"] else f'{candidate["rank"]}순위 시나리오 후보'
        parts.append(f'<article class="candidate-card{" leading" if candidate["leading"] else ""}" data-scenario="{text(candidate["id"])}">'
                     f'<span class="candidate-badge">{badge}</span><h3>{text(candidate["name"])}</h3>'
                     f'<div class="candidate-probability">{percent(candidate["probability"])}<small>평균 시나리오 확률</small></div>'
                     '<div class="candidate-bar" aria-hidden="true">' + ''.join(
                         f'<span style="width:{group_weights[key] * 100:.6f}%;background:{color}"></span>' for key, _, color in groups) +
                     '</div><dl class="candidate-groups">' + ''.join(
                         f'<div><dt><i style="background:{color}"></i>{label}</dt><dd>{percent(group_weights[key])}</dd></div>'
                         for key, label, color in groups) + '</dl><p class="candidate-label">주요 자산 · 목표 비중</p><div class="candidate-holdings">')
        holdings = sorted(((t, weights[t]) for t in assets if weights[t] > 1e-10), key=lambda row: (-row[1], row[0]))
        for ticker, weight in holdings[:4]:
            parts.append(f'<span><strong>{text(ticker)}</strong> {percent(weight)}</span>')
        if not holdings:
            parts.append('<span>현금 중심 배분</span>')
        if len(holdings) > 4:
            parts.append(f'<span class="muted">외 {len(holdings) - 4}개</span>')
        parts.append(f'</div><p class="candidate-footnote">정수 주식 후 현금 {percent(p["whole_share_weights"]["CASH"])} '
                     f'· 과거 변동성 {percent(p["historical_volatility"])}</p>'
                     f'<a class="candidate-link" href="#candidate-comparison">전체 비중·주수 비교 →</a></article>')
    parts.append('</div><p class="subtle">카드의 목표 비중은 정수 주식 적용 전입니다. 유력도는 주관적 시나리오 확률을 뜻하며, '
                 '포트폴리오의 수익 확률을 측정한 값이 아닙니다.</p>'
                 '<details id="candidate-comparison"><summary>후보 3개의 전체 비중·목표 주수 비교</summary>')
    rows = []
    for ticker in [*assets, "CASH"]:
        cells = [text(ticker)]
        for c in candidates:
            p = c["portfolio"]
            shares = f'{p["target_shares"][ticker]}주' if ticker != "CASH" else amount(p["remaining_cash"])
            cells.append(f'{percent(p["target_weights"][ticker])}<small>정수 배분 후 {percent(p["whole_share_weights"][ticker])} · {text(shares)}</small>')
        rows.append(cells)
    parts.append(table(["자산", *[c["name"] for c in candidates]], rows))
    parts.append('<p class="subtle">각 후보는 해당 시나리오의 확률로 가중한 다섯 관점의 조건별 배분을 사용합니다. '
                 '확률이 0인 시나리오의 비교안은 관점별 배분의 단순 평균을 사용합니다.</p></details>')
    final = result["portfolio"]
    major = sorted(((t, w) for t, w in final["target_weights"].items() if w > 1e-10), key=lambda item: (-item[1], item[0]))
    parts.append('<aside class="ensemble-summary"><div><strong>세 시나리오를 함께 반영한 종합 배분안</strong>'
                 '<p>' + text(' · '.join(f'{"현금" if t == "CASH" else t} {percent(w)}' for t, w in major[:5])) +
                 '</p></div><a href="#portfolio">종합안 전체 보기 →</a></aside></section>')
    return ''.join(parts)


def render_meta_report(result):
    data, plan, reviews, portfolio = (result[k] for k in ("data", "scenario_draft", "reviews", "portfolio"))
    assets = {a["ticker"]: a for a in data["assets"]}
    parts = [f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>거시 시나리오 · AI 포트폴리오</title><style>{STYLES}
.scenario{{padding:20px 0;border-top:1px solid var(--line)}}.scenario:first-of-type{{border-top:0}}
.reason{{text-align:left;white-space:normal;min-width:230px}}li{{margin:6px 0}}td small{{display:block}}
.candidate-section{{margin:0 0 28px}}.candidate-section h2{{margin:0}}.candidate-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:18px 0 12px}}
.candidate-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px;display:flex;flex-direction:column;min-width:0}}
.candidate-card.leading{{border:2px solid #247b62;background:#eff7f2;box-shadow:0 5px 18px #173e2e14;padding:21px}}
.candidate-badge{{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:12px}}.leading .candidate-badge{{color:#156746}}
.candidate-card h3{{font-size:19px;line-height:1.5;min-height:57px;overflow-wrap:anywhere}}.candidate-probability{{font-size:34px;font-weight:750;margin:10px 0 12px;line-height:1.25}}
.leading .candidate-probability{{color:#156746}}.candidate-probability small{{display:block;font-size:12px;font-weight:400;margin-top:5px}}
.candidate-bar{{display:flex;height:9px;overflow:hidden;border-radius:6px}}.candidate-bar span{{display:block}}
.candidate-groups{{margin:13px 0;display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;font-size:13px}}.candidate-groups div{{display:flex;justify-content:space-between;gap:6px}}
.candidate-groups dt{{color:var(--muted)}}.candidate-groups dd{{margin:0;font-weight:700}}.candidate-groups i{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}}
.candidate-label{{font-size:12px;color:var(--muted);margin:8px 0}}.candidate-holdings{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}}
.candidate-holdings span{{font-size:12px;background:#f0f3f5;border-radius:5px;padding:4px 7px}}.leading .candidate-holdings span{{background:#e0eee5}}
.candidate-footnote{{font-size:11px;color:var(--muted);margin:auto 0 12px}}.candidate-link{{font-size:12px;font-weight:700;text-decoration:none}}
.ensemble-summary{{display:flex;justify-content:space-between;gap:16px;align-items:center;background:#e9eff3;border-radius:10px;padding:18px 20px;margin-top:18px;font-size:13px}}
.ensemble-summary p{{color:var(--muted);margin:6px 0 0}}.ensemble-summary a{{white-space:nowrap}}.candidate-section details{{scroll-margin-top:20px}}header{{padding-top:30px;padding-bottom:26px}}
@media(max-width:850px){{.candidate-grid{{grid-template-columns:1fr}}.candidate-card h3{{min-height:0}}.ensemble-summary{{align-items:flex-start;flex-direction:column}}}}
@media print{{.candidate-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}.candidate-card{{padding:12px;break-inside:avoid}}.candidate-card.leading{{padding:11px}}.candidate-probability{{font-size:25px}}}}
</style></head><body><header><div class="eyebrow">MACRO · FIVE PERSPECTIVES</div>
<h1>6~12개월 거시 시나리오 포트폴리오</h1>
<p>미국 주식 · ETF · 현금 | {text(local_time(result['generated_at_utc']))}</p></header>
<main class="wrap">
{'<div class="notice">합성 테스트 데이터 보고서입니다. 실제 투자 자료가 아닙니다.</div>' if data.get('synthetic') else ''}
{render_candidates(result)}
<nav><a href="#candidates">포트폴리오 후보</a><a href="#portfolio">종합 배분안</a><a href="#scenarios">거시 시나리오</a><a href="#reviews">다섯 관점</a><a href="#data">관측 자료</a></nav>
<div class="notice">시나리오 확률과 수익률 범위는 AI의 주관적 작업 가정입니다. 검증된 예측 정확도나 수익 보장이 아닙니다.
다섯 역할은 같은 기반 모델의 관점 모사이며 실제 투자자들의 의견이 아닙니다. 실제 주문은 전송하지 않습니다.</div>
<div class="stats"><div class="stat">가상 기준 자금<strong>{amount(portfolio['capital'])}</strong></div>
<div class="stat">정수 주식 후 현금<strong>{percent(portfolio['whole_share_weights']['CASH'])}</strong></div>
<div class="stat">과거 연환산 변동성<strong>{percent(portfolio['historical_volatility'])}</strong></div>
<div class="stat">새 AI 분석 / 재사용<strong>{text(result['new_codex_calls'])} / {text(result['cache_hits'])}</strong></div></div>
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
