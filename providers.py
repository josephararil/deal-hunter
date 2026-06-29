"""Booking.com (apidojo) hotel grounding provider for the Stage-3 seam.

Plain functions + dicts only. No classes, no ABC, no factory.
On any failure the public entry point (ground_api) falls back to _ground_llm.
"""

import re
from datetime import date, timedelta
from urllib.parse import urlencode

import requests

import config as C


# ── Internal HTTP ─────────────────────────────────────────────────────────────

def _get(path, params):
    """GET BOOKING_BASE_URL+path; return parsed JSON or raise."""
    if not C.RAPIDAPI_KEY:
        raise ValueError("RAPIDAPI_KEY not set")
    headers = {
        "X-RapidAPI-Key":  C.RAPIDAPI_KEY,
        "X-RapidAPI-Host": C.BOOKING_RAPIDAPI_HOST,
    }
    resp = requests.get(
        C.BOOKING_BASE_URL + path,
        params=params,
        headers=headers,
        timeout=C.HOTEL_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Fuzzy matching helpers ────────────────────────────────────────────────────

_STRIP_WORDS = re.compile(r'\b(hotel|the|resort|spa|&|and)\b', re.IGNORECASE)
_PUNCT = re.compile(r'[^\w\s]')


def _normalize(s):
    """Lowercase, strip noise words and punctuation, return token set."""
    s = _STRIP_WORDS.sub(' ', s.lower())
    s = _PUNCT.sub(' ', s)
    return set(s.split())


# ── Hotel resolution ──────────────────────────────────────────────────────────

def resolve_hotel(destination):
    """Return {dest_id, search_type, name, country, kind, raw} or None."""
    dest_lower = destination.lower()

    # Check HOTEL_MAPPING first (case-insensitive substring match on alias)
    for alias, ref in C.HOTEL_MAPPING.items():
        if alias.lower() in dest_lower or dest_lower in alias.lower():
            return {
                "dest_id":     ref["dest_id"],
                "search_type": ref["search_type"],
                "name":        ref.get("name", alias),
                "country":     ref.get("country", ""),
                "kind":        "specific",
                "raw":         ref,
            }

    # Country hint for validation: "Bansko, Bulgaria" → "Bulgaria"
    parts = [p.strip() for p in destination.split(",")]
    country_hint = parts[-1] if len(parts) > 1 else None
    # Candidate hotel portion (before the first comma) for name matching
    hotel_part = parts[0]

    data = _get("/locations/auto-complete", {"languagecode": "en-us", "text": destination})
    if not isinstance(data, list) or not data:
        return None

    candidate_tokens = _normalize(hotel_part)
    city_fallback = None

    for item in data:
        dtype = (item.get("dest_type") or "").lower()
        name = item.get("name") or item.get("label") or ""
        country = item.get("country") or ""

        # Country validation: skip entries whose country doesn't match the hint
        if country_hint and country_hint.lower() not in country.lower():
            continue

        if dtype in ("hotel", "landmark"):
            name_tokens = _normalize(name)
            if candidate_tokens and candidate_tokens.issubset(name_tokens):
                return {
                    "dest_id":     item["dest_id"],
                    "search_type": dtype,
                    "name":        name,
                    "country":     country,
                    "kind":        "specific",
                    "raw":         item,
                }

        elif dtype == "city" and city_fallback is None:
            city_fallback = {
                "dest_id":     item["dest_id"],
                "search_type": "city",
                "name":        name,
                "country":     country,
                "kind":        "city",
                "raw":         item,
            }

    return city_fallback


# ── Property listing ──────────────────────────────────────────────────────────

def list_properties(ref, chk_in, chk_out):
    """Return list of property_card dicts from /properties/v2/list."""
    params = {
        "offset":                    0,
        "arrival_date":              chk_in,
        "departure_date":            chk_out,
        "dest_ids":                  ref["dest_id"],
        "search_type":               ref["search_type"],
        "room_qty":                  C.HOTEL_ROOMS,
        "guest_qty":                 C.HOTEL_ADULTS,
        "children_qty":              len(C.HOTEL_CHILDREN_AGES),
        "children_age":              ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
        "price_filter_currencycode": "EUR",
        "order_by":                  "distance" if ref["kind"] == "specific" else "price",
        "languagecode":              "en-us",
        "units":                     "metric",
    }
    data = _get("/properties/v2/list", params)
    result = data.get("result") or []
    return [r for r in result if r.get("type") == "property_card"]


# ── Rate fetching ─────────────────────────────────────────────────────────────

def price(ref, chk_in, chk_out):
    """Return HotelRate dict or None.

    Fuzzy-matches ref["name"] against listing cards; returns None if no match
    (do NOT substitute a different hotel). The caller falls back to LLM.

    HotelRate = {name, checkin, checkout, nights, price_per_night_eur,
                 total_eur, booking_url, source, currency, review_score, stars}
    """
    cards = list_properties(ref, chk_in, chk_out)
    if not cards:
        return None

    target_tokens = _normalize(ref["name"])
    matched = None
    for card in cards:
        card_tokens = _normalize(card.get("hotel_name", ""))
        if target_tokens and target_tokens.issubset(card_tokens):
            matched = card
            break

    if matched is None:
        return None

    breakdown = matched.get("composite_price_breakdown") or {}
    ppn_block = breakdown.get("gross_amount_per_night") or {}
    ppn = ppn_block.get("value")
    if ppn is None:
        return None

    total_raw = matched.get("min_total_price")
    if total_raw is None:
        gross_block = breakdown.get("gross_amount") or {}
        total_raw = gross_block.get("value")
    if total_raw is None:
        return None

    chk_in_d  = date.fromisoformat(chk_in)
    chk_out_d = date.fromisoformat(chk_out)
    nights = (chk_out_d - chk_in_d).days
    if nights <= 0:
        return None

    name = matched.get("hotel_name") or ref["name"]
    booking_url = "https://www.booking.com/searchresults.html?" + urlencode({
        "ss":             name,
        "checkin":        chk_in,
        "checkout":       chk_out,
        "group_adults":   C.HOTEL_ADULTS,
        "group_children": len(C.HOTEL_CHILDREN_AGES),
        "age":            ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
    })

    return {
        "name":                name,
        "checkin":             chk_in,
        "checkout":            chk_out,
        "nights":              nights,
        "price_per_night_eur": round(float(ppn), 2),
        "total_eur":           round(float(total_raw), 2),
        "booking_url":         booking_url,
        "source":              "Booking.com (apidojo)",
        "currency":            "EUR",
        "review_score":        matched.get("review_score"),
        "stars":               matched.get("class"),
    }


# ── Window parsing ────────────────────────────────────────────────────────────

# Matches "Sep 10-14, 2026" or "10-14 Sep 2026" styles
_EXPLICIT_RE = re.compile(
    r"(?:(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)[\s,]+(\d{4}))"
    r"|(?:([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})[\s,]+(\d{4}))"
)
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _extract_date_range(window):
    """Try to parse any date range from window; return (chk_in_str, chk_out_str) or None."""
    m = _EXPLICIT_RE.search(window)
    if m:
        groups = m.groups()
        if groups[4]:  # "Sep 10-14, 2026"
            mon_s = groups[4]; d1 = int(groups[5]); d2 = int(groups[6]); yr = int(groups[7])
        else:          # "10-14 Sep 2026"
            mon_s = groups[2]; d1 = int(groups[0]); d2 = int(groups[1]); yr = int(groups[3])
        mon = _MONTH_MAP.get(mon_s[:3].lower())
        if mon:
            try:
                return (str(date(yr, mon, d1)), str(date(yr, mon, d2)))
            except ValueError:
                pass
    return None


def _parse_window(window, destination=None):
    """Return (chk_in, chk_out) strings or None.

    - Explicit short range (≤7 nights) → use as-is.
    - Month-wide window → pick a Fri–Sun block mid-window.
    - Unparseable → None.
    """
    dates = _extract_date_range(window)
    if dates:
        chk_in_d  = date.fromisoformat(dates[0])
        chk_out_d = date.fromisoformat(dates[1])
        nights = (chk_out_d - chk_in_d).days
        if nights <= 7:
            return dates
        return _pick_weekend_block(chk_in_d, chk_out_d, destination)

    m = re.search(r"([A-Za-z]+)\s+(\d{4})", window)
    if m:
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        yr  = int(m.group(2))
        if mon:
            start = date(yr, mon, 1)
            nxt = date(yr + (mon // 12), mon % 12 + 1, 1) if mon < 12 else date(yr + 1, 1, 1)
            end = nxt - timedelta(days=1)
            return _pick_weekend_block(start, end, destination)
    return None


def _pick_weekend_block(start, end, destination):
    """Pick a Fri–Sun 2-night block mid-window, bounded by city min nights if known."""
    min_nights = 2
    if destination:
        dest_lower = destination.lower()
        for city_key, (mn, _mx) in C.CITIES.items():
            if city_key.lower().split(",")[0] in dest_lower:
                min_nights = mn
                break

    nights = max(min_nights, 2)
    mid = start + (end - start) // 2
    days_to_fri = (4 - mid.weekday()) % 7
    fri = mid + timedelta(days=days_to_fri)
    sun = fri + timedelta(days=nights)
    if sun <= end:
        return (str(fri), str(sun))
    candidate_out = start + timedelta(days=nights)
    if candidate_out <= end:
        return (str(start), str(candidate_out))
    return None


# ── Verdict logic ─────────────────────────────────────────────────────────────

def _decide_verdict(g, est, ceiling):
    """g = grounded €/night, est = est_price_eur, ceiling = country ceiling."""
    if g > ceiling:
        return "kill", "high"
    if g <= est * 1.15:
        return "confirm", "high"
    return "correct", "high"


# ── Stage-3 result builder (no LLM) ──────────────────────────────────────────

def _to_stage3(rate, verdict, confidence, ref, today):
    """Build a Stage-3 result dict from a HotelRate. No LLM call."""
    chk_in  = rate["checkin"]
    chk_out = rate["checkout"]
    try:
        ci = date.fromisoformat(chk_in)
        co = date.fromisoformat(chk_out)
        dates_str = f"{ci.strftime('%b %-d')}-{co.strftime('%-d, %Y')}"
    except (ValueError, AttributeError):
        # Windows strftime doesn't support %-d; fall back to zero-padded
        try:
            ci = date.fromisoformat(chk_in)
            co = date.fromisoformat(chk_out)
            dates_str = f"{ci.strftime('%b %d')}-{co.strftime('%d, %Y')}"
        except Exception:
            dates_str = f"{chk_in} to {chk_out}"

    hotel_name = ref.get("name") or rate["name"]
    ppn  = rate["price_per_night_eur"]
    tot  = rate["total_eur"]
    nts  = rate["nights"]
    src  = f"Booking.com (apidojo) live {today}"
    burl = rate.get("booking_url")
    stars = rate.get("stars")
    score = rate.get("review_score")

    option = {
        "dates":               dates_str,
        "nights":              nts,
        "price_per_night_eur": ppn,
        "total_eur":           tot,
        "source":              src,
    }
    if burl:
        option["booking_url"] = burl

    stars_str = f", {int(stars)}-star" if stars else ""
    score_str = f", review {score}" if score else ""

    if verdict == "confirm":
        summary = (
            f"Verified {hotel_name}{stars_str} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights){score_str}. "
            f"Price matches the Stage-1 estimate."
        )
    elif verdict == "correct":
        summary = (
            f"Found {hotel_name}{stars_str} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights){score_str}. "
            f"Price differs from Stage-1 estimate but remains under the ceiling."
        )
    else:
        summary = (
            f"Grounded price for {hotel_name} ({dates_str}) is €{ppn}/night, "
            f"which exceeds the country ceiling. Not emailing."
        )

    how_to_book = (
        f"Book at {burl}" if burl
        else f"Search for '{hotel_name}' on Booking.com."
    )

    grounding_parts = [
        f"Booking.com (apidojo) /properties/v2/list for {chk_in}–{chk_out}, "
        f"currency=EUR, adults={C.HOTEL_ADULTS}, children={C.HOTEL_CHILDREN_AGES}."
    ]
    if stars:
        grounding_parts.append(f"Property class: {int(stars)}-star.")
    if score:
        grounding_parts.append(f"Review score: {score}.")
    grounding_parts.append(f"Live rate: €{ppn}/night (€{tot} total).")

    return {
        "destination":       hotel_name,
        "verdict":           verdict,
        "options":           [option],
        "how_to_book":       how_to_book,
        "grounding":         " ".join(grounding_parts),
        "assistant_summary": summary,
        "confidence":        confidence,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def ground_api(diamond, mem_text, today):
    """Stage-3 grounding via Booking.com (apidojo). Falls back to _ground_llm on any failure."""
    try:
        if not C.RAPIDAPI_KEY:
            raise ValueError("RAPIDAPI_KEY not set")

        destination = diamond.get("destination", "")
        est         = diamond.get("est_price_eur") or 0
        ceiling     = C.get_price_ceiling(destination)

        ref = resolve_hotel(destination)
        if not ref:
            raise ValueError(f"Could not resolve destination: {destination}")

        dates = _parse_window(diamond.get("window", ""), destination)
        if not dates:
            raise ValueError(f"Could not parse window: {diamond.get('window')}")

        chk_in, chk_out = dates
        rate = price(ref, chk_in, chk_out)
        if not rate:
            raise ValueError(f"No match in Booking.com results for {destination} {chk_in}–{chk_out}")

        verdict, _ = _decide_verdict(rate["price_per_night_eur"], est, ceiling)
        # Live Booking.com data → always high confidence; may seed baselines downstream
        return _to_stage3(rate, verdict, "high", ref, today)

    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  [providers] Booking.com grounding failed ({exc}), falling back to LLM")
        import find_city_anomalies as fa  # lazy import to avoid circular dependency
        return fa._ground_llm(diamond, mem_text, today)
