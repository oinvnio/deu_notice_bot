import re
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

    # tbody > tr 전체 선택
    rows = soup.select("tbody tr")

    for row in rows:
        # 제목: td.subject > a
        a_tag = row.select_one("td.subject a")
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
            # 상대경로 쿼리스트링 형식 (?mode=view&...)
            url = "https://www.deu.ac.kr/www/deu-notice.do" + href

        # 날짜: td.data
        date_td = row.select_one("td.data")
        date = date_td.get_text(strip=True) if date_td else ""

        # 고유 ID: articleNo 파라미터 추출
        m = re.search(r"articleNo=(\d+)", href)
        notice_id = m.group(1) if m else href.strip() or title

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
