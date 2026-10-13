#!/usr/bin/env python3
"""관심종목 가격 조건 알림 — watchlist.json에 정한 조건에 시세가 도달하면 macOS 알림을 보냅니다.

매수·매도 추천이 아닙니다. 직접 정한 기준(지정가, 52주 고점 대비 하락률, 200일 이동평균,
RSI, 하루 급변)에 시세가 도달했는지를 기계적으로 확인해 알려 줄 뿐입니다.
같은 조건은 새로 충족될 때 한 번만 알립니다(하루 급변은 하루 한 번).

사용법:
  python3 stockwatch.py             # 평일 한국장 시간(09:00~16:00)에만 확인 + 알림
  python3 stockwatch.py --force     # 시간과 관계없이 확인 + 알림
  python3 stockwatch.py --force --no-alert   # 결과만 출력
"""
import html
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "watchlist.json")
STATE = os.path.join(BASE, "watch_state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
DISCLAIMER = "투자 권유 아님 · 직접 판단하세요"


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg), flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def fetch(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d" % urllib.request.quote(symbol)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.loads(r.read())["chart"]["result"][0]
    meta = res["meta"]
    pairs = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    closes = [c for _, c in pairs]
    price = meta.get("regularMarketPrice") or closes[-1]
    # 오늘 봉이 이미 있으면 현재가로 갱신, 없으면 현재가를 새 봉으로 추가
    off = meta.get("gmtoffset", 0)
    if pairs and (pairs[-1][0] + off) // 86400 == (meta.get("regularMarketTime", 0) + off) // 86400:
        closes[-1] = price
    else:
        closes.append(price)
    return meta.get("currency", "USD"), closes, price


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in diffs[:n]) / n
    loss = sum(max(-d, 0) for d in diffs[:n]) / n
    for d in diffs[n:]:
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def fmt(p, cur):
    if cur == "KRW":
        return "{:,.0f}원".format(p)
    return "$" + ("{:,.2f}".format(p) if p < 10000 else "{:,.0f}".format(p))


def analyze(stock, defaults):
    cfg = dict(defaults)
    cfg.update({k: v for k, v in stock.items() if v is not None})
    try:
        cur, closes, price = fetch(stock["symbol"])
    except Exception as e:
        log("시세 실패 %s: %s" % (stock["symbol"], e))
        return None
    change = (price / closes[-2] - 1) * 100 if len(closes) > 1 else 0.0
    high = max(closes[-252:])
    drawdown = (price / high - 1) * 100
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    ma_gap = (price / ma200 - 1) * 100 if ma200 else None
    r = rsi(closes)

    conds = []
    if cfg.get("drawdown_from_high_pct") and drawdown <= -cfg["drawdown_from_high_pct"]:
        conds.append("52주 고점 대비 %.1f%%" % drawdown)
    if cfg.get("below_ma200") and ma_gap is not None and ma_gap < 0:
        conds.append("200일선 %.1f%% 아래" % -ma_gap)
    if cfg.get("rsi_below") and r is not None and r < cfg["rsi_below"]:
        conds.append("RSI %.0f" % r)

    today = datetime.now().strftime("%Y-%m-%d")
    hits = []  # (상태 키, 배지, 알림 문구)
    if len(conds) >= cfg.get("min_conditions", 2):
        hits.append(("conditions", "조건 %d개 충족" % len(conds), "가격 조건 충족: " + " · ".join(conds)))
    if cfg.get("alert_below") and price <= cfg["alert_below"]:
        hits.append(("below", "지정가 이하", "지정가 %s 이하 도달" % fmt(cfg["alert_below"], cur)))
    if cfg.get("alert_above") and price >= cfg["alert_above"]:
        hits.append(("above", "지정가 이상", "지정가 %s 이상 도달" % fmt(cfg["alert_above"], cur)))
    if cfg.get("day_move_pct") and abs(change) >= cfg["day_move_pct"]:
        hits.append(("move:" + today, "급변 %+.1f%%" % change, "하루 %+.1f%% 급변" % change))

    return dict(name=stock["name"], symbol=stock["symbol"], aliases=stock.get("aliases") or [stock["name"]],
                currency=cur, price=price, change=change, drawdown=drawdown, ma_gap=ma_gap, rsi=r,
                conds=conds, hits=hits)


def market_open(now):
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (16, 0)


def notify(title, subtitle, message):
    subprocess.run(["osascript", "-e", "on run argv",
                    "-e", "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv) sound name \"Ping\"",
                    "-e", "end run", title, subtitle, message], check=False)


def check(alert=True, force=False):
    now = datetime.now()
    if not force and not market_open(now):
        log("장 시간이 아니라 건너뜁니다.")
        return None
    config = load_json(CONFIG, None)
    if not config:
        log("watchlist.json이 없습니다.")
        return []
    stocks, defaults = config.get("stocks", []), config.get("defaults", {})
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(lambda s: analyze(s, defaults), stocks) if r]

    state = load_json(STATE, {})
    today = now.strftime("%Y-%m-%d")
    for r in rows:
        prev = state.get(r["symbol"], {})
        active, alerted = set(prev.get("active", [])), prev.get("alerted", {})
        fresh = [h for h in r["hits"] if h[0] not in active and alerted.get(h[0]) != today]
        log("%s %s (%+.2f%%) 고점대비 %.1f%% RSI %s %s" % (
            r["name"], fmt(r["price"], r["currency"]), r["change"], r["drawdown"],
            "%.0f" % r["rsi"] if r["rsi"] is not None else "-",
            "| 새 알림: " + ", ".join(h[2] for h in fresh) if fresh else ""))
        if not alert:
            continue
        for key, _, text in fresh:
            notify("관심종목 알림 · %s" % r["name"],
                   "%s (%+.2f%%)" % (fmt(r["price"], r["currency"]), r["change"]),
                   "%s — %s" % (text, DISCLAIMER))
            alerted[key] = today
        state[r["symbol"]] = {"active": sorted(h[0] for h in r["hits"]),
                              "alerted": {k: v for k, v in alerted.items() if v == today or not k.startswith("move:")}}
    if alert:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
    return rows


def criteria_text():
    d = (load_json(CONFIG, {}) or {}).get("defaults", {})
    parts = []
    if d.get("drawdown_from_high_pct"):
        parts.append("52주 고점 대비 −%s%% 이하" % d["drawdown_from_high_pct"])
    if d.get("below_ma200"):
        parts.append("200일선 아래")
    if d.get("rsi_below"):
        parts.append("RSI %s 미만" % d["rsi_below"])
    s = "%s 중 %s개 이상" % (" · ".join(parts), d.get("min_conditions", 2)) if parts else ""
    if d.get("day_move_pct"):
        s += ", 하루 ±%s%% 급변" % d["day_move_pct"]
    return s + ", 지정가(설정 시)"


def section_html(rows, stories):
    """저녁 신문에 넣을 '관심종목 체크' 표."""
    if not rows:
        return ""
    esc = lambda s: html.escape(s or "", quote=True)
    trs = []
    for r in rows:
        names = [a.lower() for a in r["aliases"]]
        related = [s for s in stories if any(a in s["title"].lower() for a in names)]
        news = "기사 %d건" % len(related)
        if related:
            news += ' · <a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(related[0]["link"]), esc(related[0]["title"]))
        badges = "".join('<span class="badge" title="%s">%s</span>' % (esc(text), esc(b)) for _, b, text in r["hits"])
        cls = "up" if r["change"] > 0.005 else "down" if r["change"] < -0.005 else "flat"
        trs.append(
            '<tr class="%s"><td>%s<small>%s</small></td><td class="num">%s</td><td class="num %s">%+.2f%%</td>'
            '<td class="num">%.1f%%</td><td class="num">%s</td><td class="num">%s</td><td>%s</td><td class="news">%s</td></tr>' % (
                "hit" if r["hits"] else "", esc(r["name"]), esc(r["symbol"]), fmt(r["price"], r["currency"]), cls, r["change"],
                r["drawdown"], "%+.1f%%" % r["ma_gap"] if r["ma_gap"] is not None else "—",
                "%.0f" % r["rsi"] if r["rsi"] is not None else "—", badges or '<span class="flat">—</span>', news))
    return ('<section class="watch"><div class="label">관심종목 체크 <span class="en-l">Watchlist</span></div><div class="tbl"><table>'
            '<thead><tr><th>종목 Stock</th><th class="num">현재가 Price</th><th class="num">일간 1D</th><th class="num">52주 고점 대비 vs 52W High</th>'
            '<th class="num">200일선 대비 vs MA200</th><th class="num">RSI(14)</th><th>알림 조건 Alerts</th><th>오늘 뉴스 News</th></tr></thead>'
            '<tbody>%s</tbody></table></div><p class="note">알림 기준(watchlist.json에서 변경): %s. '
            '설정한 기준에 따른 기계적 표시이며 매수·매도 추천이 아닙니다. 한국 주식은 약 20분 지연 시세입니다. '
            'Rule-based flags from your own settings — not investment advice.</p></section>') % (
        "".join(trs), esc(criteria_text()))


if __name__ == "__main__":
    args = set(sys.argv[1:])
    check(alert="--no-alert" not in args, force="--force" in args)
