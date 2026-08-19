#!/usr/bin/env python3
"""애플 뉴스 수집 및 정적 사이트 생성.

Apple Newsroom RSS 피드에서 뉴스를 가져와 data/apple_news.json 에 누적 저장하고,
site/index.html 정적 페이지를 생성한다. 생성된 site/ 디렉터리는 Cloudflare Pages 로
배포되며, Cloudflare Access 로 승인된 사용자만 접속할 수 있다.

GitHub Actions 에서 실행되며, 결과는 GITHUB_OUTPUT 으로 전달한다:
  - state_changed: 새 뉴스가 있어 커밋이 필요한지
"""

import html
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "apple_news.json"
SITE_DIR = REPO_ROOT / "site"

FEEDS = [
    {"name": "Apple Newsroom", "url": "https://www.apple.com/newsroom/rss-feed.rss"},
]

MAX_ITEMS = 200  # 사이트에 표시할 최대 뉴스 수
KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
}


def http_get(url):
    last_err = None
    for attempt in range(3):
        if attempt:
            time.sleep(10 * attempt)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=(15, 90))
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            print(f"시도 {attempt + 1} 실패 ({url}): {e}", file=sys.stderr)
    raise last_err


def parse_feed(source_name, xml_text):
    """RSS 2.0 피드에서 항목 목록을 추출한다."""
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(pub_date).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        description = (node.findtext("description") or "").strip()
        category = (node.findtext("category") or "").strip()
        items.append({
            "source": source_name,
            "title": title,
            "link": link,
            "category": category,
            "description": description,
            "published": published.isoformat(),
        })
    return items


def load_state():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"items": []}


def save_state(state):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def merge_items(existing, fetched):
    """링크 기준으로 중복을 제거하며 새 항목을 병합한다. 새 항목 수를 반환한다."""
    known = {item["link"] for item in existing}
    new_items = [item for item in fetched if item["link"] not in known]
    merged = existing + new_items
    merged.sort(key=lambda item: item["published"], reverse=True)
    del merged[MAX_ITEMS:]
    return merged, len(new_items)


def format_kst(iso_str):
    dt = datetime.fromisoformat(iso_str).astimezone(KST)
    return dt.strftime("%Y년 %m월 %d일")


def render_site(items):
    updated = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    cards = []
    last_date = None
    for item in items:
        date_label = format_kst(item["published"])
        if date_label != last_date:
            cards.append(f'<h2 class="date">{html.escape(date_label)}</h2>')
            last_date = date_label
        category = (
            f'<span class="category">{html.escape(item["category"])}</span>'
            if item["category"] else ""
        )
        description = (
            f'<p class="desc">{html.escape(item["description"])}</p>'
            if item["description"] else ""
        )
        cards.append(f"""\
<a class="card" href="{html.escape(item["link"])}" target="_blank" rel="noopener">
  {category}
  <h3>{html.escape(item["title"])}</h3>
  {description}
</a>""")
    body = "\n".join(cards) if cards else '<p class="empty">아직 수집된 뉴스가 없습니다.</p>'
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Apple 뉴스</title>
<style>
  :root {{
    --header-bg: #000;
    --header-fg: #f5f5f7;
    --bg: #f5f5f7;
    --card-bg: #fff;
    --fg: #1d1d1f;
    --muted: #6e6e73;
    --accent: #0066cc;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo",
      "Noto Sans KR", sans-serif;
    background: var(--bg);
    color: var(--fg);
    -webkit-font-smoothing: antialiased;
  }}
  header {{
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--header-bg);
    color: var(--header-fg);
    padding: 0 24px;
  }}
  .header-inner {{
    max-width: 720px;
    margin: 0 auto;
    height: 52px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  header h1 {{
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }}
  header h1 .logo {{ margin-right: 6px; }}
  header .updated {{
    font-size: 12px;
    color: #a1a1a6;
  }}
  main {{
    max-width: 720px;
    margin: 0 auto;
    padding: 28px 24px 64px;
  }}
  .date {{
    font-size: 14px;
    font-weight: 600;
    color: var(--muted);
    margin: 32px 0 12px;
  }}
  .date:first-child {{ margin-top: 0; }}
  .card {{
    display: block;
    background: var(--card-bg);
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 12px;
    text-decoration: none;
    color: inherit;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
  }}
  .card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
  }}
  .category {{
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 6px;
  }}
  .card h3 {{
    font-size: 17px;
    font-weight: 600;
    line-height: 1.4;
  }}
  .desc {{
    margin-top: 6px;
    font-size: 14px;
    line-height: 1.5;
    color: var(--muted);
  }}
  .empty {{ color: var(--muted); }}
  footer {{
    max-width: 720px;
    margin: 0 auto;
    padding: 0 24px 40px;
    font-size: 12px;
    color: var(--muted);
  }}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <h1><span class="logo">&#127822;</span>Apple 뉴스</h1>
    <span class="updated">업데이트: {updated}</span>
  </div>
</header>
<main>
{body}
</main>
<footer>Apple Newsroom RSS 기반 · Cloudflare Access로 승인된 사용자만 접속할 수 있습니다.</footer>
</body>
</html>
"""


def set_output(name, value):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main():
    state = load_state()
    fetched = []
    errors = []
    for feed in FEEDS:
        try:
            resp = http_get(feed["url"])
            fetched.extend(parse_feed(feed["name"], resp.text))
        except Exception as e:  # noqa: BLE001 - 개별 피드 실패는 기록만 하고 계속
            errors.append(f"{feed['name']}: {e}")
            print(f"피드 수집 실패 ({feed['name']}): {e}", file=sys.stderr)

    merged, new_count = merge_items(state["items"], fetched)
    state["items"] = merged
    save_state(state)

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "index.html").write_text(render_site(merged), encoding="utf-8")

    print(f"새 뉴스 {new_count}건, 전체 {len(merged)}건")
    set_output("state_changed", "true" if new_count else "false")

    if errors and not fetched:
        sys.exit(1)


if __name__ == "__main__":
    main()
