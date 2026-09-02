"""
기숙사 식단표 크롤러.

식단표는 한 주 단위로 올라오므로 주간 표를 통째로 읽은 뒤,
main.py가 pick_today()로 그날 칸만 뽑아 씁니다.

식단표 페이지는 공지 게시판과 달리 표(<table>) 한 장이 전부라서,
클래스 이름 대신 **표의 내용**으로 식단표를 찾아냅니다.
(요일/날짜처럼 생긴 칸 + 조식·중식·석식처럼 생긴 칸이 가장 많은 표를 고릅니다.)

홈페이지가 개편돼 클래스 이름이 바뀌어도 표 모양만 유지되면 계속 동작하고,
표 모양 자체가 바뀌면 빈 결과를 돌려주므로 main.py가 경고를 보냅니다.
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://dorm.deu.ac.kr"
KST = timezone(timedelta(hours=9))


class Dorm(NamedTuple):
    label: str      # 디스코드에 표시할 이름
    path: str       # 식단표 페이지 경로
    color: int      # 임베드 왼쪽 띠 색상
    emoji: str      # 제목 앞에 붙일 이모지
    keyword: str    # 메인 페이지에서 이 관의 표를 골라낼 때 쓰는 단어


# 슬러그는 GitHub Secret 이름(WEBHOOK_MENU_<슬러그 대문자>)에 그대로 쓰입니다.
DORMS = {
    "hyomin": Dorm("효민생활관", "/60/6050.do", 0x2E8B57, "🍚", "효민"),
    "happy":  Dorm("행복기숙사", "/60/6051.do", 0xC05621, "🍱", "행복"),
}

# 전용 식단표 페이지가 비어 있을 때 훑어볼 예비 주소.
# 메인 페이지 하단에도 식단이 매일 갱신되므로, 관 이름이 붙은 표만 골라 씁니다.
FALLBACK_PATH = "/00/0000.do"

# 표가 어느 관의 것인지 가려낼 때 쓰는 이름 목록
KEYWORDS = tuple(d.keyword for d in DORMS.values())

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# 표가 비정상적으로 커서(잘못된 rowspan 등) 메모리를 잡아먹지 않도록 하는 상한
MAX_SPAN = 20
MAX_ROWS = 200
MAX_COLS = 30

MEAL_WORDS = ("조식", "중식", "석식", "아침", "점심", "저녁", "브런치", "간편식", "특식")

_WS = re.compile(r"[ \t　]+")


# ── 셀/표 다루기 ────────────────────────────────────────────────────────

def _cell_text(cell) -> str:
    """<br>·<li>로 나뉜 메뉴를 줄바꿈으로 살려서 읽습니다."""
    raw = cell.get_text("\n", strip=True)
    lines = [_WS.sub(" ", ln).strip() for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _grid(table) -> list[list[str]]:
    """rowspan/colspan을 펼쳐서 직사각형 2차원 배열로 만듭니다."""
    cells: list[dict[int, str]] = []

    def row(i: int) -> dict[int, str]:
        while len(cells) <= i:
            cells.append({})
        return cells[i]

    for r, tr in enumerate(table.find_all("tr")[:MAX_ROWS]):
        col = 0
        for cell in tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"]):
            while col in row(r):
                col += 1
            if col >= MAX_COLS:
                break
            try:
                rowspan = min(int(cell.get("rowspan", 1) or 1), MAX_SPAN)
                colspan = min(int(cell.get("colspan", 1) or 1), MAX_SPAN)
            except ValueError:
                rowspan = colspan = 1
            text = _cell_text(cell)
            for dr in range(max(rowspan, 1)):
                if r + dr >= MAX_ROWS:
                    break
                for dc in range(max(colspan, 1)):
                    if col + dc < MAX_COLS:
                        row(r + dr)[col + dc] = text
            col += max(colspan, 1)

    width = max((max(c) + 1 for c in cells if c), default=0)
    return [[c.get(i, "") for i in range(width)] for c in cells]


def _is_day(text: str) -> bool:
    t = text.replace(" ", "").replace("\n", "")
    if not t or len(t) > 24:
        return False
    return bool(
        re.search(r"[월화수목금토일]요일", t)
        or re.search(r"\([월화수목금토일]\)", t)
        or re.fullmatch(r"[월화수목금토일]", t)
        or re.search(r"\d{1,2}[.\-/]\d{1,2}", t)
        or re.search(r"\d{1,2}월\s*\d{1,2}일", t)
    )


def _is_meal(text: str) -> bool:
    t = text.replace(" ", "").replace("\n", "")
    # 메뉴 이름에 '중식'(중국음식)이 들어가는 경우와 구분하기 위해 짧은 칸만 인정합니다.
    return bool(t) and len(t) <= 12 and any(w in t for w in MEAL_WORDS)


# ── 식단표 표 찾아서 파싱하기 ──────────────────────────────────────────

def _parse_grid(grid: list[list[str]]) -> list[dict]:
    """
    두 가지 배치를 모두 지원합니다.
      A) 가로=요일, 세로=조식/중식/석식  (대부분의 기숙사 식단표)
      B) 가로=조식/중식/석식, 세로=요일
    """
    if not grid or len(grid) < 2 or len(grid[0]) < 2:
        return []

    height, width = len(grid), len(grid[0])
    col0 = [grid[r][0] for r in range(height)]
    row0 = grid[0]

    score_a = sum(_is_day(c) for c in row0[1:]) * 2 + sum(_is_meal(c) for c in col0[1:])
    score_b = sum(_is_meal(c) for c in row0[1:]) + sum(_is_day(c) for c in col0[1:]) * 2

    if score_b > score_a:
        # 축을 뒤집어 A 배치로 통일합니다.
        grid = [[grid[r][c] for r in range(height)] for c in range(width)]
        height, width = width, height
        col0 = [grid[r][0] for r in range(height)]

    # 조식/중식/석식 줄이 시작되기 전까지를 머리글로 봅니다.
    # (날짜 줄 + 요일 줄처럼 머리글이 두 줄인 표도 이렇게 하면 함께 잡힙니다.)
    meal_rows = [r for r in range(height) if _is_meal(grid[r][0])]
    if not meal_rows:
        return []
    header_rows = [r for r in range(meal_rows[0]) if r < height] or [0]

    def header(c: int) -> str:
        seen: list[str] = []
        for r in header_rows:
            t = grid[r][c].replace("\n", " ").strip()
            if t and t not in seen:
                seen.append(t)
        return " ".join(seen)

    day_cols = [c for c in range(1, width) if _is_day(header(c))]
    if len(day_cols) < 2:
        # 요일 판정에 실패하면 비어 있지 않은 머리글을 전부 요일로 취급합니다.
        day_cols = [c for c in range(1, width) if header(c)]
    if not day_cols:
        return []

    days = []
    for c in day_cols:
        meals = [
            {"name": grid[r][0].replace("\n", " ").strip(), "text": grid[r][c]}
            for r in meal_rows
            if grid[r][c]
        ]
        if meals:
            label = header(c)
            days.append({"day": label, "key": _day_key(label), "meals": meals})
    return days


def _owner(table, depth: int = 12) -> str:
    """
    표가 어느 관의 것인지 판단합니다(메인 페이지처럼 두 관이 섞인 경우용).
    표 바로 앞 제목부터 거슬러 올라가며 **가장 가까운** 관 이름을 채택합니다.
    한 줄에 두 관이 함께 나오면(예: 상단 메뉴바) 판단 근거로 쓰지 않고 지나칩니다.
    """
    texts = []
    if table.caption:
        texts.append(table.caption.get_text(" ", strip=True))
    node = table
    for _ in range(depth):
        node = node.find_previous(
            ["caption", "h1", "h2", "h3", "h4", "h5", "strong", "b", "a", "li", "p", "span"]
        )
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if text:
            texts.append(text[:120])

    for text in texts:
        hits = [kw for kw in KEYWORDS if kw in text]
        if len(hits) == 1:
            return hits[0]
    return ""


def _select_table(soup, keyword: str = "", require_keyword: bool = False) -> list[dict]:
    """
    페이지의 모든 표를 훑어 식단표로 가장 그럴듯한 것을 고릅니다.

    keyword: 표 주변 제목에 이 단어가 있으면 우선합니다(예: "효민", "행복").
    require_keyword: 두 관의 식단이 한 페이지에 섞여 있는 메인 페이지처럼
                     엉뚱한 관의 표를 집어오면 안 되는 경우에 켭니다.
    """
    tables = soup.find_all("table")
    # 레이아웃용 바깥 표를 피하려고, 표를 품지 않은 표부터 살펴봅니다.
    leaves = [t for t in tables if not t.find("table")]

    for group in (leaves, tables):
        best: list[dict] = []
        best_score = 0
        for table in group:
            matched = bool(keyword) and _owner(table) == keyword
            if require_keyword and not matched:
                continue
            days = _parse_grid(_grid(table))
            if not days:
                continue
            score = len(days) + (100 if matched else 0)
            if score > best_score:
                best, best_score = days, score
        if best:
            return best
    return []


# ── 오늘 날짜 고르기 ───────────────────────────────────────────────────

WEEKDAYS = "월화수목금토일"


def _day_key(label: str) -> str:
    """
    요일 머리글에서 날짜를 뽑아 "MM-DD"로 만듭니다.
    날짜가 없고 요일만 적힌 표라면 요일 한 글자("월")를 씁니다.
    """
    # "2026.09.07"처럼 연도가 붙은 형태를 먼저 봅니다.
    # (연도를 먼저 걸러내지 않으면 "26.09"를 월·일로 잘못 읽습니다.)
    for pattern in (
        r"20\d{2}\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
        r"(\d{1,2})\s*[.\-/월]\s*(\d{1,2})",
    ):
        m = re.search(pattern, label)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{month:02d}-{day:02d}"
            break
    m = re.search(f"[{WEEKDAYS}]", label)
    return m.group(0) if m else ""


def pick_today(days: list[dict], today: datetime | None = None) -> dict | None:
    """
    주간 표에서 오늘(KST) 칸을 골라냅니다. 오늘 칸이 없으면 None.

    날짜가 적힌 표는 날짜로 맞춥니다. 지난주 식단이 그대로 걸려 있어도
    날짜가 어긋나면 보내지 않으므로 묵은 식단을 전송할 일이 없습니다.
    날짜 없이 요일만 적힌 표일 때만 요일로 맞춥니다.
    """
    now = today or datetime.now(KST)
    dated = [d for d in days if re.fullmatch(r"\d{2}-\d{2}", d["key"])]

    if dated:
        wanted = now.strftime("%m-%d")
        return next((d for d in dated if d["key"] == wanted), None)

    wanted = WEEKDAYS[now.weekday()]
    return next((d for d in days if d["key"] == wanted), None)


# ── 공개 API ───────────────────────────────────────────────────────────

def _soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    # 인코딩(euc-kr/utf-8)은 bs4가 meta charset을 보고 판단하도록 원본 바이트를 넘깁니다.
    return BeautifulSoup(resp.content, "html.parser")


def fetch_menu(slug: str) -> dict:
    """
    지정한 기숙사의 이번 주 식단표를 크롤링합니다.
    반환값: {"dorm", "label", "url", "days"}
            days = [{"day": "9/1(월)", "key": "09-01",
                     "meals": [{"name": "조식", "text": "..."}]}]
    파싱에 실패하면 days가 빈 리스트입니다(main.py가 경고를 보냅니다).
    """
    if slug not in DORMS:
        raise ValueError(f"알 수 없는 기숙사: {slug}")

    dorm = DORMS[slug]
    url = os.environ.get(f"MENU_URL_{slug.upper()}", "").strip() or BASE_URL + dorm.path

    days = _select_table(_soup(url), dorm.keyword)

    # 전용 페이지에서 못 찾으면 메인 페이지 하단 식단을 훑습니다.
    # 여기엔 두 관의 표가 함께 있으므로 관 이름이 붙은 표만 인정합니다.
    if not days and dorm.path != FALLBACK_PATH:
        url = BASE_URL + FALLBACK_PATH
        days = _select_table(_soup(url), dorm.keyword, require_keyword=True)

    return {
        "dorm": slug,
        "label": dorm.label,
        "url": url,
        "days": days,
    }


if __name__ == "__main__":
    ok = True
    for slug, dorm in DORMS.items():
        menu = fetch_menu(slug)
        print(f"\n{dorm.emoji} [{dorm.label}] {menu['url']} → {len(menu['days'])}일치")
        if not menu["days"]:
            ok = False
            print("  ⚠️ 식단표를 찾지 못했습니다. 페이지 구조를 확인해 주세요:")
            print(f"     curl -s {menu['url']} > menu.html")
            continue

        today = pick_today(menu["days"])
        for day in menu["days"]:
            mark = "👉" if day is today else "  "
            print(f"  {mark} {day['day']}  (키: {day['key']})")
            for meal in day["meals"]:
                body = meal["text"].replace("\n", ", ")
                print(f"        {meal['name']}: {body[:60]}")
        if today is None:
            print("  ⚠️ 오늘 칸을 찾지 못했습니다(아직 갱신 전이거나 오늘은 미운영).")
    sys.exit(0 if ok else 1)
