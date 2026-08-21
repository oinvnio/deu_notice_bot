import re
from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.deu.ac.kr"


class Category(NamedTuple):
    label: str    # 디스코드에 표시할 이름
    path: str     # 게시판 경로
    color: int    # 임베드 왼쪽 띠 색상
    emoji: str    # 공지 제목 앞에 붙일 이모지


# 슬러그는 GitHub Secret 이름(WEBHOOK_<슬러그 대문자>)에 그대로 쓰입니다.
CATEGORIES = {
    "general":     Category("일반",      "/www/deu-notice.do",      0x0057A8, "📢"),
    "scholarship": Category("장학",      "/www/deu-scholarship.do", 0x2E8B57, "💰"),
    "education":   Category("교육·모집", "/www/deu-education.do",   0x8A2BE2, "📚"),
    "dormitory":   Category("기숙사",    "/www/deu-dormitory.do",   0xD2691E, "🏠"),
    "job":         Category("채용",      "/www/deu-job.do",         0x1E90FF, "💼"),
    "bids":        Category("입찰",      "/www/deu-bids.do",        0x708090, "📋"),
    "external":    Category("외부기관",  "/www/deu-external.do",    0xB8860B, "🌐"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_notices(slug: str = "general") -> list[dict]:
    """
    지정한 카테고리의 공지 목록을 크롤링합니다.
    반환값: [{"id", "title", "date", "url", "category"}, ...]
    """
    if slug not in CATEGORIES:
        raise ValueError(f"알 수 없는 카테고리: {slug}")

    cat = CATEGORIES[slug]
    board_url = BASE_URL + cat.path

    resp = requests.get(board_url, headers=HEADERS, timeout=15)
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
            url = BASE_URL + href
        else:
            # 상대경로 쿼리스트링 형식 (?mode=view&...) → 해당 게시판 기준
            url = board_url + href

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
            "category": cat.label,
        })

    return notices


if __name__ == "__main__":
    for slug, cat in CATEGORIES.items():
        items = fetch_notices(slug)
        print(f"{cat.emoji} [{cat.label}] {len(items)}건")
        for n in items[:3]:
            print(f"  {n['id']} | {n['date']} | {n['title'][:40]}")
