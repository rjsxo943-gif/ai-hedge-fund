"""Free Yahoo data packets. Latest-data research only; never a PIT backtest."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import re


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def symbols(value: str) -> list[str]:
    result = list(dict.fromkeys(value.upper().replace(",", " ").split()))
    if not result or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,19}", s) for s in result):
        raise ValueError("Use comma-separated stock symbols such as AAPL,MSFT")
    return result


def _cell(table, period, *labels):
    if period not in table.columns:
        return None
    for label in labels:
        if label in table.index:
            value = number(table.loc[label, period])
            if value is not None:
                return value
    return None


def annual_rows(income, balance, cashflow) -> list[dict]:
    """Align statement dates exactly; never mix a quarter with an annual row."""
    rows = []
    for period in sorted(income.columns, reverse=True)[:5]:
        revenue = _cell(income, period, "Total Revenue", "Operating Revenue")
        net_income = _cell(income, period, "Net Income", "Net Income Common Stockholders")
        if revenue is None or net_income is None:
            continue
        equity = _cell(balance, period, "Stockholders Equity", "Common Stock Equity")
        shares = _cell(balance, period, "Ordinary Shares Number")
        debt = _cell(balance, period, "Total Debt")
        fcf = _cell(cashflow, period, "Free Cash Flow")
        if fcf is None:
            operating_cash = _cell(cashflow, period, "Operating Cash Flow")
            capex = _cell(cashflow, period, "Capital Expenditure")
            if operating_cash is not None and capex is not None:
                fcf = operating_cash - abs(capex)
        rows.append({
            "report_period": period.date().isoformat(), "period_type": "annual",
            "filing_date": None, "revenue": revenue, "net_income": net_income,
            "eps_diluted": _cell(income, period, "Diluted EPS"),
            "equity": equity, "total_debt": debt, "shares_at_period_end": shares,
            "free_cash_flow": fcf,
            "net_margin": ratio(net_income, revenue),
            "gross_margin": ratio(_cell(income, period, "Gross Profit"), revenue),
            "operating_margin": ratio(_cell(income, period, "Operating Income"), revenue),
            "debt_to_equity": ratio(debt, equity),
            "current_ratio": ratio(_cell(balance, period, "Current Assets"),
                                   _cell(balance, period, "Current Liabilities")),
            "book_value_per_share": ratio(equity, shares),
        })
    for i, row in enumerate(rows):
        older = rows[i + 1] if i + 1 < len(rows) else None
        # Require about a year between periods. A missing annual row is not YoY growth.
        gap = ((datetime.fromisoformat(row["report_period"])
                - datetime.fromisoformat(older["report_period"])).days if older else 0)
        comparable = older if 330 <= gap <= 400 else None
        row["revenue_growth_yoy"] = (
            ratio(row["revenue"], comparable["revenue"]) - 1
            if comparable and comparable["revenue"] > 0 else None
        )
        row["eps_growth_yoy"] = (
            ratio(row["eps_diluted"], comparable["eps_diluted"]) - 1
            if comparable and row["eps_diluted"] is not None
            and comparable["eps_diluted"] is not None and comparable["eps_diluted"] > 0 else None
        )
        row["roe_average_equity"] = (
            ratio(row["net_income"], (row["equity"] + comparable["equity"]) / 2)
            if comparable and row["equity"] is not None and comparable["equity"] is not None
            and row["equity"] > 0 and comparable["equity"] > 0 else None
        )
    return rows


def fetch_packet(tickers: list[str], runtime_dir: Path) -> dict:
    import yfinance as yf

    runtime_dir.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(runtime_dir / "yfinance"))
    companies = []
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        try:
            history = stock.history(period="1mo", interval="1d", auto_adjust=False,
                                    raise_errors=True)
            info = stock.get_info()
            rows = annual_rows(stock.get_income_stmt(pretty=True), stock.get_balance_sheet(pretty=True),
                               stock.get_cashflow(pretty=True))
            closes = history["Close"].dropna()
            if closes.empty or not rows:
                raise ValueError("missing price or usable annual statements")
            price = number(closes.iloc[-1])
            if price is None or price <= 0:
                raise ValueError("invalid closing price")
            quote_currency = info.get("currency")
            financial_currency = info.get("financialCurrency")
            latest = rows[0]
            same_currency = bool(quote_currency and quote_currency == financial_currency)
            companies.append({
                "ticker": ticker, "name": info.get("longName", ticker),
                "sector": info.get("sector"), "industry": info.get("industry"),
                "quote": {"close": price, "date": closes.index[-1].date().isoformat(),
                          "currency": quote_currency, "basis": "Yahoo daily Close; auto_adjust=False"},
                "financial_currency": financial_currency, "annual_history": rows,
                "valuation": {
                    "pe_latest_annual": ratio(price, latest["eps_diluted"]) if same_currency else None,
                    "pb_latest_annual": ratio(price, latest["book_value_per_share"]) if same_currency else None,
                },
                "sources": [f"https://finance.yahoo.com/quote/{ticker}/history/",
                            f"https://finance.yahoo.com/quote/{ticker}/financials/",
                            f"https://finance.yahoo.com/quote/{ticker}/balance-sheet/",
                            f"https://finance.yahoo.com/quote/{ticker}/cash-flow/"],
            })
        except Exception as exc:
            raise RuntimeError(
                f"Free data retrieval failed for {ticker} ({type(exc).__name__}). "
                "Stopped without a paid fallback. Try again later or use a previously saved packet."
            ) from exc
    return {
        "schema_version": 1, "mode": "latest_research", "data_provider": "Yahoo Finance via yfinance",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "synthetic": False,
        "limitations": [
            "Annual periods, not quarterly TTM. Filing dates are unavailable in these tables.",
            "Latest restated tables may differ from what was known historically. Backtesting is disabled.",
            "Quotes can be delayed; use the recorded quote date. Missing values remain null.",
            "Annual EPS and book value may use a different split basis from the latest quote; verify valuation ratios.",
            "No macro, news, or full qualitative moat evidence is supplied in this first version.",
        ],
        "companies": companies,
    }
