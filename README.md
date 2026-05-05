# 동의대학교 공지사항 Discord 봇

학교 공지사항을 주기적으로 크롤링해서 Discord 웹후크로 전송합니다.

## 파일 구조

```
├── crawler.py          # 공지사항 크롤링
├── main.py             # 새 공지 감지 & 디스코드 전송
├── seen.json           # 이미 전송한 공지 ID 저장 (중복 방지)
└── .github/
    └── workflows/
        └── notify.yml  # GitHub Actions 스케줄러
```

## 설치 & 실행 방법

### 1. 레포지토리 생성
GitHub에 새 **private** 레포를 만들고 이 파일들을 올립니다.

### 2. Discord 웹후크 생성
1. 디스코드 채널 설정 → **연동** → **웹후크** → **새 웹후크**
2. 웹후크 URL 복사

### 3. GitHub Secret 등록
레포 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Value: 복사한 웹후크 URL

### 4. 셀렉터 확인 (중요!)
`crawler.py`의 셀렉터가 실제 홈페이지 HTML과 맞는지 확인하세요.

1. https://www.deu.ac.kr/www/deu-notice.do 접속
2. 공지 행에서 F12 → 우클릭 → 검사
3. 실제 태그/클래스 확인 후 `crawler.py` 수정

**확인할 부분:**
```python
# 공지 목록 테이블 행 선택
rows = soup.select("table.board-list tbody tr:not(.notice)")
#                   ^^^^^^^^^^^^^^ 실제 테이블 클래스로 변경

title_td = cols[1]   # 제목이 몇 번째 td인지 확인
date = cols[-1]      # 날짜가 마지막 td인지 확인
```

### 5. 수동 테스트
Actions 탭 → **DEU Notice Bot** → **Run workflow** 버튼으로 즉시 실행 가능

## 실행 주기
평일 오전 8시 ~ 오후 6시, **30분마다** 자동 실행됩니다.
(GitHub Actions 무료 플랜: 월 2,000분 → 충분히 여유 있음)
