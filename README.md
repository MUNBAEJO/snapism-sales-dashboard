# 스내피즘 · 포토이즘 매출 대시보드

두 브랜드(스내피즘 · 포토이즘)의 매출을 매일 자동 수집해 Streamlit 대시보드로 보여주고,
IP 정산서(PDF)를 발행하는 사내 시스템입니다.

- **접속**: `http://<서버>:8503` (구글 로그인 → `allowed-users.json` 승인 계정만)
- **데이터 시작일**: 스내피즘 2025-04-30 · 포토이즘 2025-01-01
- **매출 정의**: 스내피즘 = 실결제(쿠폰 제외) · 포토이즘 = 실결제 + 쿠폰 + 서비스코인(지정 국가만)
  — 브랜드마다 다르니 숫자를 비교할 땐 반드시 확인하세요.

---

## 1. 데이터 흐름

```
CMS/어드민 ──크롤──> raw/ · raw_photoism/ ──ingest──> data/*.parquet ──> Streamlit 화면
                                                          └──> 집계 parquet(포토이즘)
```

| 단계 | 스내피즘 | 포토이즘 |
|---|---|---|
| 수집 | `crawler.py` | `photoism_crawler.py` |
| 원본 | `raw/*.csv` | `raw_photoism/*.xlsx` |
| 적재 | `ingest.py` | `photoism_ingest.py` |
| 최종 | `data/master.parquet` | `data/master_photoism.parquet` |
| 집계 | — | `build_photoism_agg.py` → `master_photoism_agg / _hourly / _orig` |

포토이즘은 원본이 7천 개·4.5GB라 **기존 parquet + 신규분만 교체**하는 증분 구조입니다
(예전엔 전량 재파싱이라 OOM이 났습니다). 스내피즘은 아직 데이터가 작아 전량 재파싱입니다.

> ⚠️ **크롤러는 CMS에 실제로 접속합니다.** 함부로 돌리지 마세요.
> `ingest.py` · `photoism_ingest.py` · `build_photoism_agg.py` 는 로컬 파일만 다뤄 안전합니다.

### 과거 데이터를 다시 넣어야 할 때

원본이 이미 있으면 **CMS를 다시 안 건드려도 됩니다.** 구간만 지정해 다시 적재하세요.

```bash
python photoism_ingest.py 2025-02-02 2025-02-02
```

시작·종료를 **둘 다** 주면 그 구간만 교체하고 나머지 날짜는 기존 데이터를 그대로 둡니다.
원본까지 다시 받아야 하면 `photoism_reget.py`(하루 간격 지연 포함)를 씁니다.

---

## 2. 자동 실행

**`scheduler.py` 하나가 단일 진실입니다.** 상주 데몬이고, 로그인 시 시작 폴더의
`스내피즘_스케줄러.lnk` 로 뜹니다.

| 시각 | 하는 일 |
|---|---|
| 매일 09:00 | 환율 갱신 → 스내피즘 크롤 → 포토이즘 크롤 (실패 시 1시간 뒤 재시도) |
| 매일 08:40 / 20:40 | Jira 일정 캐시 예열 |
| 매일 11:00 | **커버리지 점검** (`coverage_audit.py`) — 이상 있으면 관리자 메일 |
| 매일 04:30 | 로그 회전 (5MB 초과 시 3세대 보관) |
| 월요일 05:00 | 매출 60일 딥 재수집 (늦게 들어온 취소 반영) |
| 월요일 07:00 | SM 촬영수 주간 갱신 |

이 밖에 Windows 작업 스케줄러에 `snapism_restart`(매일 09:30 대시보드 재시작),
`sm_weekly_mail`(월요일 11:00)이 등록돼 있습니다.

> ⚠️ **크롤을 작업 스케줄러에 또 등록하지 마세요.** `snapism_daily` · `photoism_daily`
> 가 scheduler.py와 중복 실행돼 2026-07-31에 비활성화했습니다.
> `작업스케줄러_등록.ps1` / `.py` 는 삭제 대상 작업('포토이즘백필')을 되살리니 실행 금지입니다.

### 살아있게 유지하는 것

`대시보드_자동시작.bat` 이 감시 루프 둘을 띄웁니다. 2분마다 확인해 죽어 있으면 되살립니다.

- `watchdog_loop.ps1` → 대시보드(8503)
- `scheduler_watchdog_loop.ps1` → `scheduler.py` (단일 실행 가드 = 포트 47615)

---

## 3. 화면

페이지 목록은 **`pages_registry.py` 한 곳**에서 정의합니다. 추가하려면 거기 한 줄만 넣으면
라우터와 권한 체크박스에 동시에 반영됩니다. `key` 는 팀 권한 저장에 쓰이니 **한 번 정하면 바꾸지 마세요.**

| 페이지 | 파일 | 기본 노출 |
|---|---|---|
| KPI목표 | `views/0_🎯_KPI목표.py` | ✅ |
| 스내피즘 | `views/0_📊_스내피즘.py` | ✅ |
| 포토이즘 | `views/1_📸_포토이즘.py` | ✅ |
| 주간리포트 | `views/4_📋_주간리포트.py` | ✅ |
| 타이틀 런 비교 | `views/7_🆚_타이틀_런_비교.py` | |
| IP정산서 | `views/8_🧾_IP정산서.py` | |
| IP매출 조회 (구) | `views/2_💰_IP정산현황_(스내피즘).py` | 제거 예정 |
| 기간 후 매출분석 | `views/3_⚠️_기간_후_매출분석.py` | |
| SM 촬영현황 | `views/6_🎬_SM촬영현황.py` | |
| 접속·계정 관리 | `views/5_🔐_접속관리.py` | 소유자 전용 |

권한은 `auth.py` — 역할(owner/editor/viewer) + 팀별 페이지 권한입니다.

---

## 4. IP 정산서

티켓번호 하나를 넣으면 매출을 모아 PDF를 만들고 메일로 보냅니다.

`settlement_map.py`(매핑) · `settlement_calc.py`(계산) · `settlement_pdf.py`(PDF) ·
`settlement_fx.py`(환율) · `settlement_mail.py`(발송)

- 문서 구성: 표지 → 브랜드별 상세 → 별첨(국가×멤버) → 부록(환율·단가)
- 환율은 서울외국환중개 매매기준율. **라오스(LAK)·페루(PEN)만 미고시**라 야후 파이낸스로 환산합니다
  (칠레 CLP는 고시 통화입니다 — 자주 헷갈리는 지점)
- 멤버별 수량·금액은 **최대잔여법**으로 국가 합계에 맞춥니다. 멤버마다 반올림하면 1원씩 어긋납니다

---

## 5. 설치 (새 PC)

```bash
git clone <저장소>
cd 스내피즘 매출데이터
setup.bat                          # pip install -r requirements.txt
python -m playwright install chromium
copy config.example.json config.json
```

그다음:

1. **`config.json` 채우기** — CMS 계정, Jira 토큰, 메일(Gmail 앱 비밀번호), Gemini 키
2. **`allowed-users.json`** 준비 (승인 계정 목록)
3. **`.streamlit/secrets.toml`** 에 구글 OAuth 설정
4. 시작 폴더에 `스내피즘_스케줄러.lnk`(→`스케줄러시작.bat`)와
   `SnapismDashboard.lnk`(→`대시보드_자동시작.bat`) 등록
5. `_register_restart_task.ps1` 로 매일 09:30 재시작 작업 등록 (관리자 권한)

> `config.json` · `allowed-users.json` · `.streamlit/secrets.toml` · `data/` · `raw*/` · `reports/`
> 는 **전부 gitignore 대상입니다.** 자격증명이 들어 있으니 커밋하지 마세요.

⚠️ 워치독 스크립트가 `python.exe` 경로를 하드코딩하고 있습니다(없으면 PATH에서 찾도록
폴백은 넣어뒀습니다). 그 밖에도 `_register_startup.ps1` 등에 절대경로가 남아 있으니
PC를 옮길 땐 확인하세요.

---

## 6. 점검·문제 해결

```bash
python coverage_audit.py              # 수집 결손 전 기간 점검
python coverage_audit.py --recent 14  # 최근 14일만 (스케줄러가 쓰는 모드)
```

**커버리지 점검이 잡는 것**: 완전결손 · 부분결손 · 국가이탈 · 신선도.
브랜드 최종일만 보는 신선도 체크로는 "30개국 중 한국만 빠진 날"을 못 잡습니다
(실제로 2025년 7일이 그렇게 1년 반 동안 비어 있었습니다).

기준을 읽을 때 알아둘 것:

- **같은 요일끼리 비교합니다.** 주말이 평일의 3~5배라 그냥 비교하면 월요일이 늘 걸립니다
- **최근 2일은 판정하지 않습니다.** CMS 파일은 한국 날짜로 끊기는데 서쪽 국가는 늦습니다.
  `photoism_de_20260802.xlsx` 안에 든 건 **08-01 거래**입니다(독일 = 한국 −7h).
  유럽·미주는 최근 하루가 늘 비어 있다가 다음 날 채워집니다 — 결손이 아닙니다

**로그**: `logs/scheduler.log`(스케줄), `logs/crawler.log`·`logs/photoism_crawler.log`(수집),
`logs/watchdog.log`·`logs/scheduler_watchdog.log`(감시), `logs/dashboard_access.log`(접속)

**대시보드가 안 뜰 때**: 8503 포트를 잡고 있는 프로세스만 골라 죽이세요.
`taskkill /IM python.exe` 는 스케줄러까지 같이 죽입니다.

---

## 7. 코드를 고치기 전에 알아둘 것

- **`@st.cache_data` 는 밑줄(`_`)로 시작하는 인자를 캐시 키 해시에서 제외합니다.**
  파일 버전을 `_v` 로 넘기면 캐시가 갱신되지 않습니다. 여러 번 사고가 났습니다
- 캐시된 DataFrame은 **in-place로 고치지 마세요.** 파생 컬럼은 로더 안에서 만들고,
  꼭 고쳐야 하면 `.copy()` 먼저 하세요
- 화면 문구는 **해요체**로 통일합니다
- 정산서 `.page` 는 높이 고정 + `overflow:hidden` 이라 **넘치면 조용히 잘립니다.**
  표를 늘렸으면 실제로 렌더해서 확인하세요
- 스내피즘·포토이즘 뷰에 같은 함수가 복붙돼 있습니다(CSS만 400줄 가까이).
  한쪽만 고치면 어긋나니 양쪽을 함께 보세요
