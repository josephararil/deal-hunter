"""trips.py — read-only log of trips this family has actually booked.

state/trips.json is hand-maintained by a human, never by the pipeline. It gives the
pipeline (a) revealed-preference context (what this family actually pays and chooses)
and (b) real paid prices as trustworthy baseline price anchors — stronger evidence than
any LLM guess.

Invariant T: this file is user-owned. The pipeline (and this module) reads
state/trips.json but never writes it. No function here calls common.save_json or
otherwise persists to that path.
"""

import json, os, datetime as dt

import common
import config as C
import memory as M
import providers as P

TRIPS_FILE = "trips.json"


def load():
    """Load state/trips.json. NEVER raises. Distinguishes 'missing file' (fine,
    returns default silently) from 'malformed JSON' (warns, returns default)."""
    path = os.path.join("state", TRIPS_FILE)
    try:
        data = common.load_json(TRIPS_FILE, {"trips": []})
    except Exception:
        return {"trips": []}
    if data == {"trips": []} and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError:
            print("  [trips] state/trips.json is not valid JSON — ignoring")
            return {"trips": []}
        except Exception:
            return {"trips": []}
    return data


def _parse_iso_date(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def valid_trips(data):
    """Validate + derive fields for each trip entry. Drops invalid entries with a
    one-line warning each. Never mutates the input."""
    required = ("hotel_name", "city", "country", "checkin", "checkout", "total_paid", "currency")
    out = []
    for i, entry in enumerate(data.get("trips", [])):
        reason = None
        for field in required:
            if not entry.get(field) and entry.get(field) != 0:
                reason = f"missing {field}"
                break
        if reason:
            print(f"  [trips] skipping entry {i}: {reason}")
            continue

        checkin_d = _parse_iso_date(entry["checkin"])
        if checkin_d is None:
            print(f"  [trips] skipping entry {i}: checkin is not a valid ISO date")
            continue
        checkout_d = _parse_iso_date(entry["checkout"])
        if checkout_d is None:
            print(f"  [trips] skipping entry {i}: checkout is not a valid ISO date")
            continue

        if not isinstance(entry["total_paid"], (int, float)) or isinstance(entry["total_paid"], bool):
            print(f"  [trips] skipping entry {i}: total_paid is not a number")
            continue

        nights = (checkout_d - checkin_d).days
        if nights <= 0:
            print(f"  [trips] skipping entry {i}: nights <= 0")
            continue

        trip = dict(entry)
        trip["nights"] = nights
        trip["price_per_night_eur"] = round(trip["total_paid"] / nights, 2) if trip["currency"] == "EUR" else None
        out.append(trip)
    return out


def price_anchors(trips):
    """EUR trips only: map memory.baseline_key-compatible keys to price anchor info."""
    anchors = {}
    for trip in trips:
        if trip["price_per_night_eur"] is None:
            continue
        key = M.baseline_key({
            "hotel_name": trip["hotel_name"],
            "city": trip["city"],
            "window": trip["checkin"],
        })
        anchors[key] = {
            "price_per_night_eur": trip["price_per_night_eur"],
            "hotel_name": trip["hotel_name"],
            "checkin": trip["checkin"],
        }
    return anchors


def summarize_for_prompt(trips):
    """Compact, bounded text block for prompt injection: most recent trips first."""
    if not trips:
        return "(no trips logged yet)"
    ordered = sorted(trips, key=lambda t: t["checkin"], reverse=True)[:C.MAX_TRIPS_IN_PROMPT]
    lines = ["Trips this family has actually booked (most recent first):"]
    for t in ordered:
        line = (f"  {t['hotel_name']} ({t['city']}, {t['country']}) "
                f"{t['checkin']}→{t['checkout']}, {t['nights']}n, "
                f"paid {t['currency']}{t['total_paid']}")
        if t["price_per_night_eur"] is not None:
            line += f" ~€{t['price_per_night_eur']}/night"
        if t.get("rating") is not None:
            line += f" · rated {t['rating']}/5"
        if t.get("notes"):
            line += f" · \"{t['notes']}\""
        lines.append(line)
    return "\n".join(lines)


def _row_key(row):
    return f"{M.identity({'destination': row.get('destination', '')})}|{M.season_key(row.get('window', '') or '')}"


def backtest(trips, memory):
    """Cross-reference booked trips against the pipeline's outcome ledger.
    Returns counts of picked (would-have-surfaced-as-diamond/good), skipped
    (seen but not surfaced), and unseen (never appeared in the ledger)."""
    ledger = memory.get("ledger", [])
    picked = skipped = unseen = 0
    for trip in trips:
        key = M.baseline_key({
            "hotel_name": trip["hotel_name"],
            "city": trip["city"],
            "window": trip["checkin"],
        })
        matches = [row for row in ledger if _row_key(row) == key]
        if any(row.get("verdict") in ("diamond", "good") for row in matches):
            picked += 1
        elif matches:
            skipped += 1
        else:
            unseen += 1
    return {"total": len(trips), "picked": picked, "skipped": skipped, "unseen": unseen}


def paste_snippet(item):
    """One-line JSON snippet (matching trips.json's shape) for the digest, so a human
    can paste a scored candidate straight into state/trips.json after actually booking it."""
    dates = P._extract_date_range(item.get("grounded_dates", "") or "")
    checkin, checkout = dates if dates else ("", "")
    total_paid = item.get("grounded_total_eur")
    snippet = {
        "hotel_name": item.get("hotel_name") or item.get("destination", ""),
        "city": item.get("city"),
        "country": item.get("country"),
        "checkin": checkin,
        "checkout": checkout,
        "total_paid": total_paid,
        "currency": "EUR",
        "booked_via": "booking.com",
        "rating": None,
        "notes": "",
    }
    return json.dumps(snippet)
