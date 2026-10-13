# 마켓 나이트 공개 출시 체크리스트

공개판은 `python3 newsboard.py --public`으로 `public/` 폴더에 만들어집니다.
개인용 신문(`site/`), 관심종목(`watchlist.json`), 맥 알림은 그대로이고 공개되지 않습니다.
매일 밤 9시에 GitHub이 대신 실행하므로 맥이 꺼져 있어도 발행됩니다.

## 1. GitHub에 올리기 (무료, 20분)

1. https://github.com 가입
2. 오른쪽 위 **+ → New repository**: 이름 `market-night`, **Public** 선택, 다른 옵션은 그대로 두고 생성
3. 터미널에서 (`<아이디>`는 본인 GitHub 아이디):
   ```bash
   cd ~/NewsBoard
   git remote add origin https://github.com/<아이디>/market-night.git
   git push -u origin main
   ```
   비밀번호 대신 토큰을 물어보면 GitHub → Settings → Developer settings → Personal access tokens에서 만들어 붙여 넣으세요.
4. 저장소 **Settings → Pages → Source**를 **GitHub Actions**로 바꾸기

## 2. 텔레그램 채널 만들기 (무료, 10분)

1. 텔레그램 앱에서 **새 채널** 만들기 → 공개 채널, 주소 예: `t.me/marketnight_kr`
2. `@BotFather`에게 `/newbot` → 봇 이름 정하기 → **토큰**을 받음
3. 만든 봇을 채널 **관리자**로 추가 (메시지 게시 권한)

## 3. 설정 넣기

저장소 **Settings → Secrets and variables → Actions**

| 종류 | 이름 | 값 |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` | BotFather가 준 토큰 |
| Variable | `TELEGRAM_CHAT_ID` | `@marketnight_kr` (채널 주소) |
| Variable | `MN_TELEGRAM_URL` | `https://t.me/marketnight_kr` |
| Variable | `MN_SITE_URL` | `https://<아이디>.github.io/market-night` |
| Variable | `MN_CONTACT` | (선택) 공개해도 되는 문의 이메일 |
| Variable | `MN_ADFIT_UNIT` | (나중에) 카카오 애드핏 광고 단위 ID |

## 4. 첫 발행

**Actions → publish → Run workflow**. 2~3분 뒤 사이트와 텔레그램 채널을 확인하세요.
그다음부터는 매일 21:00(KST)에 자동으로 발행됩니다.

## 5. 사람 모으기와 수익화 (출시 후)

- **검색 등록**: Google Search Console과 네이버 서치어드바이저에 사이트 등록 후 `sitemap.xml` 제출
- **도메인** (연 1~2만 원): 예) `marketnight.kr` 구입 → Settings → Pages → Custom domain에 입력 → `MN_SITE_URL` 변경
- **홍보**: 주식·코인 커뮤니티, 텔레그램 채널 목록 사이트, 블로그·스레드에 매일 '꼭 알아야 할 뉴스 5' 공유
- **광고**: 방문자가 생기면 카카오 애드핏 신청 (구글 애드센스는 자체 글이 적은 모음 사이트를 잘 승인하지 않음).
  승인되면 `MN_ADFIT_UNIT`만 넣으면 광고 자리가 생깁니다
- **채널 스폰서**: 구독자 1천~수천 명이 되면 텔레그램 채널 협찬 글로 수익

## 주의

- 공개판은 기사 **제목과 링크만** 싣습니다. 기사 요약·본문을 붙이면 매체 저작권 문제가 생길 수 있습니다.
- 유료 구독이나 종목 추천을 붙이면 유사투자자문업 신고 대상이 될 수 있으니 먼저 확인하세요.
- 시세는 Yahoo Finance·CoinGecko 무료 데이터입니다. GitHub 서버에서 막히면 일부 시세가 빠질 수 있습니다.
