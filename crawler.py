import requests
from bs4 import BeautifulSoup

NOTICE_URL = "https://www.deu.ac.kr/www/deu-notice.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_notices() -> list[dict]:
    """
    동의대학교 공지사항 페이지를 크롤링하여 공지 목록을 반환합니다.
    반환값: [{"id": str, "title": str, "date": str, "url": str}, ...]
    """
    resp = requests.get(NOTICE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    notices = []

    # ---------------------------------------------------------------
    # ⚠️  셀렉터 조정 안내
    # 브라우저에서 https://www.deu.ac.kr/www/deu-notice.do 열고
    # F12 → 공지 행(tr/li) 우클릭 → "검사" 로 실제 태그 확인 후
    # 아래 셀렉터를 수정하세요.
    # ---------------------------------------------------------------

    # 공지 목록 테이블 행 선택 (고정공지 제외)
    rows = soup.select("table.board-list tbody tr:not(.notice)")

    for row in rows:
        cols = row.select("td")
        if len(cols) < 2:
            continue

        # 제목 셀 (보통 두 번째 td)
        title_td = cols[1]
        a_tag = title_td.select_one("a")
        if not a_tag:
            continue

        title = a_tag.get_text(strip=True)

        # 링크 생성
        href = a_tag.get("href", "")
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = "https://www.deu.ac.kr" + href
        else:
            url = NOTICE_URL

        # 날짜 (보통 마지막 td)
        date = cols[-1].get_text(strip=True)

        # 고유 ID: URL 쿼리스트링 또는 행 data 속성 활용
        notice_id = href.strip() or title

        notices.append({
            "id": notice_id,
            "title": title,
            "date": date,
            "url": url,
        })

    return notices


if __name__ == "__main__":
    for n in fetch_notices():
        print(n)
