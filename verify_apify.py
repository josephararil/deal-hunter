"""
verify_apify.py — Layer-3 Apify grounding stub (NOT YET WIRED).

Apify free credits renew 2026-07-26. Do not enable before then.

This module will replace the LLM concierge as the Stage-3 grounding provider
once credits are available. It is intentionally disconnected from the active
pipeline — nothing imports or calls it today.

ATTACH POINT
------------
In find_city_anomalies.py the grounding seam is:

    ground_deal = _ground_llm   # current: LLM concierge

To switch to Apify after 2026-07-26:
1.  Set the APIFY_TOKEN secret in GitHub repo settings.
2.  Replace the seam line with:
        from verify_apify import apify_ground
        ground_deal = apify_ground
3.  Implement the TODO block inside apify_ground() below.

The call site in Stage 3 stays unchanged:
    result = ground_deal(diamond, mem_text, today)

apify_ground() must return the same schema as the LLM Stage-3 result:
  {verdict, destination, options[], how_to_book, grounding, assistant_summary, confidence}

REQUIRED SECRET
---------------
APIFY_TOKEN — GitHub repo secret. Referenced via os.environ["APIFY_TOKEN"].

Booking.com actor: "voyager/fast-booking-scraper"
Confirm field names against a live sample output if the actor slug ever changes.
"""

import os
import requests

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

# Apify actor slug for Booking.com scraping.
# Confirm against your Apify subscription — actor slugs can change between plans.
_APIFY_ACTOR = "voyager/fast-booking-scraper"

# Search parameters — match the family profile in the active pipeline
_ADULTS        = 2
_CHILDREN      = 1
_CHILDREN_AGES = [4]
_ROOMS         = 1
_CURRENCY      = "EUR"
_PROXY_GEO     = "BG"    # Bulgarian residential proxy — avoids geo-pricing distortion
_MAX_ITEMS     = 20
_MIN_REVIEWS   = 50


def _scrape(destination, checkin, checkout):
    """One Booking.com crawl via Apify for a destination and date range.

    destination: plain city/resort name string (e.g. "Bansko, Bulgaria")
    checkin, checkout: datetime.date objects
    Returns raw item list from the actor dataset.
    """
    url = (f"https://api.apify.com/v2/acts/{_APIFY_ACTOR}"
           f"/run-sync-get-dataset-items?token={APIFY_TOKEN}")
    body = {
        "search":         destination,
        "checkIn":        checkin.isoformat(),
        "checkOut":       checkout.isoformat(),
        "currency":       _CURRENCY,
        "adults":         _ADULTS,
        "children":       _CHILDREN,
        "childrenAges":   _CHILDREN_AGES,
        "rooms":          _ROOMS,
        "maxItems":       _MAX_ITEMS,
        "proxyConfiguration": {
            "useApifyProxy":      True,
            "apifyProxyGroups":   ["RESIDENTIAL"],
            "countryCode":        _PROXY_GEO,
        },
    }
    r = requests.post(url, json=body, timeout=600)
    r.raise_for_status()
    return r.json()


def _normalize(items, nights):
    """Map raw Apify/Booking.com items to clean per-night records.

    Field names match voyager/fast-booking-scraper sample output.
    Re-verify against a live sample if the actor changes.
    """
    out = []
    for h in items:
        price = h.get("price")
        score = h.get("reviewScore") or h.get("rating")
        nrev  = h.get("reviewsCount") or h.get("numberOfReviews") or 0
        stars = str(h.get("stars") or h.get("classCode") or "unknown")
        board = (h.get("mealPlan") or h.get("board") or "").lower()
        if price is None or score is None or int(nrev) < _MIN_REVIEWS:
            continue
        out.append({
            "name":          h.get("name"),
            "stars":         stars,
            "score":         float(score),
            "reviews":       int(nrev),
            "per_night":     round(float(price) / max(nights, 1), 2),
            "board":         board or "?",
            "all_inclusive": ("all" in board and "incl" in board),
            "url":           h.get("url"),
        })
    return out


def apify_ground(diamond, mem_text=None, today=None):
    """Layer-3 grounding via Apify/Booking.com. NOT YET ENABLED.

    Signature matches _ground_llm so it can drop in at the grounding seam:
        ground_deal = apify_ground

    diamond: Stage-2 survivor dict — includes destination, window, type, reason, etc.
    mem_text: memory summary string (not used by Apify; accepted for seam compatibility)
    today: ISO date string (not used by Apify; accepted for seam compatibility)

    Returns the Stage-3 result schema:
    {
      "destination": str,
      "verdict":     "confirm" | "correct" | "kill",
      "options":     [{"dates", "nights", "price_per_night_eur", "total_eur",
                       "booking_url", "source"}, ...],
      "how_to_book": str,
      "grounding":   str,
      "assistant_summary": str,
      "confidence":  "high" | "medium" | "low",
    }

    TODO (after 2026-07-26):
      1. Parse diamond["window"] into 1-3 specific (checkin, checkout) date pairs.
         The window is LLM-generated text (e.g. "Aug 8-10, 2026") — use dateutil.parser
         or a simple regex; fall back to verdict=kill if unparseable.
      2. For each date pair: items = _scrape(diamond["destination"], checkin, checkout)
         hotels = _normalize(items, nights)
      3. Find the best-value hotel match for the diamond's destination/type.
      4. Build and return the Stage-3 result dict with real prices, booking URL, grounding.
      5. Set verdict=confirm if real price validates the claim, correct if price differs,
         kill if no matching results found or price is unremarkable.
    """
    raise NotImplementedError(
        "Apify grounding is not yet enabled. "
        "Apify credits renew 2026-07-26. "
        "See verify_apify.py ATTACH POINT section for wiring instructions."
    )
