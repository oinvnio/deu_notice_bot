import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from crawler import CATEGORIES, Category, fetch_notices
from menu import DORMS, KST, Dorm, fetch_menu, pick_today

# 이모지가 섞인 공지 제목을 출력해도 죽지 않도록 (Windows 콘솔 등)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 설정 ──────────────────────────────────────────────────────────────
SEEN_FILE = Path("seen.json")
MAX_FIRST_RUN = 5     # 카테고리를 처음 켰을 때 최신 N개만 전송 (스팸 방지)
MAX_SEEN = 300        # 카테고리별 seen 보관 개수 (파일 무한 증가 방지)
SEND_INTERVAL = 0.5   # 연속 전송 간격(초), 디스코드 레이트리밋 회피

# 식단표는 공지와 성격이 달라 seen.json 안에서 "menu:" 슬롯에 따로 기록합니다.
# (공지는 공지 ID, 식단은 전송한 날짜를 넣습니다.)
MENU_SLOT = "menu:"
MENU_ALERT_SLOT = "menu:_alerts"
MENU_MAX_FIELDS = 6      # 임베드 하나에 담을 끼니 수
MENU_MAX_CHARS = 4500    # 임베드 하나의 글자 수 상한 (디스코드 한도 6000)

# 카테고리별 웹후크가 없으면 이 값으로 대체합니다(전부 한 채널로 전송).
FALLBACK_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 쉼표로 구분된 관심 키워드. 비어 있으면 모든 공지를 전송합니다.
# 예) NOTICE_KEYWORDS="장학,수강신청,등록금"
KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("NOTICE_KEYWORDS", "").split(",")
    if k.strip()
]
# ──────────────────────────────────────────────────────────────────────


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def webhook_for(slug: str) -> str:
    """카테고리 전용 웹후크(WEBHOOK_GENERAL 등), 없으면 공용 웹후크."""
    return os.environ.get(f"WEBHOOK_{slug.upper()}", "").strip() or FALLBACK_WEBHOOK


def menu_webhook_for(slug: str) -> str:
    """식단 전용 채널 → 식단 공용 → 기숙사 공지 채널 → 전체 공용 순으로 찾습니다."""
    return (
        os.environ.get(f"WEBHOOK_MENU_{slug.upper()}", "").strip()
        or os.environ.get("WEBHOOK_MENU", "").strip()
        or webhook_for("dormitory")
    )


def load_seen() -> dict[str, set[str]]:
    if not SEEN_FILE.exists():
        return {}
    data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    # 구버전 형식(플랫 리스트)은 '일반' 카테고리 기록으로 취급합니다.
    if isinstance(data, list):
        return {"general": set(data)}
    return {slug: set(ids) for slug, ids in data.items()}


def save_seen(seen: dict[str, set[str]]) -> None:
    # 공지 ID(articleNo)는 커질수록 최신이므로 숫자 기준 최신 MAX_SEEN개만 남깁니다.
    out = {
        # 숫자가 아닌 키(식단 주차 등)는 사전순으로 고정해 실행마다 파일이 흔들리지 않게 합니다.
        slug: sorted(ids, key=lambda i: (int(i) if i.isdigit() else -1, i))[-MAX_SEEN:]
        for slug, ids in sorted(seen.items())
    }
    SEEN_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def matches_keywords(notice: dict) -> bool:
    if not KEYWORDS:
        return True
    title = notice["title"].lower()
    return any(k in title for k in KEYWORDS)


def send_discord(notice: dict, webhook: str, cat: Category) -> None:
    embed = {
        # 제목 앞 이모지는 알림·채널 목록에서 카테고리를 한눈에 구분하기 위한 것입니다.
        "title": f"{cat.emoji} {notice['title']}",
        "url": notice["url"],
        "color": cat.color,
        "footer": {"text": f"📅 {notice['date']} · 동의대 {cat.label} 공지"},
    }
    resp = requests.post(webhook, json={"embeds": [embed]}, timeout=10)
    resp.raise_for_status()


def send_alert(message: str, webhook: str) -> None:
    """봇 자체가 고장났을 때 디스코드로 경고를 보냅니다."""
    print(f"⚠️ {message}")
    if not webhook:
        return
    embed = {
        "title": "⚠️ 공지 봇 오류",
        # 예외 메시지가 길어도 디스코드 한도(4096자)를 넘지 않게 자릅니다.
        "description": _clip(message, 3900),
        "color": 0xD83C3E,
    }
    try:
        requests.post(webhook, json={"embeds": [embed]}, timeout=10)
    except requests.RequestException as e:
        print(f"경고 전송도 실패했습니다: {e}")


def process_category(slug: str, seen: dict[str, set[str]]) -> bool:
    """한 카테고리를 크롤링해 새 공지를 전송합니다. 성공하면 True."""
    cat = CATEGORIES[slug]
    label = cat.label
    webhook = webhook_for(slug)

    try:
        notices = fetch_notices(slug)
    except Exception as e:
        send_alert(f"[{label}] 공지 페이지를 가져오지 못했습니다: `{e}`", webhook)
        return False

    # 파싱 결과가 0건이면 사이트 개편으로 셀렉터가 깨졌을 가능성이 큽니다.
    # 조용히 종료하면 아무도 눈치채지 못하므로 반드시 알립니다.
    if not notices:
        send_alert(
            f"[{label}] 공지를 한 건도 파싱하지 못했습니다. "
            "홈페이지 개편으로 `crawler.py`의 셀렉터가 깨졌을 수 있습니다.",
            webhook,
        )
        return False

    known = seen.setdefault(slug, set())
    is_first_run = not known

    new_notices = [n for n in notices if n["id"] not in known]
    if is_first_run:
        new_notices = new_notices[:MAX_FIRST_RUN]
        print(f"[{label}] 첫 실행: 최신 {MAX_FIRST_RUN}개만 확인")

    to_send = [n for n in new_notices if matches_keywords(n)]
    skipped = len(new_notices) - len(to_send)
    if skipped:
        print(f"[{label}] 키워드 불일치로 {skipped}건 건너뜀")

    # 오래된 것부터 전송 (시간순)
    for i, notice in enumerate(reversed(to_send)):
        if i:
            time.sleep(SEND_INTERVAL)
        print(f"[{label}] 전송: {notice['title']}")
        try:
            send_discord(notice, webhook, cat)
        except requests.RequestException as e:
            # 실패한 공지는 seen에 넣지 않아 다음 실행에서 다시 시도합니다.
            send_alert(f"[{label}] 디스코드 전송 실패: `{e}`", webhook)
            return False
        known.add(notice["id"])

    # 전송이 모두 끝났을 때만 나머지(키워드 불일치·첫 실행 제외분)를 본 것으로 처리합니다.
    known.update(n["id"] for n in notices)
    print(f"[{label}] {len(to_send)}건 전송 완료.")
    return True


# ── 오늘의 식단 ────────────────────────────────────────────────────────

def build_menu_embeds(dorm: Dorm, menu: dict, day: dict) -> list[dict]:
    """끼니 하나를 필드 하나로 만들고, 디스코드 한도에 맞춰 임베드를 나눕니다."""
    fields = [
        {
            "name": _clip(meal["name"] or "\u200b", 256),
            "value": _clip(meal["text"] or "-", 1024),
            # 조식·중식·석식이 한 줄에 나란히 보이도록 합니다.
            "inline": True,
        }
        for meal in day["meals"]
    ]

    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for field in fields:
        cost = len(field["name"]) + len(field["value"])
        if current and (len(current) >= MENU_MAX_FIELDS or size + cost > MENU_MAX_CHARS):
            chunks.append(current)
            current, size = [], 0
        current.append(field)
        size += cost
    if current:
        chunks.append(current)

    embeds = []
    for i, chunk in enumerate(chunks):
        embed = {
            "title": _clip(
                f"{dorm.emoji} {dorm.label} 오늘의 식단" + (" (이어서)" if i else ""), 256
            ),
            "color": dorm.color,
            "fields": chunk,
        }
        if i == 0:
            # url은 첫 임베드에만 답니다. 같은 url이 여러 임베드에 붙으면 디스코드가 합쳐 버립니다.
            embed["url"] = menu["url"]
            if day["day"]:
                embed["description"] = f"📅 {_clip(day['day'], 200)}"
        if i == len(chunks) - 1:
            embed["footer"] = {"text": f"동의대 {dorm.label} 식단"}
        embeds.append(embed)
    return embeds


def send_menu(embeds: list[dict], webhook: str) -> None:
    # 한 메시지에 임베드는 10개까지만 담을 수 있습니다.
    for i in range(0, len(embeds), 10):
        if i:
            time.sleep(SEND_INTERVAL)
        resp = requests.post(webhook, json={"embeds": embeds[i:i + 10]}, timeout=15)
        resp.raise_for_status()


def alert_once_a_day(message: str, webhook: str, seen: dict[str, set[str]], slug: str) -> bool:
    """
    식단 크롤링 실패는 30분마다 반복되므로 하루 한 번만 알립니다.
    (같은 경고가 채널을 도배하면 정작 봐야 할 때 보지 않게 됩니다.)
    """
    today = datetime.now(KST).strftime("%Y-%m-%d")
    fired = {mark for mark in seen.get(MENU_ALERT_SLOT, set()) if mark.endswith(today)}
    mark = f"{slug}:{today}"
    if mark in fired:
        print(f"⚠️ {message} (오늘 이미 알림을 보냈습니다)")
    else:
        send_alert(message, webhook)
        fired.add(mark)
    seen[MENU_ALERT_SLOT] = fired
    return False


def process_menu(slug: str, seen: dict[str, set[str]]) -> bool:
    """한 기숙사의 오늘 식단을 (아직 안 보냈다면) 전송합니다."""
    dorm = DORMS[slug]
    webhook = menu_webhook_for(slug)
    today = datetime.now(KST).strftime("%Y-%m-%d")

    sent = seen.setdefault(MENU_SLOT + slug, set())
    if today in sent:
        print(f"[{dorm.label} 식단] 오늘({today}) 식단은 이미 전송했습니다.")
        return True

    try:
        menu = fetch_menu(slug)
    except Exception as e:
        return alert_once_a_day(
            f"[{dorm.label} 식단] 페이지를 가져오지 못했습니다: `{e}`", webhook, seen, slug
        )

    if not menu["days"]:
        return alert_once_a_day(
            f"[{dorm.label} 식단] 식단표를 찾지 못했습니다. "
            f"홈페이지 개편으로 표 구조가 바뀌었을 수 있습니다: {menu['url']}",
            webhook, seen, slug,
        )

    day = pick_today(menu["days"])
    if day is None:
        # 아직 이번 주 식단이 안 올라왔거나, 오늘은 식당을 운영하지 않는 날입니다.
        # 둘 다 정상적인 상황이라 경고하지 않고 다음 실행에서 다시 확인합니다.
        print(
            f"[{dorm.label} 식단] 오늘 칸이 없습니다. "
            f"(표에 있는 날: {', '.join(d['day'] for d in menu['days'])})"
        )
        return True

    print(f"[{dorm.label} 식단] 전송: {day['day']} ({len(day['meals'])}끼니)")
    try:
        send_menu(build_menu_embeds(dorm, menu, day), webhook)
    except requests.RequestException as e:
        # 날짜를 기록하지 않으므로 다음 실행에서 다시 시도합니다.
        send_alert(f"[{dorm.label} 식단] 디스코드 전송 실패: `{e}`", webhook)
        return False

    sent.add(today)
    return True


def safe_process_menu(slug: str, seen: dict[str, set[str]]) -> bool:
    try:
        return process_menu(slug, seen)
    except Exception as e:
        return alert_once_a_day(
            f"[{DORMS[slug].label} 식단] 처리 중 예기치 못한 오류: `{e}`",
            menu_webhook_for(slug), seen, slug,
        )


def safe_process(slug: str, seen: dict[str, set[str]]) -> bool:
    """한 카테고리에서 터진 예외가 다른 카테고리까지 중단시키지 않도록 격리합니다."""
    try:
        return process_category(slug, seen)
    except Exception as e:
        label = CATEGORIES[slug].label
        send_alert(f"[{label}] 처리 중 예기치 못한 오류: `{e}`", webhook_for(slug))
        return False


def main() -> None:
    active = [slug for slug in CATEGORIES if webhook_for(slug)]
    menu_active = [slug for slug in DORMS if menu_webhook_for(slug)]
    if not active and not menu_active:
        print(
            "❌ 웹후크가 하나도 설정되지 않았습니다. "
            "WEBHOOK_GENERAL 등 카테고리별 시크릿이나 DISCORD_WEBHOOK_URL을 등록하세요."
        )
        sys.exit(1)

    skipped = [slug for slug in CATEGORIES if slug not in active]
    if skipped:
        print(f"웹후크 미설정으로 건너뜀: {', '.join(skipped)}")

    seen = load_seen()
    failed = [slug for slug in active if not safe_process(slug, seen)]
    menu_failed = [slug for slug in menu_active if not safe_process_menu(slug, seen)]
    # 어떤 항목이 실패하든, 이미 전송에 성공한 기록은 반드시 남깁니다.
    save_seen(seen)

    # 식단표는 부가 기능이라 실패해도 워크플로까지 빨갛게 만들지 않습니다.
    # 대신 하루 한 번 디스코드로 경고가 갑니다(alert_once_a_day).
    if menu_failed:
        print(f"⚠️ 식단표 실패(워크플로는 계속 진행): {', '.join(menu_failed)}")

    if failed:
        print(f"❌ 실패한 카테고리: {', '.join(failed)}")
        sys.exit(1)
    print(f"✅ 공지 {len(active)}개 · 식단 {len(menu_active)}곳 확인 완료.")


if __name__ == "__main__":
    main()
