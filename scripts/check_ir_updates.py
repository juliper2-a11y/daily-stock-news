#!/usr/bin/env python3
"""IR 사이트 새소식 모니터링.

모니터링 대상 (SOURCES):
  - Xiaomi IR 이벤트 캘린더: https://ir.mi.com/news-events/event-calendar
  - Sony IR 뉴스:            https://www.sony.com/en/SonyInfo/IR/news/<연도>.html
  - Dell 보도자료(IR):       https://investors.delltechnologies.com/news-events/press-release
  - Corning IR 뉴스:         https://investor.corning.com/news-and-events/news/default.aspx
  - Lenovo IR 캘린더:        https://investor.lenovo.com/en/ir/calendar.php

각 사이트에서 항목 목록을 가져와 data/ 아래 상태 파일과 비교하고,
새 항목이 있으면 알림용 마크다운(notification.md)을 생성한다.

GitHub Actions 에서 실행되며, 결과는 GITHUB_OUTPUT 으로 전달한다:
  - state_changed: 상태 파일이 갱신되어 커밋이 필요한지
  - notify:        알림 이슈를 만들어야 하는지 (새 항목 또는 최초 실행)
  - title:         이슈 제목
일부 사이트만 실패한 경우에도 성공한 사이트는 정상 처리하고,
마지막에 종료 코드 1 로 실패를 알린다.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
NOTIFICATION_FILE = REPO_ROOT / "notification.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MONTH_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]* \d{1,2},? \d{4}"
)
DAY_FIRST_DATE_RE = re.compile(
    r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]* \d{4}"
)
NUMERIC_DATE_RE = re.compile(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b")


def http_get(url, params=None):
    """WAF 우회를 위해 크롬 TLS 지문(curl_cffi)을 우선 사용하고, 재시도한다."""
    last_err = None
    for attempt in range(3):
        if attempt:
            time.sleep(10 * attempt)
        if cffi_requests is not None:
            try:
                resp = cffi_requests.get(
                    url, params=params, headers=HEADERS,
                    impersonate="chrome", timeout=90,
                )
                resp.raise_for_status()
                return resp
            except Exception as e:  # noqa: BLE001 - curl_cffi 예외 계층이 별도
                last_err = e
                print(f"curl_cffi 시도 {attempt + 1} 실패 ({url}): {e}", file=sys.stderr)
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=(15, 90))
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            print(f"requests 시도 {attempt + 1} 실패 ({url}): {e}", file=sys.stderr)
    raise RuntimeError(f"{url} 요청 실패: {last_err}")


def find_date(text):
    m = (MONTH_DATE_RE.search(text)
         or DAY_FIRST_DATE_RE.search(text)
         or NUMERIC_DATE_RE.search(text))
    return m.group(0) if m else ""


# 링크 텍스트가 이런 문구면 제목이 아니므로 컨테이너에서 실제 제목을 찾는다
GENERIC_LINK_TEXTS = {
    "read more", "learn more", "more", "details", "view details", "view more",
    "click here", "here", "webcast", "listen to webcast", "click here for webcast",
    "download", "pdf", "presentation", "press release",
}


def derive_title(container, date_text):
    """컨테이너 텍스트에서 날짜/상투어가 아닌 첫 줄을 제목으로 사용."""
    for ln in container.get_text("\n").split("\n"):
        ln = ln.strip()
        if not ln or len(ln) < 6:
            continue
        if ln.lower() in GENERIC_LINK_TEXTS:
            continue
        without_date = ln.replace(date_text, "").strip() if date_text else ln
        if not without_date or len(without_date) < 6:
            continue
        return without_date if without_date != ln else ln
    return ""


# ---------------------------------------------------------------- 범용 파서

def parse_table_rows(page_html, page_url):
    """날짜 셀로 시작하는 테이블 행 파싱: 행 = [날짜 | 내용 | (자료)].

    ir.mi.com (Nasdaq IR) 이벤트 테이블 등에서 사용. 링크가 없는 행도 잡는다.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    soup = BeautifulSoup(page_html, "html.parser")
    events = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            date_text = tds[0].get_text(" ", strip=True)
            if not find_date(date_text):
                continue
            lines = [ln.strip() for ln in tds[1].get_text("\n").split("\n") if ln.strip()]
            if not lines:
                continue
            title = lines[0]
            link = ""
            for link_el in tds[1].find_all("a", href=True) + tr.find_all("a", href=True):
                href = link_el["href"].strip()
                if href and not href.startswith(("#", "javascript:", "mailto:")):
                    link = urljoin(page_url, href)
                    break
            events.append({
                "key": f"{title}|{date_text}",
                "title": title,
                "date": date_text,
                "url": link or page_url,
            })
    return events


def parse_dated_links(page_html, page_url):
    """날짜가 붙은 링크 목록을 일반적으로 파싱.

    링크에서 가까운 컨테이너(li/tr/dd/article → 조상 순서)로 올라가며
    날짜 텍스트를 찾는다. Sony IR 뉴스, Q4 플랫폼(모듈 div) 등에 대응.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()

    events = []
    seen = set()
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        href = a["href"].strip()
        if not title or len(title) < 2 or href.startswith(("#", "javascript:", "mailto:")):
            continue

        date_text = ""
        dated_container = None
        container = a.find_parent(["li", "tr", "dd", "article"])
        if container is not None:
            date_text = find_date(container.get_text(" ", strip=True))
            if date_text:
                dated_container = container
            else:
                # <dt>날짜</dt><dd>링크</dd> 형태 지원
                prev = container.find_previous_sibling()
                if prev is not None:
                    date_text = find_date(prev.get_text(" ", strip=True))
                    if date_text:
                        dated_container = container
        if not date_text:
            # 조상으로 올라가며 날짜가 있는 작은 컨테이너 탐색 (Q4 모듈 div 등)
            node = a
            for _ in range(5):
                node = node.parent
                if node is None or node.name in ("body", "html", "[document]"):
                    break
                if len(node.find_all("a", href=True)) > 6:
                    break  # 페이지 전체 같은 큰 컨테이너는 제외
                date_text = find_date(node.get_text(" ", strip=True))
                if date_text:
                    dated_container = node
                    break
        if not date_text:
            continue

        # "Read More" 같은 상투적 링크 텍스트면 컨테이너에서 실제 제목을 찾는다
        if (title.lower() in GENERIC_LINK_TEXTS or len(title) < 6) and dated_container is not None:
            title = derive_title(dated_container, date_text) or title
        if len(title) < 6:
            continue

        url = urljoin(page_url, href)
        key = f"{title}|{date_text}"
        if key in seen:
            continue
        seen.add(key)
        events.append({"key": key, "title": title, "date": date_text, "url": url})
    return events


def parse_tables_and_links(page_html, page_url):
    """테이블 행 + 날짜 링크 파싱을 합쳐 키 기준으로 중복 제거.

    테이블 파서가 처리한(날짜 행이 있는) 테이블은 링크 파서 대상에서
    제거해, 같은 행의 부속 링크(Webcast 등)가 별도 항목으로 잡히지 않게 한다.
    """
    from bs4 import BeautifulSoup

    events = parse_table_rows(page_html, page_url)
    remaining_html = page_html
    if events:
        soup = BeautifulSoup(page_html, "html.parser")
        for table in soup.find_all("table"):
            has_dated_row = any(
                tds and find_date(tds[0].get_text(" ", strip=True))
                for tr in table.find_all("tr")
                if (tds := tr.find_all("td"))
            )
            if has_dated_row:
                table.decompose()
        remaining_html = str(soup)

    seen = {ev["key"] for ev in events}
    for ev in parse_dated_links(remaining_html, page_url):
        if ev["key"] not in seen:
            seen.add(ev["key"])
            events.append(ev)
    return events


# ---------------------------------------------------------------- 소스별 설정

def sony_urls():
    """올해 뉴스 페이지 우선, 연초에 아직 없으면 작년 페이지로 폴백."""
    year = datetime.now(timezone.utc).year
    return [
        f"https://www.sony.com/en/SonyInfo/IR/news/{year}.html",
        f"https://www.sony.com/en/SonyInfo/IR/news/{year - 1}.html",
    ]


# ---------------------------------------------------------------- 소스 정의

SOURCES = [
    {
        "id": "xiaomi_events",
        "name": "Xiaomi IR 이벤트 캘린더",
        "urls": lambda: ["https://ir.mi.com/news-events/event-calendar"],
        "parse": parse_table_rows,
        "state_file": DATA_DIR / "event_calendar_state.json",  # 기존 상태 파일 유지
    },
    {
        "id": "sony_ir_news",
        "name": "Sony IR 뉴스",
        "urls": sony_urls,
        "parse": parse_dated_links,
        "state_file": DATA_DIR / "sony_ir_news_state.json",
    },
    {
        "id": "dell_press",
        "name": "Dell 보도자료(IR)",
        "urls": lambda: ["https://investors.delltechnologies.com/news-events/press-release"],
        "parse": parse_tables_and_links,
        "state_file": DATA_DIR / "dell_press_state.json",
    },
    {
        "id": "corning_news",
        "name": "Corning IR 뉴스",
        "urls": lambda: ["https://investor.corning.com/news-and-events/news/default.aspx"],
        "parse": parse_tables_and_links,
        "state_file": DATA_DIR / "corning_news_state.json",
    },
    {
        "id": "lenovo_calendar",
        "name": "Lenovo IR 캘린더",
        "urls": lambda: ["https://investor.lenovo.com/en/ir/calendar.php"],
        "parse": parse_tables_and_links,
        "state_file": DATA_DIR / "lenovo_calendar_state.json",
        # 사이드바의 오래된 고정 링크(2008년 등)가 이벤트로 잡히지 않게 최근 항목만
        "min_year": datetime.now(timezone.utc).year - 2,
    },
]


# ---------------------------------------------------------------- 공통 로직

def print_page_diagnostics(page_html):
    """파싱 실패 시 사이트 구조 파악을 위한 진단 정보를 로그에 남긴다."""
    print(f"--- 진단: HTML 길이 {len(page_html)}", file=sys.stderr)
    m = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.S | re.I)
    print(f"--- 진단: <title> = {m.group(1).strip() if m else '(없음)'}", file=sys.stderr)
    scripts = re.findall(r"<script[^>]+src=['\"]([^'\"]+)", page_html)
    print(f"--- 진단: 외부 스크립트 {len(scripts)}개", file=sys.stderr)
    for s in scripts[:20]:
        print(f"    {s}", file=sys.stderr)
    body = re.sub(r"<script.*?</script>", " ", page_html, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    print(f"--- 진단: 본문 텍스트 앞 1500자:\n{text[:1500]}", file=sys.stderr)


def load_state(path):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"initialized": False, "events": {}}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def set_output(name, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"[output] {name}={value}")


def format_event_line(ev):
    line = f"- **{ev['title']}**"
    if ev.get("date"):
        line += f"\n  - 일시: {ev['date']}"
    line += f"\n  - 링크: {ev['url']}"
    return line


def process_source(src, now):
    urls = src["urls"]()
    page_html, page_url, last_err = None, None, None
    for url in urls:
        try:
            page_html = http_get(url).text
            page_url = url
            break
        except RuntimeError as e:
            last_err = e
    if page_html is None:
        raise RuntimeError(f"{src['name']}: 모든 URL 요청 실패: {last_err}")

    events = src["parse"](page_html, page_url)
    min_year = src.get("min_year")
    if min_year:
        def recent(ev):
            m = re.search(r"\b(19|20)\d{2}\b", ev["date"])
            return m is None or int(m.group(0)) >= min_year
        events = [ev for ev in events if recent(ev)]
    if not events:
        print(f"ERROR: {src['name']} 페이지에서 항목을 찾지 못했습니다.", file=sys.stderr)
        print_page_diagnostics(page_html)
        raise RuntimeError(f"{src['name']}: 파싱 결과 0건")

    print(f"{src['name']}: {len(events)}건 발견 ({page_url})")

    state = load_state(src["state_file"])
    known = state.get("events", {})
    first_run = not state.get("initialized", False)
    new_events = [ev for ev in events if ev["key"] not in known]

    state_changed = first_run or bool(new_events)
    if state_changed:
        for ev in events:
            known[ev["key"]] = {"title": ev["title"], "date": ev["date"], "url": ev["url"]}
        state["events"] = known
        state["initialized"] = True
        state["last_updated"] = now
        save_state(src["state_file"], state)

    return {
        "src": src,
        "page_url": page_url,
        "events": events,
        "new_events": [] if first_run else new_events,
        "first_run": first_run,
        "state_changed": state_changed,
    }


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results, failures = [], []
    for src in SOURCES:
        try:
            results.append(process_source(src, now))
        except Exception as e:  # noqa: BLE001 - 한 소스 실패가 다른 소스를 막지 않게
            print(f"ERROR: {src['name']} 처리 실패: {e}", file=sys.stderr)
            failures.append(src["name"])

    set_output("state_changed", "true" if any(r["state_changed"] for r in results) else "false")

    sections = []
    new_total = []
    first_run_names = []
    for r in results:
        if r["first_run"]:
            first_run_names.append(r["src"]["name"])
            listing = "\n".join(format_event_line(ev) for ev in r["events"][:40])
            if len(r["events"]) > 40:
                listing += f"\n- … 외 {len(r['events']) - 40}건"
            sections.append(
                f"### [모니터링 시작] {r['src']['name']}\n\n"
                f"대상: {r['page_url']}\n\n"
                f"현재 {len(r['events'])}건을 기준 상태로 저장했습니다. "
                f"앞으로 새 항목이 올라오면 이슈로 알림을 보냅니다.\n\n{listing}"
            )
        elif r["new_events"]:
            new_total.extend((r["src"]["name"], ev) for ev in r["new_events"])
            listing = "\n".join(format_event_line(ev) for ev in r["new_events"])
            sections.append(
                f"### {r['src']['name']} — 새 항목 {len(r['new_events'])}건\n\n"
                f"{listing}\n\n출처: {r['page_url']}"
            )

    if not sections:
        print("알림 없음.")
        set_output("notify", "false")
    else:
        body = "\n\n---\n\n".join(sections) + f"\n\n---\n확인 시각: {now}"
        NOTIFICATION_FILE.write_text(body, encoding="utf-8")
        set_output("notify", "true")
        if new_total:
            titles = ", ".join(f"{name}: {ev['title']}" for name, ev in new_total[:2])
            if len(new_total) > 2:
                titles += f" 외 {len(new_total) - 2}건"
            set_output("title", f"[새 소식] {titles}")
        else:
            set_output("title", f"[모니터링 시작] {', '.join(first_run_names)}")

    if failures:
        print(f"ERROR: 처리 실패한 소스: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
