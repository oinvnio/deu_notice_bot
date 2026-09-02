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
    path: str       # 기숙사별 급식 안내 페이지 (메인 페이지가 비었을 때 예비로 훑음)
    color: int      # 임베드 왼쪽 띠 색상
    emoji: str      # 제목 앞에 붙일 이모지
    keyword: str    # 메인 페이지에서 이 관의 표를 골라낼 때 쓰는 단어


# 두 기숙사의 그날 식단이 나란히 올라오는 페이지. 매일 갱신됩니다.
# 한 페이지에 두 관이 함께 있으므로 표 앞 제목("효민생활관 식단" / "행복기숙사 식단")으로
# 어느 관 것인지 가려냅니다.
MENU_PATH = "/00/0000.do"

# 슬러그는 GitHub Secret 이름(WEBHOOK_MENU_<슬러그 대문자>)에 그대로 쓰입니다.
DORMS = {
    "hyomin": Dorm("효민생활관", "/60/6050.do", 0x2E8B57, "🍚", "효민"),
    "happy":  Dorm("행복기숙사", "/60/6051.do", 0xC05621, "🍱", "행복"),
}

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


TIME_RE = re.compile(r"\d{1,2}\s*:\s*\d{2}")


def _looks_like_hours(days: list[dict]) -> bool:
    """
    조식·중식·석식 행을 가졌지만 내용이 "07:30 ~ 09:00"인 표,
    즉 식당 **운영시간 안내표**를 걸러냅니다.
    급식 안내 페이지에는 이 표가 식단표와 나란히 있어서 헷갈립니다.
    """
    texts = [meal["text"] for day in days for meal in day["meals"]]
    if not texts:
        return True
    timed = sum(1 for t in texts if TIME_RE.search(t))
    return timed * 2 >= len(texts)


def _parse_daily(grid: list[list[str]]) -> list[dict] | None:
    """
    머리글이 조식·중식·석식이고 그 아래로 그날 메뉴만 들어 있는 표를 읽습니다.

        | 조식        | 중식        | 석식        |
        | 수제비국 …  | 얼큰버섯국… | 소불고기덮밥… |

    날짜가 표 안에 없으므로 day/key는 비워 두고, 부르는 쪽에서 표 옆 날짜로 채웁니다.
    주간 표(요일 열이 따로 있는 표)라면 None을 돌려 일반 경로에 넘깁니다.
    """
    if len(grid) < 2:
        return None

    header = grid[0]
    meal_cols = [c for c, text in enumerate(header) if _is_meal(text)]
    if len(meal_cols) < 2:
        return None

    body = [row for row in grid[1:] if any(cell.strip() for cell in row)]
    if not body:
        return None

    # 첫 칸이 끼니가 아니면서 아래로 요일이 이어지면 주간 표입니다.
    if not _is_meal(header[0]) and any(_is_day(row[0]) for row in body):
        return None

    meals = []
    for c in meal_cols:
        text = "\n".join(row[c] for row in body if row[c].strip()).strip()
        if text:
            meals.append({"name": header[c].replace("\n", " ").strip(), "text": text})

    return [{"day": "", "key": "", "meals": meals}] if meals else None


DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def _nearby_date(table, depth: int = 10) -> tuple[str, str]:
    """
    표 바깥(대개 제목 오른쪽)에 적힌 날짜를 찾아 (표시용 문자열, "MM-DD")로 돌려줍니다.
    하루치 표에는 날짜 칸이 없어서 이 값으로 오늘 것인지 판단합니다.
    """
    node = table
    for _ in range(depth):
        node = node.find_previous(
            ["caption", "h1", "h2", "h3", "h4", "h5", "strong", "b", "span", "p", "li", "td", "div"]
        )
        if node is None:
            break
        m = DATE_RE.search(node.get_text(" ", strip=True))
        if m:
            year, month, day = (int(x) for x in m.groups())
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}.{month:02d}.{day:02d}", f"{month:02d}-{day:02d}"
    return "", ""


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
            grid = _grid(table)
            # 하루치 표를 먼저 시도합니다(주간 표면 None이 돌아와 일반 경로로 갑니다).
            days = _parse_daily(grid) or _parse_grid(grid)
            if not days or _looks_like_hours(days):
                continue
            if len(days) == 1 and not days[0]["key"]:
                days[0]["day"], days[0]["key"] = _nearby_date(table)
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
    # 요일은 "월요일", "(월)", 또는 칸 전체가 "월"일 때만 인정합니다.
    # 그냥 포함 여부로 보면 "주말 및 공휴일"의 '일'을 일요일로 잘못 읽습니다.
    m = re.search(f"([{WEEKDAYS}])요일", label) or re.search(f"\\(([{WEEKDAYS}])\\)", label)
    if m:
        return m.group(1)
    stripped = label.strip()
    return stripped if stripped in WEEKDAYS else ""


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


# ── 식당 운영시간 (고정값) ─────────────────────────────────────────────
#
# 급식 안내 페이지의 운영시간 안내표를 보고 옮겨 적은 값입니다.
#   효민생활관: https://dorm.deu.ac.kr/60/6050.do
#   행복기숙사: https://dorm.deu.ac.kr/60/6051.do
# 시간이 바뀌면 이 표만 고치면 됩니다. (확인일: 2026-09-02)
#
# 학기/방학은 달로 어림잡습니다(1·2·7·8월을 방학으로 봄). 개강·종강 시점이
# 어긋날 수 있어서 고른 구분 이름("학기 중 평일" 등)을 항상 함께 보여줍니다.

_HYOMIN_TERM_WEEKEND = ("학기 중 주말·공휴일", "조식 미운영 · 중식 11:30~13:30 · 석식 17:00~18:30")
_HAPPY_OFF = ("방학·주말·공휴일", "조식 08:00~09:30 · 중식 11:30~13:30 · 석식 17:00~18:30")

MEAL_HOURS: dict[str, dict[tuple[str, str], tuple[str, str]]] = {
    "hyomin": {
        ("학기", "평일"): ("학기 중 평일", "조식 07:30~09:00 · 중식 12:00~13:30 · 석식 17:00~19:00"),
        ("학기", "주말"): _HYOMIN_TERM_WEEKEND,
        ("방학", "평일"): ("방학 중 평일", "조식 07:30~09:00 · 중식 12:00~13:30 · 석식 17:00~18:30"),
        ("방학", "주말"): ("방학 중 주말", "운영 안함"),
    },
    "happy": {
        ("학기", "평일"): ("평일", "조식 07:30~09:00 · 중식 11:30~14:00 · 석식 16:50~19:00"),
        ("학기", "주말"): _HAPPY_OFF,
        ("방학", "평일"): _HAPPY_OFF,
        ("방학", "주말"): _HAPPY_OFF,
    },
}

VACATION_MONTHS = (1, 2, 7, 8)


def hours_for(slug: str, today: datetime | None = None) -> tuple[str, str] | None:
    """오늘에 해당하는 (구분 이름, 운영시간 한 줄)을 돌려줍니다."""
    now = today or datetime.now(KST)
    season = "방학" if now.month in VACATION_MONTHS else "학기"
    kind = "주말" if now.weekday() >= 5 else "평일"
    return MEAL_HOURS.get(slug, {}).get((season, kind))


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

    # 메인 페이지에 두 관 식단이 함께 있으므로 관 이름이 붙은 표만 인정합니다.
    url = os.environ.get(f"MENU_URL_{slug.upper()}", "").strip() or BASE_URL + MENU_PATH
    days = _select_table(_soup(url), dorm.keyword, require_keyword=True)

    # 거기서 못 찾으면 기숙사별 급식 안내 페이지를 훑어봅니다.
    if not days:
        url = BASE_URL + dorm.path
        days = _select_table(_soup(url), dorm.keyword)

    return {
        "dorm": slug,
        "label": dorm.label,
        "url": url,
        "days": days,
    }


def diagnose(url: str) -> None:
    """
    식단표를 못 찾을 때 페이지에 무엇이 있는지 훑어봅니다.
    `python menu.py --tables` 로 실행합니다.
    """
    print(f"\n### {url}")
    try:
        soup = _soup(url)
    except Exception as e:
        print(f"  가져오기 실패: {e}")
        return

    tables = soup.find_all("table")
    print(f"  표 {len(tables)}개")
    for i, table in enumerate(tables):
        grid = _grid(table)
        if not grid:
            print(f"  [{i}] 빈 표")
            continue
        daily = _parse_daily(grid)
        days = daily or _parse_grid(grid)
        if not days:
            verdict = "식단 아님"
        elif _looks_like_hours(days):
            verdict = "운영시간표(제외)"
        elif daily:
            label, _ = _nearby_date(table)
            verdict = f"하루치 식단 후보 (표 옆 날짜: {label or '못 찾음'}, 주인: {_owner(table) or '?'})"
        else:
            verdict = f"주간 식단 후보 {len(days)}일치 (주인: {_owner(table) or '?'})"
        print(f"  [{i}] {len(grid)}행 x {len(grid[0])}열 · {verdict}")
        for r, cells in enumerate(grid[:3]):
            line = " | ".join(c.replace("\n", " ") for c in cells[:7])
            print(f"        {r}행: {line[:100]}")

    # 실제 식단표 페이지로 가는 링크가 어디 있는지 찾습니다.
    hits = []
    for a in soup.find_all("a"):
        text = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if any(k in text for k in ("식단", "메뉴", "급식")) or any(
            k in href.lower() for k in ("diet", "menu", "food", "meal")
        ):
            hits.append((text[:40] or "(글자 없음)", href[:120]))
    print(f"  식단 관련 링크 {len(hits)}개")
    for text, href in hits[:25]:
        print(f"     - {text} → {href}")


if __name__ == "__main__":
    # 이모지가 섞인 출력을 한글 Windows(cp949 콘솔)에서도 안전하게 찍습니다.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if "--tables" in sys.argv:
        # 페이지에 어떤 표와 링크가 있는지 그대로 보여줍니다.
        for url in [BASE_URL + MENU_PATH] + [BASE_URL + d.path for d in DORMS.values()]:
            diagnose(url)
        sys.exit(0)

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
