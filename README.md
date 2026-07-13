# daily-stock-news

## Xiaomi IR 이벤트 캘린더 알림

[Xiaomi IR 이벤트 캘린더](https://ir.mi.com/news-events/event-calendar)에 새 이벤트가 올라오면 자동으로 알림을 받는 GitHub Actions 워크플로우입니다.

### 동작 방식

1. **3시간마다** GitHub Actions가 이벤트 캘린더 페이지를 확인합니다 (`.github/workflows/event-calendar-alert.yml`).
2. `scripts/check_event_calendar.py`가 이벤트 목록을 가져와 `data/event_calendar_state.json`에 저장된 이전 상태와 비교합니다.
3. **새 이벤트가 발견되면 이 저장소에 GitHub 이슈가 생성**되고, 저장소 소유자에게 자동 할당됩니다. → GitHub 알림(이메일/모바일 앱)으로 받아볼 수 있습니다.
4. 최초 실행 시에는 현재 이벤트 목록을 기준 상태로 저장하고 "모니터링 시작" 이슈를 하나 생성합니다.

### 알림을 이메일로 받으려면

- GitHub → Settings → [Notifications](https://github.com/settings/notifications)에서 **Issues 알림의 Email 수신**이 켜져 있는지 확인하세요.
- 이슈가 생성될 때 저장소 소유자(@juliper2-a11y)에게 자동 할당되므로, 별도 설정 없이도 할당 알림이 갑니다.
- 휴대폰 푸시 알림을 원하면 GitHub 모바일 앱을 설치하고 알림을 켜세요.

### 수동 실행

Actions 탭 → **Xiaomi IR 이벤트 캘린더 알림** → **Run workflow** 버튼으로 즉시 확인할 수 있습니다.

### 확인 주기 변경

`.github/workflows/event-calendar-alert.yml`의 `cron` 값을 수정하세요 (UTC 기준).

- `0 */3 * * *` — 3시간마다 (현재 설정)
- `0 * * * *` — 매시간
- `0 0 * * *` — 매일 1회 (한국시간 오전 9시)
