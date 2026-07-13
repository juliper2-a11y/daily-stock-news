# daily-stock-news

## IR 새소식 알림 (Xiaomi · Sony)

아래 사이트에 새 항목이 올라오면 자동으로 알림을 받는 GitHub Actions 워크플로우입니다.

| 사이트 | 대상 |
|---|---|
| [Xiaomi IR 이벤트 캘린더](https://ir.mi.com/news-events/event-calendar) | 실적 발표, 주주총회 등 이벤트 |
| [Sony IR 뉴스](https://www.sony.com/en/SonyInfo/IR/news/2026.html) | IR 뉴스 (연도 페이지는 자동으로 올해 것을 사용) |

### 동작 방식

1. **3시간마다** GitHub Actions가 각 사이트를 확인합니다 (`.github/workflows/event-calendar-alert.yml`).
2. `scripts/check_ir_updates.py`가 항목 목록을 가져와 `data/` 아래 상태 파일과 비교합니다.
3. **새 항목이 발견되면 이 저장소에 GitHub 이슈가 생성**되고, 저장소 소유자에게 자동 할당됩니다. → GitHub 알림(이메일/모바일 앱)으로 받아볼 수 있습니다.
4. 사이트별 최초 실행 시에는 현재 목록을 기준 상태로 저장하고 "모니터링 시작" 이슈를 생성합니다.
5. 한 사이트 확인이 실패해도 나머지 사이트는 정상 처리되며, 실패는 워크플로우 실패(빨간 X)로 표시되어 GitHub이 실패 알림을 보냅니다.

### 모니터링 사이트 추가

`scripts/check_ir_updates.py`의 `SOURCES` 목록에 `{id, name, urls, parse, state_file}` 항목을 추가하면 됩니다.

### 알림을 이메일로 받으려면

- GitHub → Settings → [Notifications](https://github.com/settings/notifications)에서 **Issues 알림의 Email 수신**이 켜져 있는지 확인하세요.
- 이슈가 생성될 때 저장소 소유자(@juliper2-a11y)에게 자동 할당되므로, 별도 설정 없이도 할당 알림이 갑니다.
- 휴대폰 푸시 알림을 원하면 GitHub 모바일 앱을 설치하고 알림을 켜세요.

### 수동 실행

Actions 탭 → **IR 새소식 알림 (Xiaomi·Sony)** → **Run workflow** 버튼으로 즉시 확인할 수 있습니다.

### 확인 주기 변경

`.github/workflows/event-calendar-alert.yml`의 `cron` 값을 수정하세요 (UTC 기준).

- `0 */3 * * *` — 3시간마다 (현재 설정)
- `0 * * * *` — 매시간
- `0 0 * * *` — 매일 1회 (한국시간 오전 9시)
