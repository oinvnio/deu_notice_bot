# 동의대학교 공지사항 Discord 봇

학교 공지사항을 **카테고리별로** 크롤링해서 각각 다른 Discord 채널로 전송하고,
기숙사 **그날 식단**을 매일 아침 보내줍니다.

## 지원 카테고리

| 카테고리 | 게시판 | GitHub Secret 이름 |
|---|---|---|
| 📢 일반 | `/www/deu-notice.do` | `WEBHOOK_GENERAL` |
| 💰 장학 | `/www/deu-scholarship.do` | `WEBHOOK_SCHOLARSHIP` |
| 📚 교육·모집 | `/www/deu-education.do` | `WEBHOOK_EDUCATION` |
| 🏠 기숙사 | `/www/deu-dormitory.do` | `WEBHOOK_DORMITORY` |
| 💼 채용 | `/www/deu-job.do` | `WEBHOOK_JOB` |
| 📋 입찰 | `/www/deu-bids.do` | `WEBHOOK_BIDS` |
| 🌐 외부기관 | `/www/deu-external.do` | `WEBHOOK_EXTERNAL` |

카테고리별 이름·이모지·임베드 색상은 `crawler.py`의 `CATEGORIES`에서 한곳으로 관리합니다.
- 시크릿을 등록한 카테고리만 동작합니다. 등록하지 않은 카테고리는 조용히 건너뜁니다.
- 카테고리 전용 시크릿이 없으면 공용 `DISCORD_WEBHOOK_URL`로 전송됩니다.
  (전부 한 채널에서 받고 싶다면 공용 시크릿 하나만 등록하세요.)

## 오늘의 식단

| 기숙사 | 페이지 | GitHub Secret 이름 |
|---|---|---|
| 🍚 효민생활관 | `dorm.deu.ac.kr/60/6050.do` | `WEBHOOK_MENU_HYOMIN` |
| 🍱 행복기숙사 | `dorm.deu.ac.kr/60/6051.do` | `WEBHOOK_MENU_HAPPY` |

기숙사별 정보는 `menu.py`의 `DORMS`에서 관리합니다.
식단표는 주 단위로 올라오므로 주간 표를 통째로 읽은 뒤 **그날 칸만 뽑아** 보냅니다.

웹후크는 **구체적인 것부터** 찾습니다:
`WEBHOOK_MENU_<기숙사>` → `WEBHOOK_MENU` → `WEBHOOK_DORMITORY` → `DISCORD_WEBHOOK_URL`

→ 따로 등록하지 않으면 **기숙사 공지 채널로 함께** 갑니다.
   식단만 다른 채널로 빼려면 `WEBHOOK_MENU` 하나만 등록하세요.

## 파일 구조

```
├── crawler.py          # 카테고리별 공지 크롤링 (CATEGORIES에 게시판·이모지·색상 정의)
├── menu.py             # 기숙사 식단표 크롤링 (DORMS에 기숙사·페이지 정의)
├── main.py             # 새 공지·식단 감지 & 채널별 디스코드 전송
├── requirements.txt    # 의존성
├── seen.json           # 전송 완료 기록 (공지 ID + 식단 주차, 중복 방지)
└── .github/
    └── workflows/
        └── notify.yml  # GitHub Actions 스케줄러
```

## 설치 & 실행 방법

### 1. Discord 채널 & 웹후크 생성
카테고리별로 채널을 만든 뒤, 각 채널에서
**채널 설정 → 연동 → 웹후크 → 새 웹후크**로 URL을 복사합니다.

### 2. GitHub Secret 등록
레포 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

위 표의 이름으로 채널별 웹후크 URL을 등록합니다.
(예: Name `WEBHOOK_SCHOLARSHIP`, Value 장학 채널의 웹후크 URL)

### 3. (선택) 키워드 필터
Settings → Secrets and variables → Actions → **Variables** 탭에서
`NOTICE_KEYWORDS`를 등록하면 제목에 해당 단어가 든 공지만 전송합니다.

```
NOTICE_KEYWORDS = 장학,수강신청,등록금
```

비워두면 모든 공지를 전송합니다.

### 4. 수동 테스트
Actions 탭 → **DEU Notice Bot** → **Run workflow** 버튼으로 즉시 실행 가능

로컬에서 크롤링만 확인하려면:

```bash
pip install -r requirements.txt
python crawler.py     # 카테고리별 공지 수집 결과를 출력 (디스코드 전송 없음)
python menu.py        # 기숙사별 식단표 파싱 결과를 출력 (디스코드 전송 없음)
```

Windows에서는 `python` 대신 `py`를 쓰면 확실합니다 (`py -m pip install -r requirements.txt`,
`py menu.py`). Git Bash에서 가상환경을 쓸 경우 활성화는
`source .venv/Scripts/activate` 입니다(PowerShell의 `.venv\Scripts\Activate.ps1`이 아닙니다).

## 동작 방식

- **첫 실행 스팸 방지**: 카테고리를 새로 켜면 최신 5건만 전송합니다.
- **중복 방지**: 전송한 공지 ID를 `seen.json`에 카테고리별로 기록하고,
  카테고리당 최근 300건까지만 보관합니다.
- **고장 알림**: 크롤링이 실패하거나 공지를 **한 건도 파싱하지 못하면**
  해당 채널로 ⚠️ 경고를 보내고 워크플로를 실패 처리합니다.
  (학교 홈페이지 개편으로 `crawler.py`의 셀렉터가 깨진 경우를 잡기 위한 장치입니다.)
- **전송 실패 재시도**: 디스코드 전송이 실패한 공지는 `seen.json`에 기록하지 않으므로
  다음 실행에서 다시 시도합니다.
- **식단은 하루 한 번**: 매일 아침 7시(KST)에 그날 식단을 보냅니다.
  전송한 날짜를 `seen.json`의 `menu:<기숙사>`에 기록하므로, 그날 이후 실행에서는
  다시 보내지 않습니다.
- **묵은 식단 방지**: 식단표에 날짜가 적혀 있으면 **오늘 날짜와 맞을 때만** 보냅니다.
  지난주 표가 그대로 걸려 있어도 잘못 보내지 않습니다.
  (날짜 없이 요일만 적힌 표라면 요일로 맞춥니다.)
- **오늘 칸이 없을 때**: 아직 갱신 전이거나 그날은 운영하지 않는 것으로 보고
  조용히 넘어간 뒤, 다음 실행에서 다시 확인합니다(경고를 보내지 않습니다).
- **식단 고장 알림은 하루 한 번**: 30분마다 같은 경고가 반복되지 않도록,
  식단 파싱 실패 알림은 기숙사당 하루 한 번만 갑니다.
  식단은 부가 기능이라 실패해도 **워크플로를 실패 처리하지는 않습니다.**

## 실행 주기
- **공지**: 평일 오전 8시 ~ 오후 6시, **30분마다**
- **식단**: 주말 포함 **매일 오전 7시**(KST)

식단이 그때까지 안 올라와 있으면 그날의 다음 실행(평일 8시 이후)에서 다시 확인합니다.

## 셀렉터가 깨졌을 때
7개 게시판 모두 같은 HTML 구조(`tbody tr` / `td.subject a` / `td.data`)를 씁니다.
학교가 개편하면 `crawler.py`의 `fetch_notices()` 안 셀렉터만 고치면 전 카테고리가 함께 복구됩니다.

## 식단표가 안 올 때
`menu.py`는 클래스 이름 대신 **표의 내용**으로 식단표를 찾습니다.
(요일/날짜처럼 생긴 칸과 조식·중식·석식처럼 생긴 칸이 가장 많은 표를 고릅니다.)
가로축이 요일인 표, 가로축이 끼니인 표, `rowspan`/`colspan`이 섞인 표를 모두 처리하고,
전용 페이지가 비어 있으면 **메인 페이지(`/00/0000.do`) 하단 식단**까지 훑습니다.
(이때는 표 바로 앞 제목에 "효민"/"행복"이 붙은 표만 각 기숙사 것으로 인정합니다.)

그래도 실패하면 먼저 로컬에서 확인하세요.

```bash
python menu.py                                   # 오늘 칸(👉)까지 표시하며 출력
curl -s https://dorm.deu.ac.kr/60/6050.do > menu.html   # 실제 HTML 확인
```

- 페이지 주소만 바뀐 경우: Actions → Variables에 `MENU_URL_HYOMIN`,
  `MENU_URL_HAPPY`를 등록하면 코드 수정 없이 새 주소를 씁니다.
- 표 구조가 완전히 바뀐 경우: `menu.py`의 `_parse_grid()`만 고치면 됩니다.
- 표는 읽었는데 오늘 칸만 못 찾는 경우: 날짜 표기가 특이한 것이니
  `menu.py`의 `_day_key()`를 고치면 됩니다.
