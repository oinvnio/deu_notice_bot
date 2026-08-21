# 동의대학교 공지사항 Discord 봇

학교 공지사항을 **카테고리별로** 크롤링해서 각각 다른 Discord 채널로 전송합니다.

## 지원 카테고리

| 카테고리 | 게시판 | GitHub Secret 이름 |
|---|---|---|
| 일반 | `/www/deu-notice.do` | `WEBHOOK_GENERAL` |
| 장학 | `/www/deu-scholarship.do` | `WEBHOOK_SCHOLARSHIP` |
| 교육·모집 | `/www/deu-education.do` | `WEBHOOK_EDUCATION` |
| 기숙사 | `/www/deu-dormitory.do` | `WEBHOOK_DORMITORY` |
| 채용 | `/www/deu-job.do` | `WEBHOOK_JOB` |
| 입찰 | `/www/deu-bids.do` | `WEBHOOK_BIDS` |
| 외부기관 | `/www/deu-external.do` | `WEBHOOK_EXTERNAL` |

- 시크릿을 등록한 카테고리만 동작합니다. 등록하지 않은 카테고리는 조용히 건너뜁니다.
- 카테고리 전용 시크릿이 없으면 공용 `DISCORD_WEBHOOK_URL`로 전송됩니다.
  (전부 한 채널에서 받고 싶다면 공용 시크릿 하나만 등록하세요.)

## 파일 구조

```
├── crawler.py          # 카테고리별 공지 크롤링 (CATEGORIES에 게시판 정의)
├── main.py             # 새 공지 감지 & 채널별 디스코드 전송
├── requirements.txt    # 의존성
├── seen.json           # 카테고리별 전송 완료 공지 ID (중복 방지)
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
python crawler.py     # 카테고리별 수집 결과를 출력 (디스코드 전송 없음)
```

## 동작 방식

- **첫 실행 스팸 방지**: 카테고리를 새로 켜면 최신 5건만 전송합니다.
- **중복 방지**: 전송한 공지 ID를 `seen.json`에 카테고리별로 기록하고,
  카테고리당 최근 300건까지만 보관합니다.
- **고장 알림**: 크롤링이 실패하거나 공지를 **한 건도 파싱하지 못하면**
  해당 채널로 ⚠️ 경고를 보내고 워크플로를 실패 처리합니다.
  (학교 홈페이지 개편으로 `crawler.py`의 셀렉터가 깨진 경우를 잡기 위한 장치입니다.)
- **전송 실패 재시도**: 디스코드 전송이 실패한 공지는 `seen.json`에 기록하지 않으므로
  다음 실행에서 다시 시도합니다.

## 실행 주기
평일 오전 8시 ~ 오후 6시, **30분마다** 자동 실행됩니다.

## 셀렉터가 깨졌을 때
7개 게시판 모두 같은 HTML 구조(`tbody tr` / `td.subject a` / `td.data`)를 씁니다.
학교가 개편하면 `crawler.py`의 `fetch_notices()` 안 셀렉터만 고치면 전 카테고리가 함께 복구됩니다.
