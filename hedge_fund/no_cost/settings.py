"""Local preferences; never stored in the public repository."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path

from hedge_fund.no_cost.data import symbols

INVESTORS = ("buffett", "munger", "graham", "lynch", "druckenmiller")
INVESTOR_NAMES = {"buffett": "워런 버핏", "munger": "찰리 멍거", "graham": "벤저민 그레이엄",
                  "lynch": "피터 린치", "druckenmiller": "스탠리 드러켄밀러"}


def defaults() -> dict:
    return {"schema_version": 1, "watchlist": ["AAPL"], "investors": list(INVESTORS),
            "max_calls": 10, "max_position": 0.20, "paper_cash": 10000.0, "paper_book": None}


def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != set(defaults()) or value["schema_version"] != 1:
        raise ValueError("설정 파일 형식이 올바르지 않습니다. settings 명령으로 설정을 확인하세요.")
    watchlist = value["watchlist"]
    if not isinstance(watchlist, list) or not watchlist or any(not isinstance(t, str) for t in watchlist):
        raise ValueError("관심 종목은 비어 있지 않은 종목코드 목록이어야 합니다.")
    if symbols(",".join(watchlist)) != watchlist:
        raise ValueError("관심 종목은 중복 없는 대문자 종목코드여야 합니다.")
    investors = value["investors"]
    if (not isinstance(investors, list) or not investors or any(i not in INVESTORS for i in investors)
            or len(set(investors)) != len(investors)):
        raise ValueError("분석 관점 설정이 올바르지 않습니다.")
    if type(value["max_calls"]) is not int or not 1 <= value["max_calls"] <= 100:
        raise ValueError("호출 상한은 1~100 사이 정수여야 합니다.")
    for name in ("paper_cash", "max_position"):
        n = value[name]
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n) or n <= 0:
            raise ValueError("가상 자금과 최대 비중은 양수여야 합니다.")
    if value["max_position"] > 1:
        raise ValueError("종목당 최대 비중은 1을 초과할 수 없습니다.")
    if value["paper_book"] is not None and not isinstance(value["paper_book"], str):
        raise ValueError("가상 포트폴리오 경로가 올바르지 않습니다.")
    return value


def load_settings(runtime_dir: Path) -> dict:
    path = runtime_dir / "settings.json"
    if not path.is_file():
        return defaults()
    return validate_settings(json.loads(path.read_text(encoding="utf-8")))
