"""Free macro observations and an explicit US-listed research universe."""

from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from hedge_fund.no_cost.data import fetch_packet, number, symbols

# These are research candidates, not recommendations or an exhaustive screen.
ETFS = {"SPY": ("미국 대형주", "equity"), "QQQ": ("나스닥100", "equity"),
        "IWM": ("미국 소형주", "equity"), "IEF": ("미국 중기 국채", "bond"),
        "GLD": ("금", "gold")}
DEFAULT_STOCKS = ["AAPL", "MSFT", "JPM", "XOM"]
# title, unit, maximum observation age, series is a level (compute YoY)
SERIES = {
    "CPIAUCSL": ("소비자물가지수", "index", 80, True),
    "UNRATE": ("실업률", "%", 80, False),
    "PAYEMS": ("비농업 고용", "thousand persons", 80, True),
    "DFF": ("실효 연방기금금리", "%", 10, False),
    "DGS2": ("미 국채 2년 금리", "%", 10, False),
    "DGS10": ("미 국채 10년 금리", "%", 10, False),
    "BAMLH0A0HYM2": ("하이일드 신용 스프레드", "percentage points", 10, False),
    "M2SL": ("광의통화 M2", "billion USD", 80, True),
    "WALCL": ("연준 총자산", "million USD", 21, True),
    "GDPC1": ("실질 GDP", "billion chained USD, annualized", 150, True),
}


def macro_from_csv(content: str, today: date) -> list[dict]:
    table = pd.read_csv(StringIO(content), na_values=".")
    if table.empty:
        raise ValueError("거시 데이터 응답이 비어 있습니다.")
    dates = pd.to_datetime(table.iloc[:, 0], errors="coerce")
    observations = []
    for series, (title, unit, max_age, is_level) in SERIES.items():
        if series not in table:
            raise ValueError(f"필수 거시 지표 {series}가 없습니다.")
        values = pd.to_numeric(table[series], errors="coerce")
        valid = dates.notna() & values.notna() & np.isfinite(values)
        history = pd.Series(values[valid].to_numpy(), index=dates[valid]).sort_index()
        history = history[history.index.date <= today]
        if history.empty:
            raise ValueError(f"{series}: 사용 가능한 관측치가 없습니다.")
        last_day, last = history.index[-1].date(), float(history.iloc[-1])
        if (today - last_day).days > max_age:
            raise ValueError(f"{series}: 관측치가 오래되어 거시 분석을 멈춥니다 ({last_day}).")
        year_before = history[history.index <= history.index[-1] - pd.DateOffset(years=1)]
        yoy = (last / float(year_before.iloc[-1]) - 1) if is_level and not year_before.empty and year_before.iloc[-1] > 0 else None
        observations.append({"id": series, "name": title, "unit": unit,
            "observation_date": str(last_day), "value": last, "yoy_change": yoy,
            "history": [{"date": stamp.date().isoformat(), "value": float(value)}
                        for stamp, value in history.iloc[-13:].items()],
            "source": f"https://fred.stlouisfed.org/series/{series}"})
    return observations


def fred_download(content: bytes) -> str:
    """Mixed-frequency graph downloads arrive as a ZIP of CSVs, not one CSV."""
    if not content.startswith(b"PK"):
        return content.decode("utf-8-sig")
    tables = []
    with ZipFile(BytesIO(content)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 30_000_000:
            raise ValueError("거시 데이터 압축 파일이 예상 크기를 초과했습니다.")
        for item in archive.infolist():
            if item.filename.lower().endswith(".csv"):
                table = pd.read_csv(BytesIO(archive.read(item)), na_values=".")
                tables.append(table.set_index(table.columns[0]))
    if not tables:
        raise ValueError("FRED 다운로드에 CSV가 없습니다.")
    return pd.concat(tables, axis=1).sort_index().to_csv(index_label="observation_date")


def fetch_meta_packet(runtime_dir: Path, stocks: list[str] | None = None) -> dict:
    import yfinance as yf

    today = datetime.now(timezone.utc).date()
    stocks = DEFAULT_STOCKS if stocks is None else stocks
    if len(stocks) > 8 or (stocks and symbols(",".join(stocks)) != stocks) or any(t in ETFS or t == "CASH" for t in stocks):
        raise ValueError("개별 주식 후보는 중복 없는 대문자 종목코드 최대 8개까지 지정하세요.")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(runtime_dir / "yfinance"))
    start = (today - timedelta(days=800)).isoformat()
    response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": ",".join(SERIES), "cosd": ",".join([start] * len(SERIES))}, timeout=45,
        headers={"User-Agent": "AI-Investment-Committee personal research; github.com/rjsxo943-gif/ai-hedge-fund"})
    response.raise_for_status()
    macro = macro_from_csv(fred_download(response.content), today)
    print("  거시 지표 10개 수집 완료. ETF·주식 후보의 가격을 확인합니다.")
    financials = fetch_packet(stocks, runtime_dir) if stocks else {"companies": []}
    company_lookup = {c["ticker"]: c for c in financials["companies"]}
    assets, returns = [], {}
    for ticker in [*ETFS, *stocks]:
        stock = yf.Ticker(ticker)
        history = stock.history(period="2y", auto_adjust=False, raise_errors=True)
        close = history["Close"].dropna()
        adjusted = history["Adj Close"].dropna()
        currency = stock.history_metadata.get("currency")
        if len(adjusted) < 200 or close.empty or currency != "USD":
            raise ValueError(f"{ticker}: USD 가격 이력이 충분하지 않습니다.")
        day, price = close.index[-1].date(), number(close.iloc[-1])
        if price is None or price <= 0 or not 0 <= (today - day).days <= 7:
            raise ValueError(f"{ticker}: 유효한 최신 종가가 없습니다.")
        r = adjusted.pct_change().dropna()
        r.index = pd.to_datetime([d.date() for d in r.index])
        returns[ticker] = r
        label, asset_class = ETFS[ticker] if ticker in ETFS else (company_lookup[ticker]["name"], "equity")
        assets.append({"ticker": ticker, "name": label, "type": "etf" if ticker in ETFS else "stock",
            "asset_class": asset_class, "close": price, "quote_date": day.isoformat(), "currency": "USD",
            "annualized_volatility": float(r.std(ddof=1) * np.sqrt(252)),
            "returns": {str(n): float(adjusted.iloc[-1] / adjusted.iloc[-n - 1] - 1)
                        for n in (63, 126, 252) if len(adjusted) > n},
            "source": f"https://finance.yahoo.com/quote/{ticker}/history/"})
        print(f"  {ticker} 확인 완료")
    aligned = pd.DataFrame(returns).dropna()
    if len(aligned) < 126:
        raise ValueError("자산 간 위험을 비교할 공통 가격 이력이 부족합니다.")
    covariance = aligned.cov() * 252
    return {"schema_version": 1, "mode": "macro_scenarios", "horizon_months": [6, 12],
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": False,
        "macro": macro, "assets": assets, "stock_fundamentals": financials["companies"],
        "covariance_annual": {t: {u: float(covariance.loc[t, u]) for u in returns} for t in returns},
        "risk_sample_days": len(aligned),
        "limitations": ["거시 관측일은 공시일이 아닙니다. 최신 수정치이며 과거 백테스트용이 아닙니다.",
                         "후보군 안에서만 선택합니다. 전체 미국 시장을 탐색한 결과가 아닙니다.",
                         "개별 주식 재무는 연간 자료이며 뉴스·기업 정성 조사는 포함하지 않습니다.",
                         "과거 가격의 수익률과 변동성은 미래 예측을 보장하지 않습니다."]}
