"""Free internet tools: search (Bing RSS, DuckDuckGo, Wikipedia), page reading, weather, currency."""

import html
import json
import re
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def get(url, timeout=12, headers=None):
    h = {"User-Agent": UA, "Accept-Language": "ar,en;q=0.8"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        raw = r.read(3_000_000)
        m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""))
        return raw.decode(m.group(1) if m else "utf-8", "replace")


def q(s):
    return urllib.parse.quote_plus(s)


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def is_arabic(s):
    return bool(re.search(r"[؀-ۿ]", s))


def search(query, n=6):
    """[{title, url, snippet}] from the first engine that answers."""
    for fn in (_bing, _duckduckgo, _wikipedia_search):
        try:
            r = fn(query)
            if r:
                return r[:n]
        except Exception:  # noqa: BLE001 - try the next engine
            continue
    return []


def _bing(query):
    xml = get("https://www.bing.com/search?format=rss&count=10&q=" + q(query))
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(r"<%s>(.*?)</%s>" % (name, name), item, re.S)
            return strip_tags(m.group(1)) if m else ""
        if tag("link"):
            out.append({"title": tag("title"), "url": tag("link"), "snippet": tag("description")})
    return out


def _duckduckgo(query):
    page = get("https://html.duckduckgo.com/html/?q=" + q(query))
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', page, re.S):
        href = html.unescape(m.group(1))
        u = re.search(r"uddg=([^&]+)", href)
        if u:
            href = urllib.parse.unquote(u.group(1))
        if href.startswith("//"):
            href = "https:" + href
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        out.append({"title": strip_tags(m.group(2)), "url": href, "snippet": strip_tags(sn.group(1)) if sn else ""})
    return out


def _wikipedia_search(query):
    lang = "ar" if is_arabic(query) else "en"
    j = json.loads(get("https://%s.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5&srsearch=%s"
                       % (lang, q(query))))
    return [{"title": s["title"], "url": "https://%s.wikipedia.org/wiki/%s" % (lang, q(s["title"].replace(" ", "_"))),
             "snippet": strip_tags(s.get("snippet", ""))} for s in j.get("query", {}).get("search", [])]


def html_to_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    return "\n".join(l for l in lines if len(l) > 1)


def relevant(text, focus, max_chars=4000):
    """The paragraphs that share the most words with `focus`, in page order."""
    if len(text) <= max_chars or not focus:
        return text[:max_chars]
    words = {w for w in re.findall(r"\w{3,}", focus.lower())}
    paras = [p for p in text.split("\n") if len(p) > 40]
    scored = sorted(range(len(paras)), key=lambda i: -sum(w in paras[i].lower() for w in words))
    keep, size = set(), 0
    for i in scored:
        if size + len(paras[i]) > max_chars:
            continue
        keep.add(i)
        size += len(paras[i])
    return "\n".join(paras[i] for i in sorted(keep))


def read(url, focus="", max_chars=4000):
    return relevant(html_to_text(get(url)), focus, max_chars)


def weather(city):
    g = json.loads(get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=%s&name=%s"
                       % ("ar" if is_arabic(city) else "en", q(city))))
    if not g.get("results"):
        return "لم أجد مدينة باسم " + city
    p = g["results"][0]
    w = json.loads(get("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f&timezone=auto&forecast_days=3"
                       "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
                       "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max" % (p["latitude"], p["longitude"])))
    c, d = w["current"], w["daily"]
    days = "; ".join("%s: %s..%s°C, rain %s%%" % (d["time"][i], d["temperature_2m_min"][i], d["temperature_2m_max"][i],
                                                    d["precipitation_probability_max"][i]) for i in range(len(d["time"])))
    return "%s, %s: now %s°C, humidity %s%%, wind %s km/h. %s" % (
        p.get("name"), p.get("country", ""), c["temperature_2m"], c["relative_humidity_2m"], c["wind_speed_10m"], days)


def currency(amount, frm, to):
    j = json.loads(get("https://open.er-api.com/v6/latest/" + q(frm.upper())))
    rate = j["rates"].get(to.upper())
    if rate is None:
        return "عملة غير معروفة: " + to
    return "%s %s = %.4f %s (rate date %s)" % (amount, frm.upper(), float(amount) * rate, to.upper(),
                                               j.get("time_last_update_utc", ""))
