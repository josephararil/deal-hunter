"""
hunt.py  —  Pipeline B (find the actual rooms)

Runs only on cities that Pipeline A flagged for a deep crawl today (or cities you pass
manually via the HUNT_CITIES env var, comma-separated). For each, it does a deep crawl and
runs TWO detectors:

  1. Cross-sectional outlier  — a hotel far below its same-class peers in the same crawl
                                ("crazy manager"), via robust z-score.
  2. Market / absolute drop   — a hotel well below the SEASONAL baseline for its
                                city|class|month ("the whole city is cheap right now", and
                                plain absolute bargains).

Both feed one shortlist, ranked by absolute EUR saved. One harsh Claude call vets it,
told the city was flagged and why, and to reject boring low-season non-deals. Survivors
accumulate into a weekly email digest.
"""

import os, statistics, random, ssl, smtplib, datetime as dt
from email.message import EmailMessage
import config as C
import common as X


def checkin_anchors():
    today = dt.date.today()
    anchors = [(f"in{d}d", today + dt.timedelta(days=d)) for d in (10, 17, 24)]
    seasonal = [("late-may", dt.date(today.year, 5, 25)),
                ("sept-coast", dt.date(today.year, 9, 5)),
                ("post-nye", dt.date(today.year + 1, 1, 3))]
    anchors += [(l, ci) for l, ci in seasonal if ci > today]
    return anchors


def baseline_median(baselines, city, stars, month):
    entry = baselines.get(f"{city}|{stars}|{month}")
    if entry and entry.get("samples"):
        return statistics.median(entry["samples"])
    return None


def detect(hotels, baselines, city, label, ci, co, nights):
    """Run both detectors over one crawl; return flagged candidates with a 'saved' figure."""
    month = ci.month
    by_class = {}
    for h in hotels:
        by_class.setdefault(h["stars"], []).append(h)

    flagged = {}
    for stars, group in by_class.items():
        prices = [x["per_night"] for x in group]
        med = statistics.median(prices) if prices else 0
        mad = statistics.median([abs(p - med) for p in prices]) or 1e-9 if prices else 1e-9
        base = baseline_median(baselines, city, stars, month)

        for h in group:
            reasons, saved = [], 0.0

            # detector 1: cross-sectional outlier (needs enough peers)
            if len(group) >= C.MIN_PEERS:
                z = 0.6745 * (h["per_night"] - med) / mad
                eur_below_peers = med - h["per_night"]
                if z <= C.OUTLIER_Z and eur_below_peers >= C.MIN_EUR_BELOW:
                    reasons.append(f"{round(eur_below_peers)} EUR below same-class peers (z={round(z,1)})")
                    saved = max(saved, eur_below_peers)

            # detector 2: below seasonal baseline (market-wide drop + absolute bargain)
            if base:
                eur_below_base = base - h["per_night"]
                if (h["per_night"] <= base * (1 - C.MARKET_DROP_PCT)
                        and eur_below_base >= C.MIN_EUR_BELOW):
                    reasons.append(f"{round(eur_below_base)} EUR below seasonal norm (~EUR{round(base)})")
                    saved = max(saved, eur_below_base)

            if reasons:
                key = f"{h['name']}|{label}"
                if key not in flagged or saved > flagged[key]["saved"]:
                    flagged[key] = {**h, "city": city, "window": label,
                                    "checkin": ci.isoformat(), "checkout": co.isoformat(),
                                    "nights": nights, "peer_median": round(med, 2),
                                    "seasonal_norm": round(base, 2) if base else None,
                                    "saved": round(saved, 2),
                                    "signals": "; ".join(reasons)}
    return list(flagged.values())


HARSH_PROMPT = """You are a ruthlessly skeptical luxury-travel deal scout. Your reputation \
depends on NEVER crying wolf — the user wants a near-empty inbox punctuated by rare, genuinely \
exceptional finds.

These hotels were flagged in cities currently believed to have unusually low prices. Context \
on why each city was flagged:
{context}

Each candidate is either far below its same-class peers, far below the city's seasonal norm, \
or both. Most are still NOT real deals: data artifacts, mislabels, room-only posing as more, \
per-person misread as per-room, bad/remote locations, or just boring low-season pricing that \
isn't actually special.

REJECT a candidate if its cheapness is merely "it's low season here" with nothing exceptional \
about it. PASS only genuinely great deals: a high-rated property at a price that is remarkable \
even accounting for the season — a deliberate underpricing, a real market collapse, or an \
absolute bargain (e.g. a recognised 4-5* brand at a budget price). Favour all-inclusive \
properties (deepest, most genuine fire-sales). Use brand knowledge: if you know a property's \
true normal rate sits far above the figure shown, the saving is bigger than stated — weight it.

RANK by absolute EUR saved per night, not percentage.

Candidates (JSON, each has an "id"):
{candidates}

Return ONLY a JSON array of the ones that PASS:
[{{"id": <int>, "why": <one sharp sentence, name the brand value if known>, \
"confidence": <0-1>, "red_flags": <what to verify before booking>}}]
Output [] if nothing qualifies."""


def harsh_filter(shortlist, context):
    if not shortlist:
        return []
    slim = [{"id": i, "name": h["name"], "city": h["city"], "stars": h["stars"],
             "score": h["score"], "reviews": h["reviews"], "per_night": h["per_night"],
             "all_inclusive": h["all_inclusive"], "saved": h["saved"],
             "signals": h["signals"]} for i, h in enumerate(shortlist)]
    import json
    raw = X.llm(messages=[{"role": "user", "content": HARSH_PROMPT.format(
        context=context, candidates=json.dumps(slim, ensure_ascii=False))}],
        model=C.MODEL_FILTER, max_tokens=2000)
    verdicts = X.parse_json_block(raw) or []
    passed = []
    for v in verdicts:
        if isinstance(v, dict) and v.get("confidence", 0) >= C.LLM_CONFIDENCE:
            h = shortlist[v["id"]]
            passed.append({**h, "why": v.get("why", ""),
                           "confidence": v.get("confidence"),
                           "red_flags": v.get("red_flags", "")})
    return passed


def send_digest(deals):
    host = os.environ["SMTP_HOST"]; port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]; pw = os.environ["SMTP_PASS"]
    to = os.environ.get("EMAIL_TO", user); frm = os.environ.get("EMAIL_FROM", user)

    rows = ""
    for d in deals:
        ai = " · all-inclusive" if d.get("all_inclusive") else ""
        norm = f" · seasonal norm ~EUR{d['seasonal_norm']}" if d.get("seasonal_norm") else ""
        rows += (f"<tr><td style='padding:12px 0;border-bottom:1px solid #eee'>"
                 f"<div style='font-size:16px'><b>{d['name']}</b> "
                 f"<span style='color:#888'>({d['city']}{ai})</span></div>"
                 f"<div style='font-size:14px;margin:4px 0'><b>EUR {d['per_night']}/night</b> — "
                 f"save ~<b>EUR {d['saved']}/night</b> · {d['nights']} nights "
                 f"{d['checkin']}→{d['checkout']}{norm}</div>"
                 f"<div style='font-size:13px;color:#555'>{d['signals']}</div>"
                 f"<div style='font-size:14px;color:#333'>{d.get('why','')}</div>"
                 f"<div style='font-size:13px;color:#b00'>⚠️ {d.get('red_flags','')}</div>"
                 + (f"<div style='font-size:13px'><a href='{d['url']}'>Open on Booking</a></div>"
                    if d.get('url') else "") + "</td></tr>")
    html = (f"<div style='font-family:system-ui,sans-serif;max-width:640px'>"
            f"<h2>Deal Hunter — {len(deals)} find(s) this week</h2>"
            f"<table style='width:100%;border-collapse:collapse'>{rows}</table>"
            f"<p style='color:#aaa;font-size:12px'>Ranked by absolute EUR saved. "
            f"Verify before booking.</p></div>")
    text = "\n\n".join(f"{d['name']} ({d['city']}) — EUR{d['per_night']}/night, "
                       f"save EUR{d['saved']}/night\n{d.get('why','')}\n{d.get('url','')}"
                       for d in deals)
    msg = EmailMessage()
    msg["Subject"] = f"🏨 Deal Hunter: {len(deals)} find(s) this week"
    msg["From"] = frm; msg["To"] = to
    msg.set_content(text); msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


def cities_to_hunt():
    override = os.environ.get("HUNT_CITIES", "").strip()
    if override:
        return [(c.strip(), f"manually requested") for c in override.split(",")]
    signals = X.load_json("city_signals.json", {"signals": []}).get("signals", [])
    return [(s["city"], s.get("reason", "")) for s in signals
            if s.get("hunt") and s.get("city") in C.CITIES]


def main():
    targets = cities_to_hunt()
    if not targets:
        print("no cities flagged for a deep crawl today; nothing to hunt")
    baselines = X.load_json("baselines.json", {})
    context_lines = [f"- {c}: {why}" for c, why in targets]

    shortlist = []
    for city, _why in targets:
        lo, hi = C.CITIES.get(city, (2, 5))
        for label, ci in checkin_anchors():
            nights = random.randint(lo, hi)
            co = ci + dt.timedelta(days=nights)
            try:
                items = X.scrape(city, ci, co, C.SCRAPE_MAX_HUNT)
                hotels = X.normalize(items, nights, C.MIN_REVIEW_COUNT)
                hotels = [h for h in hotels if h["score"] >= C.MIN_REVIEW_SCORE]
                shortlist += detect(hotels, baselines, city, label, ci, co, nights)
            except Exception as e:
                print(f"[skip] hunt {city}/{label}: {e}")

    shortlist.sort(key=lambda h: h["saved"], reverse=True)
    print(f"{len(shortlist)} flagged candidates before LLM filter")

    deals = harsh_filter(shortlist, "\n".join(context_lines) or "(manual run)")

    # accumulate into the weekly digest, deduped
    seen = X.load_json("seen.json", {})
    cutoff = (dt.date.today() - dt.timedelta(days=C.SEEN_TTL_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    pending = X.load_json("pending_digest.json", [])
    today = X.today_iso()
    for d in deals:
        key = f"{d['name']}|{d['window']}|{round(d['per_night'])}"
        if key not in seen:
            pending.append(d); seen[key] = today
    print(f"{len(deals)} passed filter; {len(pending)} accumulated for the digest")

    # send weekly (or on demand)
    force = os.environ.get("FORCE_DIGEST", "").lower() in ("1", "true")
    if dt.date.today().weekday() == C.DIGEST_WEEKDAY or force:
        live = [d for d in pending if d.get("checkout", today) >= today]
        live.sort(key=lambda d: d["saved"], reverse=True)
        live = live[:C.MAX_DIGEST_ITEMS]
        if live:
            send_digest(live)
            print(f"emailed digest with {len(live)} deals")
        else:
            print("digest day, but nothing worth sending")
        pending = []

    X.save_json("seen.json", seen)
    X.save_json("pending_digest.json", pending)


if __name__ == "__main__":
    main()
