"""Shared helpers used by all three pipelines."""

import os, json, datetime as dt
import requests
import config as C

APIFY_TOKEN       = os.environ.get("APIFY_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATE_DIR = "state"


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
