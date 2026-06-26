"""Shared helpers used by all three pipelines."""

import os, json, datetime as dt
import requests
import config as C

# ---------------------------- LLM provider ----------------------------

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
APIFY_TOKEN       = os.environ.get("APIFY_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATE_DIR = "state"

# map the model roles in config.py to each provider's model names
GEMINI_MODELS = {
    "claude-sonnet-4-6": "gemini-2.5-pro",   # planner / filter equivalent
}

def llm(messages, model, max_tokens=2000, want_search=False):
    """Single entry point used by all pipelines. Returns plain text.
    messages is a list of {"role","content"} with string content."""
    if PROVIDER == "gemini":
        return _gemini(messages, model, max_tokens, want_search)
    return _anthropic(messages, model, max_tokens, want_search)


def _anthropic(messages, model, max_tokens, want_search):
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if want_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=180)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()


def _gemini(messages, model, max_tokens, want_search):
    gmodel = GEMINI_MODELS.get(model, "gemini-2.5-pro")
    # Gemini uses a single combined text input; merge the messages.
    text = "\n\n".join(m["content"] for m in messages)
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if want_search:
        body["tools"] = [{"google_search": {}}]   # Gemini's live-search tool
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{gmodel}:generateContent?key={GEMINI_API_KEY}")
    r = requests.post(url, json=body, timeout=180)
    if r.status_code in (400, 422) and want_search:
        # Gemini rejected the google_search tool; retry without it so the planner
        # still returns signals from patterns + baselines rather than crashing.
        body.pop("tools", None)
        r = requests.post(url, json=body, timeout=180)
    r.raise_for_status()
    cand = r.json().get("candidates", [{}])[0]
    parts = cand.get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()



# ------------------------------ Apify ------------------------------

def scrape(city, checkin, checkout, max_items):
    """One Booking crawl for a city + date range. Returns raw item list."""
    url = (f"https://api.apify.com/v2/acts/{C.APIFY_ACTOR}"
           f"/run-sync-get-dataset-items?token={APIFY_TOKEN}")
    body = {
        "search": city,
        "checkIn": checkin.isoformat(), "checkOut": checkout.isoformat(),
        "currency": C.CURRENCY, "adults": C.ADULTS, "children": C.CHILDREN,
        "childrenAges": C.CHILDREN_AGES, "rooms": C.ROOMS, "maxItems": max_items,
        "proxyConfiguration": {"useApifyProxy": True,
                               "apifyProxyGroups": ["RESIDENTIAL"],
                               "countryCode": C.PROXY_GEO},
    }
    r = requests.post(url, json=body, timeout=600)
    r.raise_for_status()
    return r.json()


def normalize(items, nights, min_reviews):
    """Map raw items to clean per-night records. Field names are actor-specific —
    confirm them against your actor's sample output and rename here if needed."""
    out = []
    for h in items:
        price = h.get("price")
        score = h.get("reviewScore") or h.get("rating")
        nrev  = h.get("reviewsCount") or h.get("numberOfReviews") or 0
        stars = str(h.get("stars") or h.get("classCode") or "unknown")
        board = (h.get("mealPlan") or h.get("board") or "").lower()
        if price is None or score is None or int(nrev) < min_reviews:
            continue
        out.append({
            "name": h.get("name"), "stars": stars, "score": float(score),
            "reviews": int(nrev), "per_night": round(float(price) / max(nights, 1), 2),
            "board": board or "?",
            "all_inclusive": ("all" in board and "incl" in board),
            "url": h.get("url"),
        })
    return out


# ---------------------------- Anthropic ----------------------------

def anthropic(messages, model, max_tokens=2000, tools=None):
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if tools:
        body["tools"] = tools
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=180)
    r.raise_for_status()
    return r.json()


def text_of(resp):
    return "".join(b.get("text", "") for b in resp.get("content", [])
                   if b.get("type") == "text").strip()


def parse_json_block(text):
    """Strip markdown fences and parse the outermost JSON value the model returned,
    choosing object vs array by whichever bracket appears first."""
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    starts = [(t.find(c), c) for c in ("[", "{") if t.find(c) != -1]
    if not starts:
        return None
    _, open_c = min(starts)
    close_c = "]" if open_c == "[" else "}"
    i, j = t.find(open_c), t.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


# ------------------------------ State ------------------------------

def load_json(name, default):
    try:
        with open(os.path.join(STATE_DIR, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def today_iso():
    return dt.date.today().isoformat()
