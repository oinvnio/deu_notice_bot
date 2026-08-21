import json
import os
import sys
import time
from pathlib import Path

import requests

from crawler import CATEGORIES, Category, fetch_notices

# 이모지가 섞인 공지 제목을 출력해도 죽지 않도록 (Windows 콘솔 등)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 설정 ──────────────────────────────────────────────────────────────
SEEN_FILE = Path("seen.json")
MAX_FIRST_RUN = 5     # 카테고리를 처음 켰을 때 최신 N개만 전송 (스팸 방지)
MAX_SEEN = 300        # 카테고리별 seen 보관 개수 (파일 무한 증가 방지)
SEND_INTERVAL = 0.5   # 연속 전송 간격(초), 디스코드 레이트리밋 회피

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


def webhook_for(slug: str) -> str:
    """카테고리 전용 웹후크(WEBHOOK_GENERAL 등), 없으면 공용 웹후크."""
    return os.environ.get(f"WEBHOOK_{slug.upper()}", "").strip() or FALLBACK_WEBHOOK


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
        slug: sorted(ids, key=lambda i: int(i) if i.isdigit() else -1)[-MAX_SEEN:]
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
        "description": message,
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
    if not active:
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
    # 어떤 카테고리가 실패하든, 이미 전송에 성공한 기록은 반드시 남깁니다.
    save_seen(seen)

    if failed:
        print(f"❌ 실패한 카테고리: {', '.join(failed)}")
        sys.exit(1)
    print(f"✅ {len(active)}개 카테고리 확인 완료.")


if __name__ == "__main__":
    main()
