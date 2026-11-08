# AI Hedge Fund — 추가 비용 없는 Codex 버전

[virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)의 개인 연구용 포크입니다. 버핏·멍거·그레이엄·린치·드러켄밀러의 원본 프롬프트와 포트폴리오 계산을 재사용합니다.

**1원칙: 기존 ChatGPT Pro $100 구독 외의 추가 지출을 만들지 않습니다.**

- AI 판단: ChatGPT로 로그인한 공식 Codex CLI.
- 데이터: yfinance를 통한 무료 주가와 연간 재무자료. API 키 불필요.
- 모델 API, Financial Datasets, x402 결제: 코드에서 차단.
- 매 AI 호출 전 포함 사용량과 크레딧 잔액 조회. 사용량이 95% 이상이거나 잔액이 0임을 확인할 수 없으면 중단.
- 크레딧은 무료/유료를 구분하지 않고 모두 사용 거부. 구매·자동충전·결제 설정 변경 기능 없음.
- 무료 데이터 오류, 인증 오류, 한도 도달 시 유료 서비스로 전환하지 않음.

현재 버전은 **최신 데이터 분석 + 가상 포트폴리오 조정안**까지 제공합니다. 실제 증권계좌에 연결하거나 주문하지 않습니다.

## 설치

Python 3.11 이상과 공식 Codex CLI가 필요합니다. 먼저 `codex login`으로 **ChatGPT 계정 로그인**을 완료하세요.

Windows PowerShell:

```powershell
git clone https://github.com/rjsxo943-gif/ai-hedge-fund.git
cd ai-hedge-fund
git switch codex/no-extra-cost
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-free.in
.venv/Scripts/python.exe -m hedge_fund.no_cost doctor
```

macOS/Linux에서는 `.venv/Scripts/python.exe`를 `.venv/bin/python`으로 바꾸세요. 원본 전체 개발 환경은 `poetry install`로 설치할 수 있지만 위 최소 환경으로도 새 기능을 사용할 수 있습니다. 패키지 설치 시 `aihf`도 같은 무료 경로를 실행합니다.

## 사용

1. 무료 데이터만 수집하기. AI 호출은 없습니다.

```powershell
.venv/Scripts/python.exe -m hedge_fund.no_cost fetch --tickers AAPL --out .runtime/aapl.json
```

2. 한 투자자로 시험하기. 최대 1회 Codex 호출입니다.

```powershell
.venv/Scripts/python.exe -m hedge_fund.no_cost analyze --packet .runtime/aapl.json --investors buffett --max-calls 1 --out .runtime/buffett.json
```

3. 다섯 투자자로 분석하기. 최대 5회이며 같은 데이터의 결과는 캐시를 재사용합니다.

```powershell
.venv/Scripts/python.exe -m hedge_fund.no_cost analyze --packet .runtime/aapl.json --out .runtime/report.json
```

보고서는 원본 데이터·출처·조회 시각·주가 기준일, 투자자별 의견과 근거, 목표 비중·주식 수·매수/매도 조정안을 포함합니다. 기본 가상 자금은 USD 10,000, 종목당 최대 비중은 20%입니다. 초과분은 현금으로 남으며 공매도와 레버리지는 사용하지 않습니다.

보유 중인 **가상** 포트폴리오를 반영하려면 로컬 JSON 파일을 만들어 `--paper-book .runtime/book.json`을 추가하세요.

```json
{"currency":"USD","cash":8000,"positions":{"AAPL":5}}
```

기존 보유 종목이 분석 대상에서 빠져 있으면 멈춥니다. 보고서만 만들며 보유 파일을 자동 변경하거나 주문을 전송하지 않습니다. 여러 종목은 `fetch --tickers AAPL,MSFT`로 수집하고 `analyze --max-calls 10`처럼 호출 상한을 명시하세요.

## 데이터 범위

- 1차 무료 공급자는 Yahoo Finance입니다. SEC EDGAR 어댑터는 아직 구현하지 않았습니다.
- 현재 USD 주식의 최근 일별 종가와 연간 재무자료를 사용합니다. 연간 자료를 분기 TTM으로 표시하지 않습니다.
- 공시일이 보장되지 않고 사후 수정 자료가 포함될 수 있어 이 경로의 **과거 백테스트는 지원하지 않습니다.**
- 같은 결산일의 재무표를 맞춰 계산하고 누락 값은 null로 남깁니다.
- PER/PBR은 최신 종가와 최근 연간 실적 기준입니다. 주식분할에 따른 주당 수치 기준 차이를 원문과 대조해야 합니다.
- 주가가 7일, 최신 연간 실적의 결산일이 550일보다 오래되면 분석을 중단합니다.
- 뉴스·금리·거시 데이터와 정성적인 경영진/해자 조사는 아직 포함하지 않습니다.
- yfinance는 Yahoo의 공식 제품이 아니며 개인 연구용 데이터 이용 범위를 확인해야 합니다.

투자자 이름은 공개 철학을 모사하는 프롬프트로, 각 인물의 실제 판단이나 승인을 의미하지 않습니다.

## 비용과 보관

Codex 분석은 Pro 포함 사용량을 소비하므로 무제한이 아닙니다. 잔여량 확인은 계정 전체의 상태를 읽기만 하며 초기화권이나 크레딧을 소비하지 않습니다. 다른 세션도 같은 사용량을 공유합니다. 프로그램은 별도 API 키를 전달하지 않고 ChatGPT 로그인을 강제합니다.

추가 비용이 필요한 기능이 생기면 무료 방식으로 불가능한 이유부터 설명하고 별도로 판단합니다. 이 버전에는 유료 기능을 켜는 스위치가 없습니다.

이 포크는 공개 저장소입니다. `.runtime/`의 데이터·의견 캐시·보고서, `.env`, 가상환경, 인증 파일은 Git에서 제외합니다. Google Cloud 유료 서버나 상시 서비스를 만들지 않으며 GitHub Actions 워크플로도 추가하지 않았습니다.

Codex Cloud에서 소스를 수정·테스트하려면 사용할 Cloud 환경을 먼저 연결해야 합니다. 로컬 구독 인증 파일을 Git에 올리거나 공개 CI에 복사하지 마세요.

## 검증

```powershell
.venv/Scripts/python.exe -m pytest tests/no_cost hedge_fund/portfolio/test_construction.py hedge_fund/risk/test_limits.py -q
```

유료 API 차단, API 키 인증 거부, 구독 포함량/크레딧 검사, 호출 상한, 데이터 정렬, 캐시, 포트폴리오 위험 한도를 검증합니다. 원본 유료 어댑터 통합 테스트는 이 포크의 동작과 달라 실행 대상으로 삼지 않습니다.

원본 설명: [docs/UPSTREAM_README.md](docs/UPSTREAM_README.md). MIT 라이선스를 따릅니다.

공식 참고: [Codex 실행](https://learn.chatgpt.com/docs/non-interactive-mode), [계정 사용량 조회](https://learn.chatgpt.com/docs/app-server), [yfinance](https://github.com/ranaroussi/yfinance).
