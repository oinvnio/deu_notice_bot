import json
import os
import sys
from pathlib import Path

import requests

from crawler import fetch_notices

# ── 설정 ──────────────────────────────────────────────────────────────
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")   # GitHub Secret
SEEN_FILE = Path("seen.json")
MAX_FIRST_RUN = 5   # 처음 실행 시 최신 N개만 전송 (스팸 방지)
# ──────────────────────────────────────────────────────────────────────


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_discord(notice: dict) -> None:
    embed = {
        "title": notice["title"],
        "url": notice["url"],
        "color": 0x0057A8,   # 동의대 블루
        "footer": {"text": f"📅 {notice['date']} · 동의대학교 공지사항"},
    }
    payload = {"embeds": [embed]}
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()


def main() -> None:
    if not WEBHOOK_URL:
        print("❌ DISCORD_WEBHOOK_URL 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    notices = fetch_notices()
    if not notices:
        print("공지사항을 가져오지 못했습니다.")
        sys.exit(0)

    seen = load_seen()
    is_first_run = len(seen) == 0

    new_notices = [n for n in notices if n["id"] not in seen]

    # 첫 실행이면 최신 N개만, 이후엔 모두 전송
    if is_first_run:
        new_notices = new_notices[:MAX_FIRST_RUN]
        print(f"첫 실행: 최신 {MAX_FIRST_RUN}개 공지 전송")

    if not new_notices:
        print("새 공지사항 없음.")
        # seen 업데이트 (모든 현재 공지를 이미 봤다고 기록)
        for n in notices:
            seen.add(n["id"])
        save_seen(seen)
        return

    # 오래된 것부터 전송 (시간순)
    for notice in reversed(new_notices):
        print(f"전송: {notice['title']}")
        send_discord(notice)
        seen.add(notice["id"])

    save_seen(seen)
    print(f"✅ {len(new_notices)}개 공지 전송 완료.")


if __name__ == "__main__":
    main()
