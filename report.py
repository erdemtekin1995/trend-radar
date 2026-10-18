#!/usr/bin/env python3
"""
Trend Radar — günlük rapor.

Bir günün saatlik snapshot'larını birleştirir ve şunları üretir:
  * reports/YYYY-MM-DD.md      okunabilir günlük rapor
  * docs/data/YYYY-MM-DD.json  dashboard verisi
  * docs/data/index.json       dashboard'un gün listesi

ANTHROPIC_API_KEY ortam değişkeni varsa Claude'dan o günün trendlerine göre
uygulama fikirleri ve video içerik fikirleri ister. Yoksa bu adımı atlar.

Kullanım:
  python report.py                  # dün (config'teki saat dilimine göre)
  python report.py --date today
  python report.py --date 2026-10-18
  python report.py --no-ai
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SNAP_DIR = ROOT / "data" / "snapshots"
REPORT_DIR = ROOT / "reports"
DOCS_DATA = ROOT / "docs" / "data"

COUNTRY_NAMES = {
    "US": "ABD", "DE": "Almanya", "TR": "Türkiye", "KR": "Güney Kore", "CN": "Çin (Baidu)",
}
FLAGS = {"US": "🇺🇸", "DE": "🇩🇪", "TR": "🇹🇷", "KR": "🇰🇷", "CN": "🇨🇳"}


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def norm(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip().casefold())


def read_day(day: str) -> list[dict]:
    p = SNAP_DIR / f"{day}.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def aggregate(rows: list[dict]) -> dict[str, list[dict]]:
    """geo -> trend listesi (tekilleştirilmiş, sıralı)"""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["geo"], norm(r["term"]))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "term": r["term"], "geo": r["geo"], "source": r["source"],
                "first_seen": r["ts"], "last_seen": r["ts"], "snapshots": set(),
                "traffic": r.get("traffic"), "traffic_label": r.get("traffic_label"),
                "best_rank": r.get("rank") or 999, "news": [], "url": r.get("url"),
                "picture": r.get("picture"),
            }
        g["snapshots"].add(r["ts"])
        g["first_seen"] = min(g["first_seen"], r["ts"])
        g["last_seen"] = max(g["last_seen"], r["ts"])
        t = r.get("traffic")
        if t is not None and (g["traffic"] is None or t > g["traffic"]):
            g["traffic"], g["traffic_label"] = t, r.get("traffic_label")
        if r.get("rank"):
            g["best_rank"] = min(g["best_rank"], r["rank"])
        seen_urls = {n["url"] for n in g["news"]}
        for n in r.get("news", []):
            if n.get("url") and n["url"] not in seen_urls and len(g["news"]) < 3:
                g["news"].append(n)
                seen_urls.add(n["url"])
        if not g.get("picture") and r.get("picture"):
            g["picture"] = r["picture"]

    by_geo: dict[str, list[dict]] = defaultdict(list)
    for g in groups.values():
        g["hours"] = len(g.pop("snapshots"))
        by_geo[g["geo"]].append(g)

    for geo, items in by_geo.items():
        if geo == "CN" or all(i["traffic"] is None for i in items):
            # Baidu: hacim yok -> en iyi sıra, sonra listede kalma süresi
            items.sort(key=lambda i: (i["best_rank"], -i["hours"]))
        else:
            items.sort(key=lambda i: (-(i["traffic"] or 0), -i["hours"], i["best_rank"]))
    return dict(by_geo)


def history(day: str, days_back: int = 7) -> dict[str, dict[str, int]]:
    """geo -> {normalize terim: kaç farklı günde görüldü} (bugün hariç, son N gün)"""
    d0 = datetime.strptime(day, "%Y-%m-%d")
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for k in range(1, days_back + 1):
        d = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
        seen = {(r["geo"], norm(r["term"])) for r in read_day(d)}
        for geo, t in seen:
            out[geo][t] += 1
    return out


def mark_new_and_recurring(by_geo: dict, hist: dict) -> None:
    for geo, items in by_geo.items():
        h = hist.get(geo, {})
        for i in items:
            prev = h.get(norm(i["term"]), 0)
            i["is_new"] = prev == 0
            i["days_seen_7d"] = prev + 1


# ---------------------------------------------------------------- AI

def build_prompt(cfg: dict, day: str, by_geo: dict) -> str:
    n = cfg.get("ai_top_n_per_country", 12)
    lines = []
    for geo in list(cfg.get("google_geos", [])) + (["CN"] if cfg.get("baidu_china") else []):
        items = by_geo.get(geo, [])[:n]
        if not items:
            continue
        lines.append(f"\n## {geo} — {COUNTRY_NAMES.get(geo, geo)}")
        for i in items:
            if geo == "CN":
                vol = f"Baidu sıra #{i['best_rank']}" + (f" ({i['traffic_label']})" if i["traffic_label"] else "")
            else:
                vol = i["traffic_label"] or "?"
            flags = []
            if i["is_new"]:
                flags.append("YENİ")
            if i["days_seen_7d"] >= 3:
                flags.append(f"son 7 günün {i['days_seen_7d']}'ünde")
            news = i["news"][0]["title"] if i["news"] else ""
            lines.append(
                f"- {i['term']} | {vol} | {i['hours']} saat listede"
                + (f" | {', '.join(flags)}" if flags else "")
                + (f" | haber: {news[:140]}" if news else "")
            )
    data_block = "\n".join(lines)

    return f"""Aşağıda {day} tarihinde 5 pazarda öne çıkan arama trendleri var. ABD, Almanya, Türkiye ve Kore verisi Google Trends "Trending now" listesinden; Çin verisi Baidu 热搜 listesinden (Google Çin'de çalışmadığı için).

Okuyucu bir mobil uygulama geliştiricisi ve içerik üreticisi. Amaç: {cfg.get('ai_focus')}

Kurallar:
- Tek seferlik haber olaylarını (maç sonucu, piyango çekilişi) doğrudan uygulama fikrine çevirme; arkalarındaki TEKRAR EDEN ihtiyacı bul (ör. "X ne zaman" aramaları -> takvim/hatırlatma ihtiyacı). Tek seferlik olaylar video fikri için daha uygundur.
- Birden fazla ülkede görülen ya da son günlerde tekrar eden konulara ağırlık ver.
- Ölüm, afet, savaş, suç mağdurları gibi trajedileri sansasyonel video malzemesine çevirme; bu konulardan ancak faydalı/bilgilendirici bir açı varsa bahset.
- Somut ol: genel "AI asistan uygulaması" gibi fikirler verme.
- Tüm metinler {cfg.get('ai_language', 'Türkçe')} olsun.

Trend verisi:
{data_block}

SADECE şu yapıda geçerli bir JSON döndür, başka hiçbir şey yazma:
{{
  "summary": "günün genel tablosu, 2-3 cümle",
  "countries": {{"US": "1-2 cümle", "DE": "...", "TR": "...", "KR": "...", "CN": "..."}},
  "patterns": ["ülkeler arası veya tekrar eden ihtiyaç kalıbı", "..."],
  "app_ideas": [
    {{"title": "kısa isim", "problem": "hangi ihtiyacı çözüyor", "why_now": "hangi trend(ler) bunu gösteriyor", "trends": ["terim1", "terim2"], "market": "TR|US|DE|KR|CN|global", "mvp": "ilk sürümde olacak 2-3 özellik", "effort": "kolay|orta|zor"}}
  ],
  "video_ideas": [
    {{"title": "video başlığı", "format": "Shorts/TikTok/uzun video vb.", "hook": "ilk 3 saniyede söylenecek cümle", "trends": ["terim"], "market": "TR|US|DE|KR|CN|global", "timing": "bugün|bu hafta|evergreen"}}
  ]
}}
4-6 app fikri ve 6-10 video fikri ver."""


AI_ERROR: str | None = None  # dashboard'da gösterilir


def ask_claude(cfg: dict, prompt: str) -> dict | None:
    global AI_ERROR
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY yok, AI fikir adımı atlandı.")
        AI_ERROR = "ANTHROPIC_API_KEY secret'ı bulunamadı."
        return None
    body = json.dumps({
        "model": os.environ.get("CLAUDE_MODEL") or cfg.get("ai_model", "claude-haiku-5-5"),
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # Workspace'e bağlı olmayan anahtarlar için hangi workspace kullanılacağı
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if ws:
        headers["anthropic-workspace-id"] = ws
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            err = json.loads(e.read().decode("utf-8"))
            detail = (err.get("error") or {}).get("message", "")
        except Exception:
            pass
        AI_ERROR = f"Claude API hatası {e.code}: {detail or e.reason}"
        print(AI_ERROR, file=sys.stderr)
        return None
    except Exception as e:
        AI_ERROR = f"Claude isteği başarısız: {e}"
        print(AI_ERROR, file=sys.stderr)
        return None
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    parsed = parse_json_loose(text)
    if parsed is None:
        AI_ERROR = "Claude cevap verdi ama cevap JSON olarak okunamadı."
    return parsed


def parse_json_loose(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"AI cevabı JSON olarak okunamadı: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- çıktı

def fmt_vol(i: dict) -> str:
    if i["geo"] == "CN":
        return f"#{i['best_rank']}"
    return i["traffic_label"] or "-"


def write_markdown(cfg: dict, day: str, by_geo: dict, ai: dict | None) -> Path:
    top = cfg.get("report_top_n", 20)
    out = [f"# Trend Radar — {day}\n"]
    if ai:
        out.append(f"> {ai.get('summary', '')}\n")
        if ai.get("patterns"):
            out.append("## Kalıplar\n")
            out += [f"- {p}" for p in ai["patterns"]]
            out.append("")
        if ai.get("app_ideas"):
            out.append("## Uygulama fikirleri\n")
            for a in ai["app_ideas"]:
                out.append(f"### {a.get('title')}  `{a.get('market', '')}` · {a.get('effort', '')}")
                out.append(f"- **Problem:** {a.get('problem', '')}")
                out.append(f"- **Neden şimdi:** {a.get('why_now', '')}")
                out.append(f"- **MVP:** {a.get('mvp', '')}")
                if a.get("trends"):
                    out.append(f"- **Trendler:** {', '.join(a['trends'])}")
                out.append("")
        if ai.get("video_ideas"):
            out.append("## Video fikirleri\n")
            out.append("| Başlık | Format | Hook | Pazar | Zamanlama |")
            out.append("|---|---|---|---|---|")
            for v in ai["video_ideas"]:
                cells = [v.get(k, "") for k in ("title", "format", "hook", "market", "timing")]
                out.append("| " + " | ".join(str(c).replace("|", "/") for c in cells) + " |")
            out.append("")

    for geo, items in by_geo.items():
        out.append(f"## {FLAGS.get(geo, '')} {COUNTRY_NAMES.get(geo, geo)}\n")
        if ai and ai.get("countries", {}).get(geo):
            out.append(f"_{ai['countries'][geo]}_\n")
        out.append("| # | Arama | Hacim | Saat | Not |")
        out.append("|---|---|---|---|---|")
        for n, i in enumerate(items[:top], 1):
            note = "🆕" if i["is_new"] else (f"🔁 {i['days_seen_7d']}/7 gün" if i["days_seen_7d"] >= 3 else "")
            term = f"[{i['term']}]({i['url']})" if i.get("url") else i["term"]
            out.append(f"| {n} | {term} | {fmt_vol(i)} | {i['hours']} | {note} |")
        out.append("")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    p = REPORT_DIR / f"{day}.md"
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def write_json(cfg: dict, day: str, by_geo: dict, ai: dict | None, n_snap: int) -> Path:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    order = list(cfg.get("google_geos", [])) + (["CN"] if cfg.get("baidu_china") else [])
    payload = {
        "date": day,
        "generated_at": datetime.now(ZoneInfo(cfg.get("timezone", "UTC"))).isoformat(timespec="minutes"),
        "snapshots": n_snap,
        "countries": [
            {"geo": g, "name": COUNTRY_NAMES.get(g, g), "flag": FLAGS.get(g, ""),
             "trends": by_geo.get(g, [])[: max(cfg.get("report_top_n", 20), 30)]}
            for g in order
        ],
        "ai": ai,
        "ai_error": None if ai else AI_ERROR,
    }
    p = DOCS_DATA / f"{day}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    idx_p = DOCS_DATA / "index.json"
    days = []
    if idx_p.exists():
        try:
            days = json.loads(idx_p.read_text(encoding="utf-8")).get("days", [])
        except json.JSONDecodeError:
            days = []
    if day not in days:
        days.append(day)
    days.sort(reverse=True)
    idx_p.write_text(json.dumps({"days": days}, ensure_ascii=False), encoding="utf-8")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="yesterday", help="YYYY-MM-DD, 'today' veya 'yesterday'")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--print-prompt", action="store_true", help="AI'ya gidecek metni göster")
    args = ap.parse_args()

    cfg = load_config()
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    now = datetime.now(tz)
    if args.date == "today":
        day = now.strftime("%Y-%m-%d")
    elif args.date == "yesterday":
        day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        day = args.date

    rows = read_day(day)
    if not rows:
        print(f"{day} için veri yok ({SNAP_DIR / (day + '.jsonl')}).")
        sys.exit(0)

    by_geo = aggregate(rows)
    mark_new_and_recurring(by_geo, history(day))
    n_snap = len({r["ts"] for r in rows})

    ai = None
    prompt = build_prompt(cfg, day, by_geo)
    if args.print_prompt:
        print(prompt)
    if not args.no_ai:
        ai = ask_claude(cfg, prompt)

    md = write_markdown(cfg, day, by_geo, ai)
    js = write_json(cfg, day, by_geo, ai, n_snap)
    print(f"Rapor: {md.relative_to(ROOT)}  |  Dashboard verisi: {js.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
