#!/usr/bin/env python3
"""마켓 나이트 · Market Night
매일 저녁 9시, 한국어·영어 금융·경제·증시·코인 뉴스를 한 장의 신문으로 정리합니다.

표준 라이브러리만 사용합니다. 국내외 매체 RSS와 시세 데이터를 받아 중복 기사를 묶고
순위를 매긴 뒤 site/index.html(+ 날짜별 지난 호)을 만들고 macOS 알림을 보냅니다.

사용법:
  python3 newsboard.py              # 생성 + 알림 + "신문 열기" 대화상자
  python3 newsboard.py --no-alert   # 생성만
  python3 newsboard.py --no-dialog  # 생성 + 알림 배너만
  python3 newsboard.py --open       # 생성 후 페이지 열기
  python3 newsboard.py --public     # 공개판: public/에 생성 (관심종목·기사 요약·알림 없음)
  python3 newsboard.py --post-telegram   # 공개판 생성 때 저장한 메시지를 텔레그램 채널에 보내기

공개판 설정(환경 변수, 모두 선택): MN_SITE_URL, MN_TELEGRAM_URL, MN_ADFIT_UNIT, MN_CONTACT,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

BASE = os.path.dirname(os.path.abspath(__file__))
# 공개판은 기사 제목·링크만 싣고(요약은 매체 저작물이라 뺍니다) 개인 관심종목 표도 넣지 않습니다
PUBLIC = "--public" in sys.argv
SITE = os.path.join(BASE, "public" if PUBLIC else "site")
ARCHIVE = os.path.join(SITE, "archive")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Safari/605.1.15"
WINDOW_HOURS = 30
LOCAL_TZ = datetime.now().astimezone().tzinfo
NAME = "마켓 나이트"
NAME_EN = "Market Night"
HANGUL = re.compile(r"[가-힣]")
SITE_URL = os.environ.get("MN_SITE_URL", "").rstrip("/")
TELEGRAM_URL = os.environ.get("MN_TELEGRAM_URL", "")
ADFIT_UNIT = os.environ.get("MN_ADFIT_UNIT", "")
CONTACT = os.environ.get("MN_CONTACT", "")
TG_MESSAGE = os.path.join(BASE, "tg_message.html")

# (매체명, url, 기본 섹션, 매체 가중치)
FEEDS = [
    # 국내
    ("한국경제", "https://www.hankyung.com/feed/finance", "markets", 1.2),
    ("한국경제", "https://www.hankyung.com/feed/economy", "economy", 1.2),
    ("한국경제", "https://www.hankyung.com/feed/international", "global", 1.1),
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml", "economy", 1.2),
    ("연합뉴스", "https://www.yna.co.kr/rss/market.xml", "markets", 1.2),
    ("조선비즈", "https://biz.chosun.com/arc/outboundfeeds/rss/category/stock/?outputType=xml", "markets", 1.1),
    ("조선비즈", "https://biz.chosun.com/arc/outboundfeeds/rss/category/policy/?outputType=xml", "economy", 1.1),
    ("연합인포맥스", "https://news.einfomax.co.kr/rss/allArticle.xml", "markets", 1.1),
    ("서울경제", "https://www.sedaily.com/rss/finance", "markets", 1.0),
    ("이데일리", "https://rss.edaily.co.kr/stock_news.xml", "markets", 1.0),
    ("파이낸셜뉴스", "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml", "markets", 1.0),
    ("파이낸셜뉴스", "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml", "economy", 1.0),
    ("뉴시스", "https://www.newsis.com/RSS/economy.xml", "economy", 0.9),
    ("블록미디어", "https://www.blockmedia.co.kr/feed", "crypto", 1.0),
    ("토큰포스트", "https://www.tokenpost.kr/rss", "crypto", 0.9),
    ("코인리더스", "https://www.coinreaders.com/rss/rss_news.php", "crypto", 0.8),
    # Global (English)
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "global", 1.2),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "economy", 1.2),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "markets", 1.2),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "markets", 1.2),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "markets", 1.0),
    ("Financial Times", "https://www.ft.com/markets?format=rss", "markets", 1.3),
    ("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml", "economy", 1.2),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", "global", 1.0),
    ("The Guardian", "https://www.theguardian.com/business/economics/rss", "economy", 1.0),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "economy", 1.5),
    ("Investing.com", "https://www.investing.com/rss/news_14.rss", "economy", 0.9),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml", "markets", 0.8),
    ("Cointelegraph", "https://cointelegraph.com/rss", "crypto", 1.0),
    ("Decrypt", "https://decrypt.co/feed", "crypto", 1.0),
]

SECTIONS = [
    ("markets", "증시·주식", "Markets"),
    ("economy", "경제·정책", "Economy & Policy"),
    ("crypto", "코인·가상자산", "Crypto"),
    ("global", "글로벌 경제", "Global"),
]

KEYWORDS_KO = {
    "crypto": r"(비트코인|이더리움|가상자산|암호화폐|가상화폐|스테이블코인|블록체인|업비트|빗썸|코인원|솔라나|리플|알트코인|밈코인|디파이|토큰|클래리티법|지니어스법)",
    "economy": r"(금리|기준금리|한은|한국은행|금통위|물가|인플레|소비자물가|성장률|고용|실업|일자리|환율|원·달러|원달러|관세|무역|수출|수입|경상수지|재정|국채|기재부|기획재정부|경기|부동산|주택|집값|가계부채|대출|세금|세제|예산|정책|금융당국|금감원|금융위)",
    "markets": r"(코스피|코스닥|증시|주가|주식|상장|공모주|외국인|기관|개미|개인투자자|순매수|순매도|시가총액|시총|실적|영업이익|매출|반도체|삼성전자|하이닉스|목표주가|배당|공매도|증권|마감시황|장중|급등주|종목|밸류업)",
    "global": r"(미국|중국|일본|유럽|트럼프|연준|파월|워시|월가|뉴욕증시|나스닥|다우|이란|호르무즈|엔화|위안|유로|달러화|국제유가|글로벌|해외)",
}
KEYWORDS_EN = {
    "crypto": r"\b(bitcoin|btc|eth|ether(eum)?|crypto\w*|stablecoins?|blockchain|solana|xrp|ripple|coinbase|binance|defi|altcoins?|memecoins?|web3|nfts?|tether|usdc|dogecoin|clarity act|genius act)\b",
    "economy": r"\b(fed|federal reserve|fomc|powell|warsh|inflation|cpi|ppi|pce|gdp|payrolls?|jobs report|unemployment|jobless|rate (cut|hike)s?|interest rates?|treasur(y|ies)|recession|tariffs?|central bank|ecb|boj|economy|economic|retail sales|consumer (spending|confidence|sentiment)|deficit|housing|mortgages?)\b",
    "markets": r"\b(stocks?|s&p|nasdaq|dow|shares|equit(y|ies)|earnings|futures|rally|sell-?off|ipo|wall street|index|indexes|investors?|bonds?|yields?|etfs?|volatility|vix|nvidia|apple|tesla)\b",
    "global": r"\b(china|chinese|europe|european|japan|uk|britain|germany|india|trump|iran|russia|opec|oil|global|world|imf|trade war)\b",
}
KEYWORDS = {k: "(?:%s)|(?:%s)" % (KEYWORDS_KO[k], KEYWORDS_EN[k]) for k in KEYWORDS_KO}
HIGH_IMPACT = ("(?:기준금리|금리 (?:인상|인하|동결)|연준|물가|고용지표|관세|급락|급등|폭락|폭등|사상 최고|최고치|최저치|붕괴|돌파|어닝|실적 발표|합병|인수|파산|상장폐지|디폴트|비트코인|삼성전자|하이닉스|엔비디아|환율|코스피|외국인 (?:매도|매수|투매)|쇼크|위기)"
               r"|\b(?:fed|fomc|rate (?:cut|hike)|inflation|cpi|jobs report|payrolls|gdp|recession|tariffs?|earnings|record|plunge[sd]?|surge[sd]?|soar[sd]?|tumble[sd]?|crash|sell-?off|rally|bitcoin|sec|etf|bankrupt\w*|chapter 11|merger|acquisition|layoffs?|default|downgrade|nvidia|opec|treasury yields?)\b")
JUNK = (r"(\[포토\]|\[사진\]|\[인사\]|\[부고\]|부음|\[게시판\]|\[표\]|\[카드뉴스\]|\[영상\]|\[광고\]|운세|헌혈|봉사활동|후원|기부|업무협약|\bmou\b|시상식|수상|채용|이벤트|경품|프로모션|캠페인|사명 변경|간담회 개최|설명회 개최|오늘의 일정|증시일정|사회공헌|창립 \d+주년"
        r"|mortgage (and refinance )?rates today|cd rates|savings (account )?rates|best (credit|high-yield|savings)|horoscope|quiz|podcast|\bwatch:|\bvideo\b|sponsored|price prediction|how to buy|deals? of the day|coupon|this week in|newsletter)")
STOP = set("the a an and or of to in on for with at by from as is are was were be been it its this that these those after before over under into amid about than more most new says say said will would could may might can up down out off not no but if his her their our your what why how who when which just still also has have had do does did we you they he she them us all any some per vs via year years week day today".split())
FNG_KR = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}
WEEKDAYS = "월화수목금토일"


def is_ko(text):
    return bool(HANGUL.search(text))


# ---------------------------------------------------------------- 수집

def get(url, timeout=20, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))  # 일시적 연결 끊김이면 잠시 후 다시 시도


def get_json(url):
    try:
        return json.loads(get(url))
    except Exception as e:
        log("데이터 실패 %s: %s" % (url, e))
        return None


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def clean(text, limit=None):
    text = html.unescape(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(The post .*|Continue reading.*|Read more.*|더보기.*|기사 원문.*|\[…\]|\[\.\.\.\]|…$)$", "", text).strip()
    # "[서울=뉴시스]홍길동 기자 = ", "(서울=연합뉴스) 홍길동 기자 = " 같은 기자 표기 제거
    text = re.sub(r"^\s*[\[(【]?[^\])】]{0,20}=[^\])】]{0,12}[\])】]\s*", "", text)
    text = re.sub(r"^[^=.\n]{0,30}?(기자|특파원|객원기자)\s*=\s*", "", text)
    text = re.sub(r"^[가-힣]{2,4}\s*(기자|특파원)\s+", "", text)
    text = re.sub(r"^\s*\[[^\]]{1,15}\]\s*", "", text).replace("##", "")
    text = re.sub(r"\s?[\[(]\d{6}[\])]", "", text).strip()  # 종목코드 [005930] 제거
    if limit and len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("다."), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + (2 if cut[end:end + 2] == "다." else 1)] if end > limit * 0.45 else cut.rsplit(" ", 1)[0] + "…"
    return text.strip()


def parse_date(s):
    if not s:
        return None
    s = re.sub(r",(?=\S)", ", ", s.strip())
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=LOCAL_TZ)
    return d.astimezone(timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_xml_items(raw):
    """ElementTree로 파싱하고, 실패하면(EUC-KR 등) 정규식으로 항목을 뽑습니다."""
    m = re.search(rb'encoding=["\']([^"\']+)', raw[:200])
    enc = m.group(1).decode().lower() if m else "utf-8"
    if enc not in ("utf-8", "utf8"):
        raw = raw.decode(enc, "replace").encode("utf-8")
        raw = re.sub(rb'encoding=["\'][^"\']+["\']', b'encoding="utf-8"', raw, count=1)
    try:
        root = ET.fromstring(raw)
        out = []
        for node in root.iter():
            if local(node.tag) not in ("item", "entry"):
                continue
            f = {}
            for child in node:
                t = local(child.tag)
                if t == "link" and child.get("href"):
                    f.setdefault("link", child.get("href"))
                elif child.text:
                    f.setdefault(t, child.text.strip())
            out.append(f)
        return out
    except ET.ParseError:
        text = raw.decode("utf-8", "replace")
        out = []
        for block in re.findall(r"<item[\s>].*?</item>", text, re.S):
            f = {}
            for t, v in re.findall(r"<(?:\w+:)?(title|link|description|pubDate|date)[^>]*>(.*?)</(?:\w+:)?\1>", block, re.S):
                f.setdefault(t, re.sub(r"<!\[CDATA\[|\]\]>", "", v).strip())
            out.append(f)
        return out


def fetch_feed(feed):
    name, url, section, weight = feed
    try:
        entries = parse_xml_items(get(url))
    except Exception as e:
        log("피드 실패 %s (%s): %s" % (name, url, e))
        return []
    items = []
    for f in entries:
        title = clean(f.get("title"))
        link = (f.get("link") or "").strip()
        if not title or not link:
            continue
        date = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        desc = clean(f.get("description") or f.get("summary") or f.get("encoded"), 220 if is_ko(title) else 320)
        if desc.lower().startswith(title.lower()[:25]):
            desc = desc[len(title):].strip(" -–—:") if len(desc) > len(title) + 30 else ""
        items.append(dict(title=title, link=link, date=date, summary=desc,
                          source=name, section=section, weight=weight))
    log("%-15s %3d건" % (name, len(items)))
    return items


# ---------------------------------------------------------------- 순위

NUMWORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def tokens(title):
    """한글은 두 글자 조각(bigram), 영문·숫자는 단어 단위로 비교합니다."""
    t = re.sub(r"\[[^\]]*\]", " ", title.lower())
    out = set()
    for w in re.findall(r"[a-z0-9&%.]+|[가-힣]+", t):
        if HANGUL.match(w):
            out |= {w[i:i + 2] for i in range(len(w) - 1)}
        else:
            w = w.strip(".")
            w = NUMWORDS.get(w, w)
            if (len(w) > 2 or w.isdigit()) and w not in STOP:
                out.add(w)
    return out


# 언어가 달라도 같은 사건인지 판단하기 위한 한·영 공통 개념
CONCEPTS = [
    ("10y", r"10년물|10년 만기|10-year|ten-year|10yr"), ("treasury", r"국채|treasur"),
    ("fed", r"연준|fomc|\bfed\b|federal reserve"), ("rate", r"금리|interest rate|\byields?\b"),
    ("hike", r"인상|\bhikes?\b"), ("cut", r"인하|\bcuts?\b"), ("bitcoin", r"비트코인|bitcoin|\bbtc\b"),
    ("ethereum", r"이더리움|ethereum|\bether\b"), ("clarity", r"클래리티|clarity act"),
    ("nvidia", r"엔비디아|nvidia"), ("ai", r"\bai\b|인공지능"), ("slowdown", r"속도\s?조절|제동|slow\s?down"),
    ("chip", r"반도체|chips?\b|semiconductor"), ("oil", r"유가|\boil\b|crude|브렌트|brent|wti"),
    ("china", r"중국|china|chinese"), ("tariff", r"관세|tariff"), ("inflation", r"물가|인플레|inflation|\bcpi\b"),
    ("jobs", r"고용|\bjobs\b|payroll"), ("kospi", r"코스피|kospi"), ("samsung", r"삼성전자|samsung"),
    ("hynix", r"하이닉스|hynix"), ("trump", r"트럼프|trump"), ("warsh", r"워시|warsh"),
    ("iran", r"이란|\biran|호르무즈|hormuz"), ("retail", r"소매판매|retail sales"), ("gold", r"금값|\bgold\b"),
]


def concepts(title):
    t = title.lower()
    found = {name for name, p in CONCEPTS if re.search(p, t)}
    return found | set(re.findall(r"\d+(?:\.\d+)?%", t))


def same_topic(a, b):
    shared = a["concepts"] & b["concepts"]
    return len(shared) >= 3 or (len(shared) >= 2 and any(x.endswith("%") for x in shared))


def classify(item):
    title = item["title"].lower()
    text = title + " " + item["summary"].lower()
    if item["section"] == "crypto" or re.search(KEYWORDS["crypto"], title):
        return "crypto"
    hits = {k: len(re.findall(p, text)) + 2 * len(re.findall(p, title)) for k, p in KEYWORDS.items() if k != "crypto"}
    best = max(hits, key=hits.get)
    return best if hits[best] >= 3 and hits[best] > hits.get(item["section"], 0) else item["section"]


def relevance(title):
    t = title.lower()
    return sum(len(re.findall(p, t)) for p in KEYWORDS.values())


def build_stories(items, now):
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    seen, fresh = set(), []
    for it in items:
        if it["date"] and (it["date"] < cutoff or it["date"] > now + timedelta(hours=2)):
            continue
        key = re.sub(r"\W", "", it["title"].lower())
        if re.search(JUNK, it["title"], re.I) or it["link"] in seen or key in seen:
            continue
        seen.update((it["link"], key))
        it["tokens"] = tokens(it["title"])
        it["section"] = classify(it)
        fresh.append(it)

    # 여러 매체가 보도한 같은 사건을 하나로 묶기
    clusters = []
    for it in sorted(fresh, key=lambda x: -x["weight"]):
        a = it["tokens"]
        min_inter = 4 if is_ko(it["title"]) else 3
        best, best_j = None, 0.0
        for c in clusters:
            b = c[0]["tokens"]
            inter = len(a & b)
            if inter < min_inter:
                continue
            j = inter / len(a | b)
            if j > best_j:
                best, best_j = c, j
        if best is not None and best_j >= 0.3:
            best.append(it)
        else:
            clusters.append([it])

    stories = []
    for c in clusters:
        lead = max(c, key=lambda x: (x["weight"], len(x["summary"])))
        # 대표 기사 요약 우선, 없으면 제목이 가장 비슷한 기사의 요약
        summary = lead["summary"] or next((x["summary"] for x in sorted(
            c, key=lambda x: -len(x["tokens"] & lead["tokens"])) if x["summary"]), "")
        sources = sorted({x["source"] for x in c})
        date = max((x["date"] for x in c if x["date"]), default=None)
        age_h = (now - date).total_seconds() / 3600 if date else WINDOW_HOURS
        text = " ".join(x["title"] for x in c).lower()
        rel = relevance(lead["title"])
        # 금융 키워드가 없는 기사는 여러 매체가 보도해도 가산점을 40%만 받습니다
        score = (2.5 * (len(sources) - 1) * (1.0 if rel else 0.4)
                 + 0.4 * min(len(c) - len(sources), 3)
                 + lead["weight"]
                 + 0.8 * min(len(set(m.group(0) for m in re.finditer(HIGH_IMPACT, text))), 4)
                 + 0.4 * min(rel, 3) - (1.0 if rel == 0 else 0)
                 + max(0.0, 2.0 - age_h / 8)
                 + (0.5 if summary else -0.3)
                 - (0.8 if lead["title"].endswith("?") else 0))
        stories.append(dict(title=lead["title"], link=lead["link"], summary=summary,
                            source=lead["source"], sources=sources, date=date,
                            section=lead["section"], score=score, ko=is_ko(lead["title"]),
                            concepts=concepts(lead["title"])))
    stories.sort(key=lambda s: -s["score"])
    return stories


def pick_mixed(pool, n, min_each):
    """점수순으로 n건을 고르되 한국어·영어 기사를 각각 최소 min_each건 넣습니다."""
    picked = [s for s in pool if s["ko"]][:min_each] + [s for s in pool if not s["ko"]][:min_each]
    ids = {id(s) for s in picked}
    picked += [s for s in pool if id(s) not in ids][:max(0, n - len(picked))]
    return sorted(picked, key=lambda s: -s["score"])[:n]


def distinct(pool, taken=(), limit=150):
    """이미 고른 기사와 같은 사건(언어가 달라도)인 기사를 뺍니다."""
    out = []
    for s in pool:
        if not any(same_topic(s, t) for t in list(taken) + out):
            out.append(s)
            if len(out) >= limit:
                break
    return out


def select(stories):
    """1면(톱+3), 섹션별 7건, 그 밖의 뉴스 12건을 고릅니다. 1면과 같은 사건은 섹션에서 뺍니다."""
    lead = stories[0]
    seconds = pick_mixed(distinct(stories[1:], [lead]), 3, 1)
    front = [lead] + seconds
    used = {id(s) for s in front}
    by_sec = {}
    for key, _, _ in SECTIONS:
        pool = [s for s in stories if s["section"] == key and id(s) not in used]
        by_sec[key] = pick_mixed(distinct(pool, front), 7, 3)
        used |= {id(s) for s in by_sec[key]}
    also = pick_mixed(distinct([s for s in stories if id(s) not in used]), 12, 4)
    return lead, seconds, by_sec, also


def og_description(url):
    try:
        raw = get(url, timeout=10)[:300000]
    except Exception:
        return ""
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    enc = m.group(1).decode().lower() if m else "utf-8"
    try:
        text = raw.decode("cp949" if enc in ("euc-kr", "ks_c_5601-1987") else enc, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    for pat in (r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:description|description)["\']'):
        m = re.search(pat, text, re.I)
        if m:
            return clean(m.group(1), 220 if is_ko(m.group(1)) else 320)
    return ""


def enrich_summaries(stories):
    lead, seconds, by_sec, _ = select(stories)
    shown = [lead] + seconds + [s for v in by_sec.values() for s in v]
    todo = [s for s in shown if not s["summary"]]
    with ThreadPoolExecutor(max_workers=16) as ex:
        for s, d in zip(todo, ex.map(lambda s: og_description(s["link"]), todo)):
            if d and not d.lower().startswith(s["title"].lower()[:20]):
                s["summary"] = d
    log("요약 보강 %d/%d" % (sum(1 for s in todo if s["summary"]), len(todo)))


# ---------------------------------------------------------------- 시세

YAHOO = [
    ("코스피 KOSPI", "^KS11", "idx"), ("코스닥 KOSDAQ", "^KQ11", "idx"),
    ("S&P 500", "^GSPC", "idx"), ("나스닥 Nasdaq", "^IXIC", "idx"), ("다우 Dow", "^DJI", "idx"),
    ("닛케이 Nikkei", "^N225", "idx"), ("VIX 공포지수", "^VIX", "idx"),
    ("미 10년물 US 10Y", "^TNX", "yield"), ("원/달러 USD/KRW", "KRW=X", "krw"),
    ("달러인덱스 DXY", "DX-Y.NYB", "idx"), ("금 Gold", "GC=F", "usd"), ("WTI 유가 Oil", "CL=F", "usd"),
]


def yahoo_quote(entry):
    label, sym, kind = entry
    d = get_json("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d"
                 % urllib.request.quote(sym))
    try:
        res = d["chart"]["result"][0]
        closes = [x for x in res["indicators"]["quote"][0]["close"] if x is not None]
        price = res["meta"].get("regularMarketPrice") or closes[-1]
        if closes and abs(closes[-1] - price) / price > 1e-6:
            closes.append(price)
        prev = closes[-2]
        return dict(label=label, symbol=sym, kind=kind, price=price,
                    change=(price / prev - 1) * 100, abs_change=price - prev, spark=closes[-22:])
    except Exception:
        return None


COINS = "bitcoin,ethereum,ripple,solana,binancecoin"
COIN_KR = {"BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "SOL": "솔라나"}


def crypto_data():
    coins = get_json("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=%s"
                     "&order=market_cap_desc&sparkline=true&price_change_percentage=24h" % COINS) or []
    out = []
    for c in coins:
        sym = c["symbol"].upper()
        spark = (c.get("sparkline_in_7d") or {}).get("price") or []
        label = "%s %s" % (COIN_KR[sym], sym) if sym in COIN_KR else sym
        out.append(dict(label=label, symbol=sym, kind="usd", price=c["current_price"],
                        change=c.get("price_change_percentage_24h") or 0.0, spark=spark[::6]))
    glob = (get_json("https://api.coingecko.com/api/v3/global") or {}).get("data") or {}
    fng = ((get_json("https://api.alternative.me/fng/?limit=1") or {}).get("data") or [{}])[0]
    return out, glob, fng


# ---------------------------------------------------------------- 브리핑 문장

def move(pct, form):
    """등락률을 한국어 서술로. form: 고(연결) / 다(종결) / 해(원인) / 한(수식)"""
    a = abs(pct)
    if a < 0.15:
        return {"고": "보합세를 보였고", "다": "보합세를 보였다", "해": "보합세를 보이며", "한": "보합권인"}[form]
    word = ("소폭 상승" if a < 0.5 else "상승" if a < 1.5 else "급등" if a < 3 else "폭등") if pct > 0 else \
           ("소폭 하락" if a < 0.5 else "하락" if a < 1.5 else "급락" if a < 3 else "폭락")
    return "%.2f%% %s%s" % (a, word, {"고": "했고", "다": "했다", "해": "해", "한": "한"}[form])


def verb(pct):
    a = abs(pct)
    if a < 0.15:
        return "was little changed"
    if pct > 0:
        return ("edged up" if a < 0.5 else "rose" if a < 1.5 else "jumped" if a < 3 else "surged") + " %.2f%%" % a
    return ("edged down" if a < 0.5 else "fell" if a < 1.5 else "slid" if a < 3 else "tumbled") + " %.2f%%" % a


def won(n):
    if n >= 1e8:
        eok, man = int(n // 1e8), int((n % 1e8) // 1e4)
        return "%d억 %s만원" % (eok, "{:,}".format(man)) if man else "%d억원" % eok
    if n >= 1e4:
        return "{:,}원".format(int(round(n)))
    return "{:,.1f}원".format(n)


def market_brief(q, coins, fng):
    parts = []
    ks, kq = q.get("^KS11"), q.get("^KQ11")
    if ks:
        s = "코스피는 %s로 %s" % ("{:,.2f}".format(ks["price"]), move(ks["change"], "고" if kq else "다"))
        if kq:
            s += ", 코스닥은 " + move(kq["change"], "다")
        parts.append(s + ".")
    us = [(n, q[s]) for n, s in (("S&P 500은", "^GSPC"), ("나스닥은", "^IXIC"), ("다우는", "^DJI")) if s in q]
    if us:
        parts.append("최근 뉴욕증시에서 " + ", ".join(
            "%s %s" % (n, move(x["change"], "다" if i == len(us) - 1 else "고")) for i, (n, x) in enumerate(us)) + ".")
    tnx = q.get("^TNX")
    if tnx:
        bps = tnx["abs_change"] * 100
        parts.append("미국 10년물 국채 금리는 %s." % (
            "%.2f%% 부근에서 움직임이 거의 없었다" % tnx["price"] if abs(bps) < 1.5 else
            "%.0fbp %s %.2f%%를 기록했다" % (abs(bps), "오른" if bps > 0 else "내린", tnx["price"])))
    krw = q.get("KRW=X")
    if krw:
        parts.append("원·달러 환율은 %s원으로 전일보다 %s." % ("{:,.1f}".format(krw["price"]), move(krw["change"], "다")))
    oil, gold = q.get("CL=F"), q.get("GC=F")
    if oil and gold:
        parts.append("WTI 유가는 %s 배럴당 %.2f달러, 금값은 %s 온스당 %s달러였다." % (
            move(oil["change"], "한"), oil["price"], move(gold["change"], "한"), "{:,.0f}".format(gold["price"])))
    btc = next((c for c in coins if c["symbol"] == "BTC"), None)
    if btc:
        s = "비트코인은 24시간 동안 %s %s달러" % (move(btc["change"], "해"), "{:,.0f}".format(btc["price"]))
        if krw:
            s += "(약 %s)" % won(btc["price"] * krw["price"])
        s += "에 거래되고 있다"
        if fng.get("value"):
            s += ". 코인 시장 심리는 ‘%s’(%s/100) 단계다" % (FNG_KR.get(fng["value_classification"], fng["value_classification"]), fng["value"])
        parts.append(s + ".")
    vix = q.get("^VIX")
    if vix:
        mood = "낮은" if vix["price"] < 15 else "보통" if vix["price"] < 20 else "높은" if vix["price"] < 30 else "매우 높은"
        parts.append("VIX 공포지수는 %.1f로 시장 변동성은 %s 수준이다." % (vix["price"], mood))
    return " ".join(parts)


def market_brief_en(q, coins, fng):
    parts = []
    ks, kq = q.get("^KS11"), q.get("^KQ11")
    if ks:
        parts.append("In Seoul, the KOSPI %s%s." % (verb(ks["change"]), " and the KOSDAQ %s" % verb(kq["change"]) if kq else ""))
    spx, ndx, dji = q.get("^GSPC"), q.get("^IXIC"), q.get("^DJI")
    if spx:
        extra = ["the Nasdaq %s" % verb(ndx["change"])] if ndx else []
        extra += ["the Dow %s" % verb(dji["change"])] if dji else []
        parts.append("At the last US close, the S&P 500 %s%s." % (verb(spx["change"]), ", while " + " and ".join(extra) if extra else ""))
    tnx = q.get("^TNX")
    if tnx:
        bps = tnx["abs_change"] * 100
        parts.append("The 10-year Treasury yield %s %.2f%%." % (
            "held near" if abs(bps) < 1.5 else ("rose %.0f bps to" % bps if bps > 0 else "fell %.0f bps to" % -bps), tnx["price"]))
    oil, gold = q.get("CL=F"), q.get("GC=F")
    if oil and gold:
        parts.append("WTI crude %s to $%.2f and gold %s to $%s." % (
            verb(oil["change"]), oil["price"], verb(gold["change"]), "{:,.0f}".format(gold["price"])))
    btc = next((c for c in coins if c["symbol"] == "BTC"), None)
    if btc:
        s = "Bitcoin %s over 24 hours to $%s" % (verb(btc["change"]), "{:,.0f}".format(btc["price"]))
        if fng.get("value"):
            s += ", with crypto sentiment at “%s” (%s/100)" % (fng["value_classification"], fng["value"])
        parts.append(s + ".")
    return " ".join(parts)


# ---------------------------------------------------------------- HTML

def esc(s):
    return html.escape(s or "", quote=True)


def ago(d, now, ko):
    if not d:
        return ""
    h = (now - d).total_seconds() / 3600
    if ko:
        return "방금 전" if h < 1 else "%d시간 전" % h if h < 24 else "%d일 전" % (h // 24)
    return "just now" if h < 1 else "%dh ago" % h if h < 24 else "%dd ago" % (h // 24)


def sparkline(vals, w=110, h=30):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join("%.1f,%.1f" % (i * w / (len(vals) - 1), h - 2 - (v - lo) / rng * (h - 4)) for i, v in enumerate(vals))
    cls = "up" if vals[-1] >= vals[0] else "down"
    return '<svg class="spark %s" viewBox="0 0 %d %d" preserveAspectRatio="none" aria-hidden="true"><polyline points="%s"/></svg>' % (cls, w, h, pts)


def fmt_price(x):
    p = x["price"]
    if x["kind"] == "yield":
        return "%.2f%%" % p
    if x["kind"] == "krw":
        return "{:,.1f}원".format(p)
    s = "{:,.2f}".format(p) if p < 10000 else "{:,.0f}".format(p)
    return ("$" + s) if x["kind"] == "usd" else s


def quote_card(x, krw_rate=None):
    ch = x["change"]
    cls = "up" if ch > 0.005 else "down" if ch < -0.005 else "flat"
    arrow = "▲" if cls == "up" else "▼" if cls == "down" else "■"
    sub = '<div class="qk">≈ %s</div>' % won(x["price"] * krw_rate) if krw_rate else ""
    return ('<div class="q"><div class="ql">%s</div><div class="qp">%s</div>%s'
            '<div class="qc %s">%s %.2f%%</div>%s</div>') % (esc(x["label"]), fmt_price(x), sub, cls, arrow, abs(ch), sparkline(x["spark"]))


def story_html(s, now, cls="story", summary=True):
    ko = s["ko"]
    meta = "%s · %s" % (esc(s["source"]), ago(s["date"], now, ko))
    if len(s["sources"]) > 1:
        others = esc(", ".join(x for x in s["sources"] if x != s["source"]))
        meta += (' · <span class="multi" title="함께 보도: %s">%d개 매체 보도</span>' if ko else
                 ' · <span class="multi" title="Also covered by: %s">%d outlets</span>') % (others, len(s["sources"]))
    body = '<p>%s</p>' % esc(s["summary"]) if summary and s["summary"] and not PUBLIC else ""
    return '<article class="%s" lang="%s"><h3><a href="%s" target="_blank" rel="noopener">%s</a></h3>%s<div class="meta">%s</div></article>' % (
        cls, "ko" if ko else "en", esc(s["link"]), esc(s["title"]), body, meta)


def kdate(d):
    return "%d년 %d월 %d일 %s요일" % (d.year, d.month, d.day, WEEKDAYS[d.weekday()])


def render(now_local, stories, quotes, coins, glob, fng, brief, brief_en, issue, archive_links, watch_html=""):
    now = now_local.astimezone(timezone.utc)
    lead, seconds, by_sec, also = select(stories)

    krw = quotes.get("KRW=X", {}).get("price")
    takeaways = "".join('<li lang="%s"><a href="%s" target="_blank" rel="noopener">%s</a></li>' % (
        "ko" if s["ko"] else "en", esc(s["link"]), esc(s["title"])) for s in pick_mixed(distinct(stories), 5, 2))
    mkt_cards = "".join(quote_card(quotes[sym]) for _, sym, _ in YAHOO if sym in quotes)
    coin_cards = "".join(quote_card(c, krw) for c in coins)
    cstats = []
    if glob.get("total_market_cap"):
        cstats.append("전체 시가총액 Market cap <b>$%.2fT</b> (24h %+.1f%%)" % (glob["total_market_cap"]["usd"] / 1e12, glob.get("market_cap_change_percentage_24h_usd") or 0))
    if glob.get("market_cap_percentage"):
        cstats.append("비트코인 점유율 BTC dominance <b>%.1f%%</b>" % glob["market_cap_percentage"]["btc"])
    if fng.get("value"):
        cstats.append("공포·탐욕 지수 Fear &amp; Greed <b>%s · %s %s</b>" % (
            fng["value"], esc(FNG_KR.get(fng["value_classification"], "")), esc(fng["value_classification"])))
    sections = "".join(
        '<section class="col"><h2>%s <span class="en-l">%s</span></h2>%s</section>' % (
            ko, en, "".join(story_html(s, now) for s in by_sec[key]) or '<p class="empty">No major stories · 주요 기사 없음</p>')
        for key, ko, en in SECTIONS)
    also_html = "".join('<li>%s</li>' % story_html(s, now, "mini", summary=False) for s in also)
    arch = " · ".join('<a href="%s">%s</a>' % (href, label) for label, href in archive_links[:14])
    names = sorted({f[0] for f in FEEDS})
    sources = "국내 %s / Global %s" % (", ".join(n for n in names if is_ko(n)), ", ".join(n for n in names if not is_ko(n)))
    head, sub, ad, notice = public_blocks(now_local, lead, brief, issue)
    top5 = '<div class="top5"><div class="label">꼭 알아야 할 뉴스 5 <span class="en-l">Top 5</span></div><ol>%s</ol>%s</div>' % (takeaways, sub)

    return TEMPLATE.format(
        name=NAME, name_en=NAME_EN, date=kdate(now_local), date_en=now_local.strftime("%a, %b %-d, %Y"),
        issue=issue, time=now_local.strftime("%H:%M"),
        brief=esc(brief), brief_en=esc(brief_en), takeaways=takeaways, mkt_cards=mkt_cards, coin_cards=coin_cards,
        cstats=" &nbsp;·&nbsp; ".join(cstats), lead=story_html(lead, now, "lead"),
        seconds="".join(story_html(s, now, "second") for s in seconds), sections=sections,
        also=also_html, archive=arch or "—", nstories=len(stories), sources=esc(sources), watch=watch_html,
        head=head, ad=ad, notice=notice,
        # 공개판은 요약이 없어 1면 왼쪽이 짧으므로 '꼭 알아야 할 뉴스 5'를 왼쪽으로 옮깁니다
        top_left=top5 if PUBLIC else "", top_right="" if PUBLIC else top5)


def public_blocks(now_local, lead, brief, issue):
    """공개판에만 들어가는 검색·공유 메타 태그, 구독 안내, 광고 자리, 저작권 안내."""
    if not PUBLIC:
        return "", "", "", ""
    title = "%s 제%d호 — %s" % (NAME, issue, kdate(now_local))
    desc = clean(brief, 150) or "매일 밤 9시, 국내외 증시·경제·코인 뉴스를 한 장으로."
    head = ['<meta name="description" content="%s">' % esc(desc),
            '<meta property="og:type" content="article"><meta property="og:site_name" content="%s · %s">' % (NAME, NAME_EN),
            '<meta property="og:title" content="%s"><meta property="og:description" content="%s">' % (esc(title), esc(lead["title"])),
            '<meta name="twitter:card" content="summary">']
    if SITE_URL:
        head.append('<link rel="canonical" href="%s/"><meta property="og:url" content="%s/">' % (esc(SITE_URL), esc(SITE_URL)))
    sub = ('<div class="sub"><b>매일 밤 9시, 텔레그램으로 받아보기</b><span>시장 브리핑과 꼭 알아야 할 뉴스 5개를 보내 드립니다.</span>'
           '<a class="btn" href="%s" target="_blank" rel="noopener">텔레그램 채널 구독 · Subscribe</a></div>' % esc(TELEGRAM_URL)) if TELEGRAM_URL else ""
    ad = ('<div class="ad"><ins class="kakao_ad_area" style="display:none" data-ad-unit="%s" data-ad-width="728" data-ad-height="90"></ins>'
          '<script async src="https://t1.daumcdn.net/kas/static/ba.min.js"></script></div>' % esc(ADFIT_UNIT)) if ADFIT_UNIT else ""
    notice = ("<br>마켓 나이트는 각 매체의 기사 제목과 링크를 모아 소개하며, 기사 저작권은 해당 매체에 있습니다. "
              "시장 브리핑은 시세 데이터로 자동 작성됩니다." + (" 문의 Contact: %s" % esc(CONTACT) if CONTACT else ""))
    return "".join(head), sub, ad, notice


TEMPLATE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} · {name_en} — {date}</title>
{head}
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;600;900&family=Noto+Sans+KR:wght@400;500;700&family=Playfair+Display:ital,wght@1,700&display=swap" rel="stylesheet">
<style>
:root{{--paper:#f7f3ea;--ink:#1b1a17;--muted:#6b665c;--rule:#1b1a17;--hair:#d8d0bf;--up:#c62828;--down:#1558b0;--accent:#8a1c1c;
--serif:"Noto Serif KR","AppleMyungjo","Nanum Myeongjo",Georgia,serif;--sans:"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",-apple-system,sans-serif}}
@media (prefers-color-scheme:dark){{:root{{--paper:#16150f;--ink:#ece6d8;--muted:#a39c8c;--rule:#ece6d8;--hair:#3a372e;--up:#ff6b6b;--down:#6aa8ff;--accent:#e2a36b}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 var(--serif);padding:0 20px;word-break:keep-all;overflow-wrap:break-word}}
.wrap{{max-width:1240px;margin:0 auto;padding-block:18px 40px}}
a{{color:inherit;text-decoration:none}} a:hover{{text-decoration:underline;text-decoration-thickness:1px}}
.en-l{{font:600 11px/1 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-left:6px;vertical-align:middle}}
.topline{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;font:500 12px/1 var(--sans);letter-spacing:.04em;color:var(--muted);border-bottom:1px solid var(--rule);padding-bottom:8px}}
.mast{{text-align:center;padding:14px 0 12px;border-bottom:4px double var(--rule)}}
.mast h1{{font:900 clamp(40px,8vw,84px)/1.1 var(--serif);margin:0;letter-spacing:-.02em}}
.mast .en-name{{font:italic 700 clamp(18px,2.4vw,26px)/1.2 "Playfair Display",Georgia,serif;margin-top:2px}}
.mast .tag{{color:var(--muted);margin-top:4px;font-size:14.5px}}
.band{{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));border-bottom:1px solid var(--rule)}}
.q{{padding:10px 10px 6px;border-right:1px solid var(--hair);font-family:var(--sans);line-height:1.35}}
.ql{{font-size:12px;color:var(--muted)}}
.qp{{font-weight:700;font-size:16px;font-variant-numeric:tabular-nums}}
.qk{{font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}}
.qc{{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}}
.up{{color:var(--up)}} .down{{color:var(--down)}} .flat{{color:var(--muted)}}
.spark{{width:100%;height:26px;display:block;margin-top:4px}} .spark polyline{{fill:none;stroke-width:1.5;vector-effect:non-scaling-stroke}}
.spark.up polyline{{stroke:var(--up)}} .spark.down polyline{{stroke:var(--down)}}
.label{{font:700 13px/1 var(--sans);letter-spacing:.06em;color:var(--accent);margin:18px 0 10px}}
.front{{display:grid;grid-template-columns:1.7fr 1fr;gap:28px;padding:18px 0;border-bottom:1px solid var(--rule)}}
.brief{{border-left:1px solid var(--hair);padding-left:24px}}
.brief p{{margin:0 0 10px;font-size:15.5px}} .brief p.en{{font-size:14.5px;color:var(--muted);border-top:1px dotted var(--hair);padding-top:8px}}
.top5 ol{{padding-left:20px;margin:0}} .top5 li{{margin-bottom:6px;font-size:14.5px}}
.front > div > .top5{{margin-top:14px;border-top:1px solid var(--hair)}} .front > div > .top5 li{{font-size:16px}}
.lead h3{{font:900 clamp(26px,3.3vw,40px)/1.3 var(--serif);margin:0 0 10px;letter-spacing:-.02em}}
.lead p{{font-size:17.5px;margin:0 0 8px}}
.seconds{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:18px;padding-top:14px;border-top:1px solid var(--hair)}}
.second h3{{font:700 18.5px/1.4 var(--serif);margin:0 0 6px}} .second p{{font-size:14.5px;margin:0 0 6px}}
.meta{{font:12px/1.4 var(--sans);color:var(--muted)}}
.multi{{color:var(--accent);font-weight:700;cursor:help}}
.cols{{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--rule)}}
.col{{padding:6px 18px 18px;border-right:1px solid var(--hair)}} .col:first-child{{padding-left:0}} .col:last-child{{border-right:0;padding-right:0}}
.col h2{{font:900 21px/1.3 var(--serif);margin:10px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--rule)}}
.story{{padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--hair)}} .story:last-child{{border-bottom:0}}
.story h3{{font:600 16px/1.45 var(--serif);margin:0 0 4px}} .story p{{font-size:14px;margin:0 0 4px;opacity:.88}}
.crypto{{padding:4px 0 14px;border-bottom:1px solid var(--rule)}} .crypto .band{{border:0}} .cstats{{font:13px var(--sans);color:var(--muted);margin-top:8px}}
.also ul{{columns:2;column-gap:32px;list-style:none;padding:0;margin:0}} .also li{{break-inside:avoid;padding:6px 0;border-bottom:1px dotted var(--hair)}}
.mini h3{{font:600 15px/1.45 var(--serif);margin:0}}
footer{{margin-top:22px;padding-top:10px;border-top:4px double var(--rule);font:12px/1.7 var(--sans);color:var(--muted)}}
footer a{{text-decoration:underline}} .empty{{color:var(--muted)}}
.watch{{padding:4px 0 14px;border-bottom:1px solid var(--rule)}} .tbl{{overflow-x:auto}}
.watch table{{width:100%;min-width:820px;border-collapse:collapse;font:13.5px/1.45 var(--sans);font-variant-numeric:tabular-nums}}
.watch th{{text-align:left;font-weight:500;font-size:12px;color:var(--muted);border-bottom:1px solid var(--rule);padding:6px 8px;white-space:nowrap}}
.watch td{{padding:8px;border-bottom:1px solid var(--hair);vertical-align:top}} .watch .num{{text-align:right;white-space:nowrap}}
.watch td small{{display:block;color:var(--muted);font-size:11px}} .watch tr.hit td:first-child{{box-shadow:inset 3px 0 var(--accent)}}
.watch td.news{{max-width:320px}} .watch td.news a{{text-decoration:underline}}
.badge{{display:inline-block;font-size:11.5px;font-weight:700;color:var(--accent);border:1px solid var(--accent);border-radius:3px;padding:0 5px;margin:0 4px 3px 0;white-space:nowrap;cursor:help}}
.note{{font:12px/1.6 var(--sans);color:var(--muted);margin:8px 0 0}}
.sub{{margin-top:16px;padding:12px 14px;border:1px solid var(--rule);font:13.5px/1.5 var(--sans);display:flex;flex-direction:column;gap:6px}}
.sub span{{color:var(--muted)}} .btn{{align-self:flex-start;background:var(--ink);color:var(--paper);font-weight:700;padding:7px 12px;border-radius:3px}} .btn:hover{{text-decoration:none;opacity:.85}}
.ad{{display:flex;justify-content:center;padding:12px 0;border-bottom:1px solid var(--rule);overflow:hidden}}
@media (max-width:980px){{.cols{{grid-template-columns:1fr 1fr}} .col:nth-child(2){{border-right:0;padding-right:0}} .col:nth-child(3){{padding-left:0}}}}
@media (max-width:760px){{.front{{grid-template-columns:1fr}} .brief{{border-left:0;padding-left:0;border-top:1px solid var(--hair);padding-top:14px}} .seconds{{grid-template-columns:1fr}} .cols{{grid-template-columns:1fr}} .col{{padding:6px 0 14px;border-right:0}} .also ul{{columns:1}}}}
</style></head><body><div class="wrap">
<div class="topline"><span>제 {issue}호 · No. {issue}</span><span>{date} · {date_en}</span><span>저녁 9시판 · 9 PM Edition · {time}</span></div>
<header class="mast"><h1>{name}</h1><div class="en-name">{name_en}</div><div class="tag">증시 · 경제 · 주식 · 코인 — Markets, Economy, Stocks &amp; Crypto, Korea and the World</div></header>
<div class="band">{mkt_cards}</div>
<div class="front">
  <div>{lead}<div class="seconds">{seconds}</div>{top_left}</div>
  <aside class="brief"><div class="label" style="margin-top:0">오늘의 시장 브리핑 <span class="en-l">Market Brief</span></div>
  <p lang="ko">{brief}</p><p class="en" lang="en">{brief_en}</p>{top_right}</aside>
</div>
<div class="crypto"><div class="label">코인 데스크 <span class="en-l">Crypto Desk · 24h</span></div><div class="band">{coin_cards}</div><div class="cstats">{cstats}</div></div>
{watch}{ad}
<div class="cols">{sections}</div>
<section class="also"><div class="label">그 밖의 주요 뉴스 <span class="en-l">Also Noteworthy</span></div><ul>{also}</ul></section>
<footer>기사 {nstories}건을 모아 순위를 매겼습니다 · {nstories} stories ranked. 출처 Sources: {sources}. 시세 Prices: Yahoo Finance, CoinGecko, alternative.me. 여러 매체가 함께 보도한 기사일수록 위에 배치됩니다. 투자 권유가 아닙니다 · Not investment advice.{notice}<br>지난 호 Past editions: {archive}</footer>
</div></body></html>"""


# ---------------------------------------------------------------- 알림

def notify(title, subtitle, message, dialog, page):
    subprocess.run(["osascript", "-e", "on run argv",
                    "-e", "display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv) sound name \"Glass\"",
                    "-e", "end run", title, subtitle, message], check=False)
    if not dialog:
        return
    r = subprocess.run(["osascript", "-e", "on run argv",
                        "-e", "set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons {\"나중에 Later\", \"신문 열기 Open\"} default button \"신문 열기 Open\" with icon note giving up after 1800",
                        "-e", "return button returned of r", "-e", "end run", title, message],
                       capture_output=True, text=True, check=False)
    if "Open" in r.stdout:
        subprocess.run(["open", page], check=False)


# ---------------------------------------------------------------- 공개판 배포

def telegram_message(now_local, issue, brief, stories):
    top = "\n".join('%d. <a href="%s">%s</a>' % (i, esc(s["link"]), esc(s["title"]))
                    for i, s in enumerate(pick_mixed(distinct(stories), 5, 2), 1))
    more = '\n\n<a href="%s/">오늘 밤 신문 전체 보기 →</a>' % esc(SITE_URL) if SITE_URL else ""
    return "<b>%s 제%d호</b> · %s\n\n%s\n\n<b>꼭 알아야 할 뉴스 5</b>\n%s%s" % (
        NAME, issue, kdate(now_local), esc(brief), top, more)


def post_telegram():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat and os.path.exists(TG_MESSAGE)):
        log("텔레그램 설정이나 메시지가 없어 건너뜁니다.")
        return 0
    with open(TG_MESSAGE, encoding="utf-8") as fh:
        text = fh.read()
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % token, data, timeout=20)
    except Exception as e:
        log("텔레그램 전송 실패: %s" % e)
        return 1
    log("텔레그램 전송 완료")
    return 0


def write_seo_files(existing, stamp):
    if not SITE_URL:
        return
    urls = [SITE_URL + "/"] + ["%s/archive/%s" % (SITE_URL, f) for f in [stamp + ".html"] + existing]
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s\n</urlset>\n'
                 % "\n".join("<url><loc>%s</loc></url>" % esc(u) for u in urls))
    with open(os.path.join(SITE, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write("User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % SITE_URL)


# ---------------------------------------------------------------- main

def main():
    args = set(sys.argv[1:])
    if "--post-telegram" in args:
        return post_telegram()
    if PUBLIC:
        args.add("--no-alert")
    os.makedirs(ARCHIVE, exist_ok=True)
    now_local = datetime.now().astimezone()
    now = now_local.astimezone(timezone.utc)
    page = os.path.join(SITE, "index.html")

    with ThreadPoolExecutor(max_workers=24) as ex:
        feed_jobs = ex.map(fetch_feed, FEEDS)
        quote_jobs = ex.map(yahoo_quote, YAHOO)
        crypto_job = ex.submit(crypto_data)
        items = [i for batch in feed_jobs for i in batch]
        quotes = {q["symbol"]: q for q in quote_jobs if q}
        coins, glob, fng = crypto_job.result()

    stories = build_stories(items, now)
    if not stories:
        log("기사를 가져오지 못해 이전 호를 유지합니다.")
        if "--no-alert" not in args:
            notify(NAME, "업데이트 실패 · Update failed", "오늘 밤 뉴스를 가져오지 못했습니다. 인터넷 연결을 확인하세요.", False, page)
        return 1

    if not PUBLIC:
        enrich_summaries(stories)
    brief, brief_en = market_brief(quotes, coins, fng), market_brief_en(quotes, coins, fng)
    watch_html = ""
    if not PUBLIC:  # 공개판에는 개인 관심종목 표를 싣지 않습니다
        try:
            import stockwatch
            # 표만 만들고 관심종목 알림은 보내지 않습니다(밤 9시에는 신문 알림 하나만)
            watch_html = stockwatch.section_html(stockwatch.check(alert=False, force=True) or [], stories)
        except Exception as e:
            log("관심종목 체크 실패: %s" % e)

    stamp = now_local.strftime("%Y-%m-%d")
    existing = sorted((f for f in os.listdir(ARCHIVE) if f.endswith(".html") and f != stamp + ".html"), reverse=True)
    issue = len(existing) + 1

    with open(page, "w", encoding="utf-8") as fh:
        fh.write(render(now_local, stories, quotes, coins, glob, fng, brief, brief_en, issue,
                        [(f[:-5], "archive/" + f) for f in existing], watch_html))
    with open(os.path.join(ARCHIVE, stamp + ".html"), "w", encoding="utf-8") as fh:
        fh.write(render(now_local, stories, quotes, coins, glob, fng, brief, brief_en, issue,
                        [("최신 호 Latest", "../index.html")] + [(f[:-5], f) for f in existing], watch_html))
    if PUBLIC:
        write_seo_files(existing, stamp)
        with open(TG_MESSAGE, "w", encoding="utf-8") as fh:
            fh.write(telegram_message(now_local, issue, brief, stories))
    log("생성 완료 %s (기사 %d건: 한국어 %d · English %d, 시세 %d개, 코인 %d개)" % (
        page, len(stories), sum(s["ko"] for s in stories), sum(not s["ko"] for s in stories), len(quotes), len(coins)))

    if "--open" in args:
        subprocess.run(["open", page], check=False)
    if "--no-alert" not in args:
        ks, spx = quotes.get("^KS11"), quotes.get("^GSPC")
        btc = next((c for c in coins if c["symbol"] == "BTC"), None)
        sub = " · ".join(filter(None, [
            "KOSPI %+.2f%%" % ks["change"] if ks else "",
            "S&P %+.2f%%" % spx["change"] if spx else "",
            "BTC %+.2f%%" % btc["change"] if btc else ""]))
        notify("%s · %s" % (NAME, NAME_EN), sub or "새 호 New edition", stories[0]["title"],
               "--no-dialog" not in args, page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
