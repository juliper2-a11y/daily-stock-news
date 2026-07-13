#!/usr/bin/env python3
"""Xiaomi IR 이벤트 캘린더(https://ir.mi.com/news-events/event-calendar) 모니터링.

페이지에서 이벤트 목록을 가져와 data/event_calendar_state.json 에 저장된
이전 상태와 비교하고, 새 이벤트가 있으면 알림용 마크다운을 생성한다.

GitHub Actions 에서 실행되며, 결과는 GITHUB_OUTPUT 으로 전달한다:
  - state_changed: 상태 파일이 갱신되어 커밋이 필요한지
  - new_events:    새 이벤트가 발견되어 이슈를 만들어야 하는지
  - first_run:     최초 실행(기준 상태 저장)인지
알림 본문은 notification.md 로 저장된다.
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

BASE_URL = "https://ir.mi.com"
PAGE_URL = f"{BASE_URL}/news-events/event-calendar"
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "data" / "event_calendar_state.json"
NOTIFICATION_FILE = REPO_ROOT / "notification.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


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
                print(f"curl_cffi 시도 {attempt + 1} 실패: {e}", file=sys.stderr)
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=(15, 90))
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            print(f"requests 시도 {attempt + 1} 실패: {e}", file=sys.stderr)
    raise RuntimeError(f"{url} 요청 실패: {last_err}")


def fetch_page_html():
    return http_get(PAGE_URL).text


def parse_q4_date(raw):
    """Q4 API 날짜 형식을 ISO 문자열로 변환. 실패하면 원본 반환."""
    if not raw:
        return ""
    m = re.match(r"/Date\((\d+)", raw)
    if m:
        ts = int(m.group(1)) / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return raw.strip()


def fetch_events_via_api(page_html):
    """Q4 플랫폼의 Event.svc JSON API 로 이벤트 조회 (apiKey 는 페이지에서 추출)."""
    m = re.search(r"apiKey['\"]?\s*[:=]\s*['\"]([A-Fa-f0-9]{16,64})['\"]", page_html)
    if not m:
        return None
    api_key = m.group(1)
    api_url = f"{BASE_URL}/feed/Event.svc/GetEventList"
    params = {
        "apiKey": api_key,
        "includeTags": "true",
        "pageIndex": 0,
        "pageSize": 100,
        "eventDateFilter": "All",
        "sortOperator": 1,
        "year": -1,
        "includePresentations": "true",
        "includePressReleases": "true",
    }
    try:
        resp = http_get(api_url, params=params)
        data = resp.json()
    except (RuntimeError, ValueError):
        return None

    items = data.get("GetEventListResult") or []
    events = []
    for item in items:
        title = (item.get("Title") or "").strip()
        if not title:
            continue
        link = item.get("LinkToDetailPage") or ""
        if link and link.startswith("/"):
            link = BASE_URL + link
        key = str(item.get("EventId") or "") or f"{title}|{item.get('StartDate', '')}"
        events.append({
            "key": key,
            "title": title,
            "start": parse_q4_date(item.get("StartDate")),
            "end": parse_q4_date(item.get("EndDate")),
            "url": link or PAGE_URL,
        })
    return events or None


def fetch_events_via_html(page_html):
    """서버 렌더링된 HTML 에서 이벤트 파싱 (Q4 모듈 마크업 + 일반 폴백)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page_html, "html.parser")
    events = []

    # Q4 웹사이트 모듈 마크업
    for item in soup.select(".module_item"):
        headline = item.select_one(".module_headline, .module_headline-link, h3, h4, a")
        if not headline:
            continue
        title = headline.get_text(" ", strip=True)
        if not title:
            continue
        date_el = item.select_one(
            ".module_date-time, .module_date-text, .module-event_date, time"
        )
        date_text = date_el.get_text(" ", strip=True) if date_el else ""
        link_el = headline if headline.name == "a" else item.select_one("a[href]")
        link = link_el.get("href", "") if link_el else ""
        if link.startswith("/"):
            link = BASE_URL + link
        events.append({
            "key": f"{title}|{date_text}",
            "title": title,
            "start": date_text,
            "end": "",
            "url": link or PAGE_URL,
        })

    if events:
        return events

    # 일반 폴백: schema.org Event JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "Event":
                title = (obj.get("name") or "").strip()
                if not title:
                    continue
                events.append({
                    "key": f"{title}|{obj.get('startDate', '')}",
                    "title": title,
                    "start": obj.get("startDate", ""),
                    "end": obj.get("endDate", ""),
                    "url": obj.get("url") or PAGE_URL,
                })
    return events


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"initialized": False, "events": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def set_output(name, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"[output] {name}={value}")


def format_event_line(ev):
    period = ev["start"]
    if ev.get("end") and ev["end"] != ev["start"]:
        period = f"{ev['start']} ~ {ev['end']}"
    line = f"- **{ev['title']}**"
    if period:
        line += f"\n  - 일시: {period}"
    line += f"\n  - 링크: {ev['url']}"
    return line


def main():
    page_html = fetch_page_html()

    events = fetch_events_via_api(page_html)
    source = "api"
    if not events:
        events = fetch_events_via_html(page_html)
        source = "html"

    if not events:
        print("ERROR: 페이지에서 이벤트를 하나도 찾지 못했습니다. "
              "사이트 구조가 바뀌었거나 접근이 차단되었을 수 있습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"{len(events)}개 이벤트 발견 (source={source})")

    state = load_state()
    known = state.get("events", {})
    first_run = not state.get("initialized", False)

    new_events = [ev for ev in events if ev["key"] not in known]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state_changed = first_run or bool(new_events)
    if state_changed:
        for ev in events:
            known[ev["key"]] = {"title": ev["title"], "start": ev["start"], "url": ev["url"]}
        state["events"] = known
        state["initialized"] = True
        state["last_updated"] = now
        state["source"] = source
        save_state(state)

    set_output("state_changed", "true" if state_changed else "false")
    set_output("first_run", "true" if first_run else "false")

    if first_run:
        body = (
            f"Xiaomi IR 이벤트 캘린더 모니터링이 시작되었습니다.\n\n"
            f"모니터링 대상: {PAGE_URL}\n\n"
            f"현재 등록된 이벤트 {len(events)}건을 기준 상태로 저장했습니다. "
            f"앞으로 새 이벤트가 올라오면 이슈로 알림을 보냅니다.\n\n"
            f"### 현재 이벤트 목록\n\n"
            + "\n".join(format_event_line(ev) for ev in events)
        )
        NOTIFICATION_FILE.write_text(body, encoding="utf-8")
        set_output("new_events", "false")
        set_output("title", "[모니터링 시작] Xiaomi IR 이벤트 캘린더 알림 설정 완료")
        return

    if new_events:
        print(f"새 이벤트 {len(new_events)}건 발견!")
        body = (
            f"Xiaomi IR 이벤트 캘린더에 새 이벤트 {len(new_events)}건이 올라왔습니다.\n\n"
            + "\n".join(format_event_line(ev) for ev in new_events)
            + f"\n\n---\n확인 시각: {now}\n출처: {PAGE_URL}"
        )
        NOTIFICATION_FILE.write_text(body, encoding="utf-8")
        set_output("new_events", "true")
        titles = ", ".join(ev["title"] for ev in new_events[:2])
        if len(new_events) > 2:
            titles += f" 외 {len(new_events) - 2}건"
        set_output("title", f"[새 이벤트] {titles}")
    else:
        print("새 이벤트 없음.")
        set_output("new_events", "false")


if __name__ == "__main__":
    main()
