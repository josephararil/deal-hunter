"""
Deal Hunter — finds hotels that are statistical price outliers vs their own class,
ranks them by absolute EUR saved, runs one harsh LLM filter, and emails a weekly digest.

Crawls DAILY (to catch ephemeral deals), accumulates the week's best into a pending
digest, and EMAILS once a week. Stateless detection: the peer group lives inside each
crawl. The only state is seen.json (dedupe) and pending_digest.json (the week's finds).
"""

import os, json, ssl, smtplib, statistics, random, datetime as dt
from email.message import EmailMessage
import requests

APIFY_TOKEN       = os.environ["APIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

APIFY_ACTOR  = "voyager~booking-scraper"   # verify this actor's INPUT field names on its Apify page
SEEN_PATH    = "seen.json"
PENDING_PATH = "pending_digest.json"

# ============================ CONFIG — edit freely ============================

# city -> (min_nights, max_nights). Each run picks a random length in range, so coverage
# smooths out over days instead of hardcoding one trip length. Bounds encode "worth the
# trip": longer minimums for far places. Tune these freely.
CITIES = {
    # --- Bulgaria, by car (<3h) ---
    "Asenovgrad, Bulgaria": (1, 3),  "Banya, Bulgaria": (2, 4),
    "Bansko, Bulgaria": (2, 7),      "Burgas, Bulgaria": (2, 7),
    "Chiflik, Bulgaria": (2, 4),     "Hisarya, Bulgaria": (2, 4),
    "Koprivshtitsa, Bulgaria": (1, 3), "Nessebar, Bulgaria": (2, 7),
    "Pazardzhik, Bulgaria": (1, 3),  "Pamporovo, Bulgaria": (2, 7),
    "Smolyan, Bulgaria": (2, 5),     "Sofia, Bulgaria": (1, 4),
    "Sozopol, Bulgaria": (2, 7),     "Stara Zagora, Bulgaria": (1, 3),
    "Veliko Tarnovo, Bulgaria": (2, 4),
    # --- Greece, by car ---
    "Alexandroupoli, Greece": (2, 5), "Kavala, Greece": (2, 5),
    "Komotini, Greece": (2, 4),       "Xanthi, Greece": (2, 4),
    # --- Turkey ---
    "Edirne, Turkey": (2, 3),         "Istanbul, Turkey": (3, 6),
    # --- by low-cost flight (min >= 2: a 1-night trip isn't worth the flight) ---
    "Bari, Italy": (3, 7),   "Milan, Italy": (2, 7),  "Naples, Italy": (3, 7),
    "Rome, Italy": (3, 7),   "Bratislava, Slovakia": (2, 7), "Vienna, Austria": (2, 7),
    "Athens, Greece": (3, 7), "Budapest, Hungary": (2, 7), "Krakow, Poland": (3, 7),
    "Belgrade, Serbia": (2, 5), "London, United Kingdom": (3, 7),
    "Birmingham, United Kingdom": (3, 7), "Manchester, United Kingdom": (3, 7),
    # --- suggested additions (uncomment if you'd actually go) ---
    # "Thessaloniki, Greece": (3, 6),
    # "Skopje, North Macedonia": (2, 5),
}

MIN_REVIEW_SCORE = 8.0     # HARD floor — never relaxed, beats everything
MIN_REVIEW_COUNT = 200     # excludes fake / brand-new listings

ADULTS, CHILDREN, ROOMS = 2, 1, 1
CHILDREN_AGES = [4]
CURRENCY  = "EUR"
PROXY_GEO = "BG"

OUTLIER_Z      = -3.5      # robust z threshold; more negative = stricter
MIN_PEERS      = 6         # need this many same-class hotels to trust the baseline
MIN_EUR_BELOW  = 40.0      # min EUR/night below peers to qualify
LLM_CONFIDENCE = 0.75      # only keep deals the LLM is at least this sure about

DIGEST_WEEKDAY    = 6      # 0=Mon ... 6=Sun. Digest goes out on this day.
MAX_DIGEST_ITEMS  = 10     # cap the email to the top N by absolute EUR saved
SEEN_TTL_DAYS     = 21
# =============================================================================


def checkin_anchors():
    """Check-in dates to probe. Near-term horizons are stretched to 10/17/24 days so a
    deal caught any day in the week is still bookable when the weekly digest lands."""
    today = dt.date.today()
    anchors = [(f"in{d}d", today + dt.timedelta(days=d)) for d in (10, 17, 24)]
    seasonal = [
        ("late-may",  dt.date(today.year, 5, 25)),
        ("sept-coast", dt.date(today.year, 9, 5)),
        ("post-nye",  dt.date(today.year + 1, 1, 3)),
    ]
    anchors += [(lbl, ci) for lbl, ci in seasonal if ci > today]
    return anchors


def scrape(city, checkin, checkout):
    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?token={APIFY_TOKEN}"
    body = {
        "search": city,
        "checkIn": checkin.isoformat(), "checkOut": checkout.isoformat(),
        "currency": CURRENCY, "adults": ADULTS, "children": CHILDREN,
        "childrenAges": CHILDREN_AGES, "rooms": ROOMS, "maxItems": 300,
        "proxyConfiguration": {"useApifyProxy": True,
                               "apifyProxyGroups": ["RESIDENTIAL"], "countryCode": PROXY_GEO},
    }
    r = requests.post(url, json=body, timeout=600)
    r.raise_for_status()
    return r.json()


def normalize(items, nights):
    out = []
    for h in items:
        price = h.get("price")
        score = h.get("reviewScore") or h.get("rating")
        nrev  = h.get("reviewsCount") or h.get("numberOfReviews") or 0
        stars = str(h.get("stars") or h.get("classCode") or "unknown")
        board = (h.get("mealPlan") or h.get("board") or "").lower()
        if price is None or score is None:
            continue
        if float(score) < MIN_REVIEW_SCORE or int(nrev) < MIN_REVIEW_COUNT:
            continue
        out.append({
            "name": h.get("name"), "stars": stars, "score": float(score),
            "reviews": int(nrev), "per_night": round(float(price) / max(nights, 1), 2),
            "board": board or "?",
            "all_inclusive": "all" in board and "incl" in board,
            "url": h.get("url"),
        })
    return out


def robust_outliers(hotels, city, label, ci, co, nights):
    """Within each star class, flag deliberate-looking underpricing (apples to apples)."""
    groups = {}
    for h in hotels:
        groups.setdefault(h["stars"], []).append(h)

    flagged = []
    for stars, group in groups.items():
        if len(group) < MIN_PEERS:
            continue
        prices = [x["per_night"] for x in group]
        med = statistics.median(prices)
        mad = statistics.median([abs(p - med) for p in prices]) or 1e-9
        for h in group:
            z = 0.6745 * (h["per_night"] - med) / mad
            eur_below = med - h["per_night"]
            if z <= OUTLIER_Z and eur_below >= MIN_EUR_BELOW:
                flagged.append({**h, "city": city, "window": label,
                                "checkin": ci.isoformat(), "checkout": co.isoformat(),
                                "nights": nights, "peer_median": round(med, 2),
                                "eur_below_peers": round(eur_below, 2),
                                "pct_below": round((1 - h["per_night"] / med) * 100),
                                "robust_z": round(z, 1)})
    return flagged


HARSH_PROMPT = """You are a ruthlessly skeptical luxury-travel deal scout. Your reputation \
depends on NEVER crying wolf. The user wants a near-empty inbox punctuated by the rare, \
genuinely exceptional find — not a stream of cheap hotels.

Below are hotels that are statistical price outliers vs their same-class peers in the same \
city and dates. Most are NOT real deals: data artifacts, mislabels, room-only rates posing \
as more, per-person prices misread as per-room, bad/remote locations, or hidden catches.

RANKING RULE: prioritize the largest ABSOLUTE EUR saving per night ("eur_below_peers"), NOT \
the largest percentage. A 140 EUR/night saving on a luxury hotel is dramatically better than \
a 50 EUR/night saving on a budget one. Use your knowledge of real hotel brands and markets — \
if you recognize a property whose normal rate sits far above the local median, the true \
saving is even larger than the number shown; weight that heavily. Also FAVOUR all-inclusive \
properties ("all_inclusive": true): they have the highest fixed costs and run the deepest, \
most genuine fire-sales, so an all-inclusive outlier is more likely to be real.

Pass a candidate ONLY if you would personally stake your reputation that it is a real, \
bookable, exceptional opportunity — the kind that happens when a manager deliberately dumps \
inventory (underbooking, post-holiday trough, charter overstock). When unsure, REJECT.

Candidates (JSON, each has an "id"):
%s

Return ONLY a JSON array (no prose, no markdown) of the ones that PASS:
[{"id":<int>, "why":<one sharp sentence, name the real brand value if you know it>, \
"confidence":<0-1>, "red_flags":<what to verify before booking>}]
Output [] if nothing qualifies."""


def llm_filter(shortlist):
    if not shortlist:
        return []
    slim = [{"id": i, "name": h["name"], "city": h["city"], "window": h["window"],
             "stars": h["stars"], "score": h["score"], "reviews": h["reviews"],
             "per_night": h["per_night"], "peer_median": h["peer_median"],
             "eur_below_peers": h["eur_below_peers"], "pct_below": h["pct_below"],
             "all_inclusive": h["all_inclusive"]}
            for i, h in enumerate(shortlist)]
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": 2000,
              "messages": [{"role": "user",
                            "content": HARSH_PROMPT % json.dumps(slim, ensure_ascii=False)}]},
        timeout=120)
    r.raise_for_status()
    text = "".join(b["text"] for b in r.json()["content"] if b["type"] == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        verdicts = json.loads(text)
    except json.JSONDecodeError:
        print("LLM did not return parseable JSON; no deals this run")
        return []

    passed = []
    for v in verdicts:
        if v.get("confidence", 0) < LLM_CONFIDENCE:
            continue
        h = shortlist[v["id"]]
        passed.append({**h, "why": v.get("why", ""),
                       "confidence": v.get("confidence"),
                       "red_flags": v.get("red_flags", "")})
    return passed


# ----------------------------- state helpers -----------------------------

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def prune_seen(seen):
    cutoff = (dt.date.today() - dt.timedelta(days=SEEN_TTL_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


# ----------------------------- email digest ------------------------------

def send_digest(deals):
    host = os.environ["SMTP_HOST"]; port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]; pw = os.environ["SMTP_PASS"]
    to   = os.environ.get("EMAIL_TO", user); frm = os.environ.get("EMAIL_FROM", user)

    rows = ""
    for d in deals:
        ai = " · all-inclusive" if d.get("all_inclusive") else ""
        rows += (
            f"<tr><td style='padding:12px 0;border-bottom:1px solid #eee'>"
            f"<div style='font-size:16px'><b>{d['name']}</b> "
            f"<span style='color:#888'>({d['city']}{ai})</span></div>"
            f"<div style='font-size:14px;margin:4px 0'>"
            f"<b>€{d['per_night']}/night</b> — save ~<b>€{d['eur_below_peers']}/night</b> "
            f"({d['pct_below']}% below peers) · {d['nights']} nights "
            f"{d['checkin']}→{d['checkout']}</div>"
            f"<div style='font-size:14px;color:#333'>{d.get('why','')}</div>"
            f"<div style='font-size:13px;color:#b00'>⚠️ {d.get('red_flags','')}</div>"
            + (f"<div style='font-size:13px'><a href='{d['url']}'>Open on Booking</a></div>"
               if d.get('url') else "")
            + "</td></tr>"
        )
    html = (f"<div style='font-family:system-ui,sans-serif;max-width:640px'>"
            f"<h2>Deal Hunter — {len(deals)} find(s) this week</h2>"
            f"<table style='width:100%;border-collapse:collapse'>{rows}</table>"
            f"<p style='color:#aaa;font-size:12px'>Ranked by absolute EUR saved. "
            f"Verify before booking.</p></div>")
    text = "\n\n".join(
        f"{d['name']} ({d['city']}) — €{d['per_night']}/night, save €{d['eur_below_peers']}/night, "
        f"{d['nights']}n {d['checkin']}->{d['checkout']}\n{d.get('why','')}\n{d.get('url','')}"
        for d in deals)

    msg = EmailMessage()
    msg["Subject"] = f"🏨 Deal Hunter: {len(deals)} find(s) this week"
    msg["From"] = frm; msg["To"] = to
    msg.set_content(text); msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


# --------------------------------- main ----------------------------------

def main():
    # 1) crawl and detect (daily)
    shortlist = []
    for city, (lo, hi) in CITIES.items():
        for label, ci in checkin_anchors():
            nights = random.randint(lo, hi)
            co = ci + dt.timedelta(days=nights)
            try:
                items = scrape(city, ci, co)
                hotels = normalize(items, nights)
                shortlist += robust_outliers(hotels, city, label, ci, co, nights)
            except Exception as e:
                print(f"[skip] {city}/{label}: {e}")

    shortlist.sort(key=lambda h: h["eur_below_peers"], reverse=True)
    print(f"{len(shortlist)} qualifying outliers before LLM filter")

    # 2) harsh LLM filter, accumulate the week's fresh finds
    deals = llm_filter(shortlist)
    seen = prune_seen(load_json(SEEN_PATH, {}))
    pending = load_json(PENDING_PATH, [])
    today = dt.date.today().isoformat()
    for d in deals:
        key = f"{d['name']}|{d['window']}|{round(d['per_night'])}"
        if key not in seen:
            pending.append(d); seen[key] = today
    print(f"{len(deals)} passed LLM; {len(pending)} accumulated for the digest")

    # 3) on digest day, drop expired finds, send the best, clear the queue
    force = os.environ.get("FORCE_DIGEST", "").lower() in ("1", "true")
    if dt.date.today().weekday() == DIGEST_WEEKDAY or force:
        live = [d for d in pending if d.get("checkout", today) >= today]
        live.sort(key=lambda d: d["eur_below_peers"], reverse=True)
        live = live[:MAX_DIGEST_ITEMS]
        if live:
            send_digest(live)
            print(f"emailed digest with {len(live)} deals")
        else:
            print("digest day, but nothing worth sending")
        pending = []  # clear regardless so stale items don't pile up

    save_json(SEEN_PATH, seen)
    save_json(PENDING_PATH, pending)


if __name__ == "__main__":
    main()
