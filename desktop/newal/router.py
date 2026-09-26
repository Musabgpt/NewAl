"""The router: sends each request to the model made for it.

1. Obvious cases by pattern (code fences, tracebacks).
2. Nearest labelled examples by embedding (Qwen3-Embedding, ~0.15 s): 16/16 on our test set.
3. Without the embedding model: LFM2.5 1.2B decides (slower, ~4 s on a laptop CPU, as hybrid
   models cannot reuse the cached prompt, and less accurate: 4/10 against 16/16).
Requests the user rates 👍 become new examples, so routing keeps improving without training.
"""

import json
import math
import os
import re
import threading

from . import catalog, config
from .engine import pool

ROUTES = ("code", "tools", "analyze", "chat")
ROLE_OF = {"code": "coder", "tools": "agent", "analyze": "judge", "chat": "agent"}

EXAMPLES = {
    "code": ["اكتب دالة بايثون تعكس نص", "write a python script that renames files", "صلح هالكود ما عم يشتغل",
             "TypeError: 'NoneType' object is not subscriptable", "اعملي صفحة HTML فيها فورم تسجيل",
             "SQL query to get top 10 customers", "كيف اعمل API بـ FastAPI", "برمج لعبة snake بجافاسكربت",
             "اشرحلي هالكود", "regex لايميلات", "convert this function to TypeScript", "عندي error بالكود",
             "اكتب سكربت باورشل يحذف الملفات القديمة", "write unit tests for this class"],
    "tools": ["شو سعر الذهب اليوم", "ابحث عن آخر أخبار الذكاء الاصطناعي", "what's the weather in Damascus",
              "اعمل ملف notes.txt وحط فيه مهامي", "افتح مجلد المشروع", "شغل الأمر ipconfig", "ارفع الملف على درايف",
              "شو في بمستودعاتي على github", "نزل dataset من kaggle", "افتح المشروع بـ VS Code",
              "list the files in my workspace", "تذكر إني بحب القهوة", "كم الساعة هلق", "حول 100 دولار لليرة",
              "اقرألي هالرابط", "clone my repo from gitlab", "اعمل issue على github", "كم مساحة الهارد الفاضية"],
    "analyze": ["صغلي فكرتي كطلب واضح: تطبيق مذاكرة", "حوّل هالخطأ لبرومت واضح للمبرمج", "قارن بين React و Vue لمشروعي",
                "راجع هالجواب وقلي إذا صح", "حلل ليش فشل هالحل", "make this a better prompt: a logo for my shop",
                "اعطيني خطة لمشروع متجر الكتروني", "what are the pros and cons of microservices", "قيم هالفكرة بصراحة",
                "ليش البرنامج بطيء برأيك", "اكتبلي مواصفات مشروع تطبيق توصيل"],
    "chat": ["صباح الخير", "كيفك؟", "احكيلي نكتة", "شو هي الجاذبية", "what is photosynthesis", "مين هو ابن سينا",
             "شكرا كتير", "اكتبلي قصيدة عن الشام", "ترجملي هالجملة للانكليزي", "شو معنى كلمة serendipity",
             "what do you think about cats"],
}

PREFIX = "Instruct: Classify what kind of help the user asks for\nQuery: "
LEARNED = os.path.join(config.DATA, "router_learned.json")
CACHE = os.path.join(config.DATA, "router_vectors.json")

_CODE = re.compile(r"```|Traceback \(most recent call last\)|^\s*(def|class|import|function|const|public)\s", re.M)

SYSTEM = """Pick the route for the user's request. Reply with JSON only.
code = write/fix/explain code. tools = act on the computer or internet (search, files, terminal, GitHub, Drive, Kaggle, VS Code, memory).
analyze = plans, comparisons, reviews, turning ideas or errors into clear prompts. chat = everything else."""
SCHEMA = {"type": "object", "properties": {"route": {"type": "string", "enum": list(ROUTES)}}, "required": ["route"]}

_lock = threading.Lock()
_index = None      # [(route, text, unit vector)]


def _learned():
    try:
        with open(LEARNED, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _build():
    global _index
    items = [(r, t) for r, ts in EXAMPLES.items() for t in ts] + [tuple(x) for x in _learned()]
    model = catalog.MODELS["embed"]["file"]
    cached = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("model") == model:
            cached = data["vecs"]
    except (OSError, ValueError, KeyError):
        pass
    missing = [t for _, t in items if t not in cached]
    for i in range(0, len(missing), 16):
        batch = missing[i:i + 16]
        for t, v in zip(batch, pool.embed([PREFIX + t for t in batch])):
            cached[t] = _unit(v)
    if missing:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"model": model, "vecs": cached}, f, ensure_ascii=False)
    _index = [(r, t, cached[t]) for r, t in items]


def by_examples(text):
    """(route, confidence 0..1) from the 5 nearest examples."""
    with _lock:
        if _index is None:
            _build()
        index = _index
    q = _unit(pool.embed([PREFIX + text[:1000]])[0])
    near = sorted(((sum(a * b for a, b in zip(q, v)), r) for r, _, v in index), reverse=True)[:5]
    votes = {}
    for s, r in near:
        votes[r] = votes.get(r, 0) + s
    best = max(votes, key=votes.get)
    return best, votes[best] / (sum(votes.values()) or 1)


def by_model(text):
    r = pool.complete_json("router", [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": text[:1500]}], SCHEMA)
    return r.get("route") if r.get("route") in ROUTES else "chat"


def route(text):
    if _CODE.search(text):
        return "code"
    if catalog.available("embed"):
        try:
            # Measured: where the examples are unsure, LFM2.5 1.2B was wrong too, so they have the last word.
            return by_examples(text)[0]
        except Exception:  # noqa: BLE001 - fall through to the model
            pass
    if catalog.pick("router"):
        try:
            return by_model(text)
        except Exception:  # noqa: BLE001 - routing must never block an answer
            pass
    return "chat"


def learn(text, route_name):
    """Adds a request the user was happy with as a routing example."""
    global _index
    if route_name not in ROUTES or not text.strip():
        return
    items = _learned()
    if [route_name, text[:300]] in items:
        return
    items.append([route_name, text[:300]])
    with open(LEARNED, "w", encoding="utf-8") as f:
        json.dump(items[-500:], f, ensure_ascii=False)
    with _lock:
        _index = None
