#!/usr/bin/env python3
"""Fluent Ops — günlük haber özetleri (Çeviriden Öğren).

GitHub Actions her sabah çalıştırır. Adımlar:
1) Birkaç güvenilir kaynaktan (Google News, BBC) günün başlıklarını RSS ile toplar.
2) Her alan için Gemini'ye (ücretsiz anahtar) en önemli 2 haberi seçtirir, Google Arama ile
   doğrulatıp ÖZGÜN bir özet yazdırır: İngilizce + doğal Türkçe, renk eşlemeli parçalar.
3) Kuralları denetler (geçmeyenler bir kez düzeltilir, yine geçmezse atlanır).
4) news/voa.json dosyasına ekler (son 45 gün saklanır). Uygulama bu dosyayı açılışta okur.

Yalnızca Python standart kütüphanesi kullanır. Gerekli ortam değişkeni: GEMINI_API_KEY
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(ROOT, "news", "voa.json")
KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODELS = [m for m in [os.environ.get("GEMINI_MODEL", "").strip(), "gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"] if m]
KEEP_DAYS = 45
PER_AREA = int(os.environ.get("NEWS_PER_AREA", "2"))
UA = {"User-Agent": "Mozilla/5.0 (FluentOps daily news; +https://github.com)"}


def gn(q=None, lang="en", topic=None):
    loc = {"en": "hl=en-US&gl=US&ceid=US:en", "tr": "hl=tr&gl=TR&ceid=TR:tr"}[lang]
    if q:
        return "https://news.google.com/rss/search?q=%s+when:1d&%s" % (urllib.parse.quote(q), loc)
    if topic:
        return "https://news.google.com/rss/headlines/section/topic/%s?%s" % (topic, loc)
    return "https://news.google.com/rss?" + loc


AREAS = [
    {"code": "GUN", "cat": "Gündem", "focus": "the most important world and Türkiye news of the day (politics, diplomacy, climate summits, major scandals, trending people)",
     "feeds": [gn(lang="tr"), gn(lang="en"), "https://feeds.bbci.co.uk/news/world/rss.xml", "https://feeds.bbci.co.uk/turkce/rss.xml"]},
    {"code": "EKO", "cat": "Ekonomi", "focus": "economy and markets (central banks, interest rates, inflation, currencies, energy prices, big companies), with priority to news that matters for Türkiye",
     "feeds": [gn(topic="BUSINESS", lang="en"), gn(topic="BUSINESS", lang="tr"), gn("ekonomi OR borsa OR faiz OR enflasyon", "tr")]},
    {"code": "TEK", "cat": "Teknoloji & Otomotiv", "focus": "technology and the automotive industry (electric vehicles, Togg, carmakers, batteries, AI, engineering)",
     "feeds": [gn(topic="TECHNOLOGY", lang="en"), gn("electric vehicle OR automaker OR automotive industry", "en"), gn("Togg OR otomotiv OR elektrikli araç", "tr")]},
    {"code": "FUT", "cat": "Futbol", "focus": "football: Galatasaray first, then the Turkish Süper Lig, the Champions League and the Premier League",
     "feeds": [gn("Galatasaray", "tr"), gn("Süper Lig OR Şampiyonlar Ligi", "tr"), "https://feeds.bbci.co.uk/sport/football/rss.xml"]},
]

SPEC = r"""
Write each story as an ORIGINAL news summary in your own words (never copy sentences from any source).
Level: B1–B2 learners. 13–18 sentences. Clear, neutral, factual. Explain background briefly so a learner understands why it matters.
Facts: use only facts confirmed by the headlines or your search. No invented numbers, quotes or names. Attribute claims ("according to ...").

COLOUR ALIGNMENT (most important):
Every sentence is written twice, Turkish and English, split into numbered meaning chunks written as [N|text].
- The same number marks the same meaning in both languages; word order may differ.
- Chunks are phrases or single words (subject, verb group, object, time/place adverbial, linking word). Usually 3–8 chunks per sentence.
- EVERY word must be inside a chunk. Only spaces and punctuation (. , ; : ! ? " ' ( ) –) may appear outside brackets.
- The SET of numbers used must be IDENTICAL in the Turkish and English sentence. A number may repeat for split parts.
- Numbers start at 1 in each sentence. Never put [ or ] inside chunk text.
Example:
{"tr":"[1Dün] [2Merkez Bankası] [3faizi] [4yüzde 2] [5düşürdü].","en":"[1Yesterday], [2the Central Bank] [5cut] [3interest rates] [4by 2 percentage points]."}
(Written with the bar: "[1|Dün] [2|Merkez Bankası] ..." — ALWAYS use the bar form [N|text].)

NATURAL TURKISH: write Turkish as a native journalist would — verb at the end, time/place near the beginning, modifiers before nouns.
Do NOT copy English word order; the numbers handle reordering. Check vowel harmony (de/da, ki, mi/mı/mu/mü, -ler/-lar).

Return ONLY a JSON array (no prose, no markdown fences). Each element:
{"title":"English headline","titleTr":"Türkçe başlık","sources":["BBC","Reuters"],"url":"one link copied exactly from the list","s":[{"tr":"...","en":"..."}, ...]}
"""

CH = re.compile(r"\[(\d+)\|?([^\[\]]*)\]")
OUTSIDE_OK = re.compile(r"^[\s.,;:!?\"'“”‘’()\-–—…]*$")


def side(s):
    if not isinstance(s, str) or not s.strip():
        return None, "empty"
    ms = list(CH.finditer(s))
    if not ms:
        return None, "no chunks"
    rest = CH.sub("", s)
    if not OUTSIDE_OK.match(rest):
        return None, "text outside chunks: %r" % rest.strip()[:40]
    if any(not m.group(2).strip() for m in ms):
        return None, "empty chunk"
    return set(int(m.group(1)) for m in ms), None


def check(a):
    errs = []
    for k in ("title", "titleTr", "s"):
        if not a.get(k):
            errs.append("missing " + k)
    if errs:
        return errs
    n = len(a["s"])
    if not 12 <= n <= 20:
        errs.append("needs 13-18 sentences, got %d" % n)
    for j, x in enumerate(a["s"]):
        t, e1 = side(x.get("tr"))
        e, e2 = side(x.get("en"))
        if e1 or e2:
            errs.append("sentence %d: %s" % (j + 1, e1 or e2))
        elif t != e:
            errs.append("sentence %d: number sets differ tr%s en%s" % (j + 1, sorted(t), sorted(e)))
    return errs


def fetch(url, data=None, headers=None, timeout=40):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def headlines(area):
    out, seen = [], set()
    for url in area["feeds"]:
        try:
            root = ET.fromstring(fetch(url, timeout=25))
        except Exception as ex:
            print("  feed failed:", url[:80], ex)
            continue
        for it in root.iter("item"):
            title = strip_tags(it.findtext("title"))
            src = strip_tags(it.findtext("source")) or ("BBC" if "bbc" in url else "")
            link = (it.findtext("link") or "").strip()
            date = (it.findtext("pubDate") or "").strip()
            desc = strip_tags(it.findtext("description"))[:240]
            key = re.sub(r"\W+", "", title.lower())[:60]
            if not title or key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "source": src, "link": link, "date": date, "desc": desc})
            if len(out) >= 40:
                break
    return out[:40]


def gemini(prompt, search=True):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4}}
    if search:
        body["tools"] = [{"google_search": {}}]
    last = None
    for model in MODELS:
        for attempt in range(2):
            try:
                raw = fetch("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                            data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": KEY}, timeout=180)
                j = json.loads(raw)
                parts = j["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts)
            except urllib.error.HTTPError as ex:
                last = "%s HTTP %s %s" % (model, ex.code, ex.read()[:300])
                if ex.code == 429:
                    time.sleep(35)
                    continue
                if ex.code == 400 and "tools" in body:  # arama aracı desteklenmiyorsa aramasız dene
                    body.pop("tools")
                    continue
                break
            except Exception as ex:
                last = "%s %s" % (model, ex)
                time.sleep(5)
    raise RuntimeError(last)


def parse_json(text):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, k = t.find("["), t.rfind("]")
    if i < 0 or k < 0:
        raise ValueError("no JSON array")
    return json.loads(t[i:k + 1])


def main():
    if not KEY:
        sys.exit("GEMINI_API_KEY yok — GitHub > Settings > Secrets and variables > Actions altına ekleyin.")
    now = dt.datetime.utcnow() + dt.timedelta(hours=3)
    today = now.strftime("%Y-%m-%d")
    feed = {"items": []}
    if os.path.exists(FEED):
        feed = json.load(open(FEED, encoding="utf-8"))
    items = feed.get("items", [])
    recent = [x["title"] for x in items if x.get("date", "") >= (now - dt.timedelta(days=3)).strftime("%Y-%m-%d")]
    added = 0
    for area in AREAS:
        if any(x["id"].startswith("N-%s-%s" % (today.replace("-", ""), area["code"])) for x in items):
            print(area["cat"], "already done today"); continue
        hs = headlines(area)
        print(area["cat"], len(hs), "headlines")
        if len(hs) < 3:
            continue
        lst = "\n".join("%d. %s | %s | %s | %s\n   %s" % (i + 1, h["title"], h["source"], h["date"], h["link"], h["desc"]) for i, h in enumerate(hs))
        prompt = ("You prepare the daily news section of a Turkish–English learning app for an adult engineer in Türkiye.\n"
                  "Area: %s.\nToday's headlines from several outlets:\n%s\n\n"
                  "Choose the %d most important DIFFERENT stories of the last 24 hours for this area. Prefer stories reported by several outlets. "
                  "Skip these stories already covered: %s\n"
                  "Use Google Search to confirm the key facts, then write each story.\n%s") % (area["focus"], lst, PER_AREA, json.dumps(recent, ensure_ascii=False), SPEC)
        try:
            arts = parse_json(gemini(prompt))
        except Exception as ex:
            print("  generation failed:", ex); continue
        n = 0
        for a in arts[:PER_AREA]:
            errs = check(a)
            if errs:
                fix = "Fix this JSON article so it follows every rule. Problems found:\n- %s\n\nRules:%s\n\nArticle:\n%s\nReturn a JSON array with the one fixed article." % ("\n- ".join(errs[:12]), SPEC, json.dumps(a, ensure_ascii=False))
                try:
                    time.sleep(7)
                    a = parse_json(gemini(fix, search=False))[0]
                    errs = check(a)
                except Exception as ex:
                    errs = [str(ex)]
            if errs:
                print("  skipped:", a.get("title"), errs[:3]); continue
            n += 1
            srcs = [s for s in a.get("sources", []) if isinstance(s, str)][:4]
            url = a.get("url") if isinstance(a.get("url"), str) and a.get("url", "").startswith("http") else (hs[0]["link"])
            items.append({"id": "N-%s-%s-%d" % (today.replace("-", ""), area["code"], n), "date": today, "level": 2, "cat": area["cat"],
                          "title": a["title"].strip(), "titleTr": a["titleTr"].strip(), "by": ", ".join(srcs) or "Çeşitli kaynaklar",
                          "src": url, "note": "özgün özet", "s": [{"tr": x["tr"].strip(), "en": x["en"].strip()} for x in a["s"]]})
            recent.append(a["title"])
            added += 1
            print("  +", a["title"])
        time.sleep(7)
    cut = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    items = [x for x in items if x.get("date", "") >= cut]
    items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    os.makedirs(os.path.dirname(FEED), exist_ok=True)
    json.dump({"updated": today, "items": items}, open(FEED, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("added", added, "total", len(items))


if __name__ == "__main__":
    main()
