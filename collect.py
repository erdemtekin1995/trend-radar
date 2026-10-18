#!/usr/bin/env python3
"""
Trend Radar — toplayıcı.

Her çalıştığında:
  * Google Trends "Trending now" RSS feed'ini config.json'daki ülkeler için çeker (US, DE, TR, KR)
  * Çin için Baidu 热搜 (gerçek zamanlı sıcak aramalar) listesini çeker
    (Google Çin'de çalışmadığı için Google Trends'in CN verisi yok)
  * Sonuçları data/snapshots/YYYY-MM-DD.jsonl dosyasına satır satır ekler

Ek bağımlılık gerektirmez (sadece Python 3.9+ standart kütüphane).

Kullanım:
  python collect.py                 # canlı veriyi çek
  python collect.py --fixtures      # fixtures/ klasöründeki örnek dosyalarla çalış (offline test)
  python collect.py --dry-run       # sadece ekrana yaz, dosyaya kaydetme
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SNAP_DIR = ROOT / "data" / "snapshots"
FIXTURES = ROOT / "fixtures"
HT = "{https://trends.google.com/trending/rss}"

GOOGLE_RSS = "https://trends.google.com/trending/rss?geo={geo}"
BAIDU_API = "https://top.baidu.com/api/board?platform=wise&tab=realtime"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def http_get(url: str, retries: int = 3, timeout: int = 20) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # ağ hatası, 429 vb.
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url} alınamadı: {last}")


def parse_traffic(label: str | None) -> int | None:
    """'2000+' -> 2000, '500K+' -> 500000, '1M+' -> 1000000"""
    if not label:
        return None
    m = re.match(r"\s*([\d.,]+)\s*([KkMmBb])?", label)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((m.group(2) or "").lower(), 1)
    return int(num * mult)


def _text(el, tag):
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def parse_google_rss(xml_text: str, geo: str, ts: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for rank, it in enumerate(root.iter("item"), start=1):
        term = _text(it, "title")
        if not term:
            continue
        label = _text(it, f"{HT}approx_traffic")
        started = None
        pub = _text(it, "pubDate")
        if pub:
            try:
                started = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
            except Exception:
                started = None
        news = []
        for n in it.findall(f"{HT}news_item"):
            title = _text(n, f"{HT}news_item_title")
            url = _text(n, f"{HT}news_item_url")
            if title and url:
                news.append({"title": title, "url": url, "source": _text(n, f"{HT}news_item_source")})
        items.append({
            "ts": ts,
            "source": "google",
            "geo": geo,
            "rank": rank,
            "term": term,
            "traffic_label": label,
            "traffic": parse_traffic(label),
            "started": started,
            "picture": _text(it, f"{HT}picture"),
            "news": news,
            "url": f"https://trends.google.com/trends/explore?q={urllib.parse.quote(term)}&geo={geo}&date=now%207-d",
        })
    return items


def parse_baidu(json_text: str, ts: str, skip_pinned: bool = True) -> list[dict]:
    data = json.loads(json_text)
    out = []
    try:
        cards = data["data"]["cards"]
    except (KeyError, TypeError):
        return out
    for card in cards:
        for block in card.get("content", []):
            # Liste bazen bir seviye daha iç içe geliyor
            entries = block.get("content") if isinstance(block.get("content"), list) else [block]
            for e in entries:
                word = e.get("word") or e.get("query")
                if not word:
                    continue
                pinned = bool(e.get("isTop"))
                if pinned and skip_pinned:
                    continue
                score = e.get("hotScore")
                try:
                    score = int(score) if score is not None else None
                except (TypeError, ValueError):
                    score = None
                out.append({
                    "ts": ts,
                    "source": "baidu",
                    "geo": "CN",
                    "rank": e.get("index", len(out) + 1),
                    "term": word,
                    "traffic_label": e.get("labelTagName") or e.get("newHotName"),
                    "traffic": score,  # wise platformunda genelde yok; rank kullanılır
                    "started": None,
                    "picture": e.get("img"),
                    "news": [{"title": e["desc"], "url": e.get("url"), "source": "Baidu"}] if e.get("desc") else [],
                    "url": e.get("url"),
                })
    return out


def collect(cfg: dict, use_fixtures: bool) -> tuple[list[dict], list[str]]:
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows, errors = [], []

    for geo in cfg.get("google_geos", []):
        try:
            if use_fixtures:
                fx = FIXTURES / f"google_{geo}.xml"
                if not fx.exists():
                    fx = FIXTURES / "google_TR.xml"
                xml_text = fx.read_text(encoding="utf-8")
            else:
                xml_text = http_get(GOOGLE_RSS.format(geo=geo))
            got = parse_google_rss(xml_text, geo, ts)
            rows += got
            print(f"[google:{geo}] {len(got)} trend")
        except Exception as e:
            errors.append(f"google:{geo}: {e}")
            print(f"[google:{geo}] HATA: {e}", file=sys.stderr)

    if cfg.get("baidu_china", True):
        try:
            if use_fixtures:
                js = (FIXTURES / "baidu.json").read_text(encoding="utf-8")
            else:
                js = http_get(BAIDU_API)
            got = parse_baidu(js, ts, cfg.get("baidu_skip_pinned", True))
            rows += got
            print(f"[baidu:CN] {len(got)} trend")
        except Exception as e:
            errors.append(f"baidu:CN: {e}")
            print(f"[baidu:CN] HATA: {e}", file=sys.stderr)

    return rows, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", action="store_true", help="fixtures/ dosyalarıyla offline çalış")
    ap.add_argument("--dry-run", action="store_true", help="kaydetmeden ekrana yaz")
    args = ap.parse_args()

    cfg = load_config()
    rows, errors = collect(cfg, args.fixtures)

    if args.dry_run:
        for r in rows:
            print(f"{r['geo']}\t#{r['rank']}\t{r['traffic_label'] or '-'}\t{r['term']}")
        return

    if rows:
        tz = ZoneInfo(cfg.get("timezone", "UTC"))
        day = datetime.now(tz).strftime("%Y-%m-%d")
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAP_DIR / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{len(rows)} satır -> {path.relative_to(ROOT)}")

    # Tüm kaynaklar başarısız olduysa workflow kırmızı görünsün
    if not rows and errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
