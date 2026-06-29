"""Xotelo hotel grounding provider for the Stage-3 seam.

Plain functions + dicts only. No classes, no ABC, no factory.
On any failure the public entry point (ground_api) falls back to _ground_llm.
"""

import re
from datetime import date, timedelta

import requests

import config as C


# ── Internal HTTP ─────────────────────────────────────────────────────────────

def _get(path, params):
    """GET XOTELO_BASE_URL+path; return the result payload or raise."""
    headers = (
        {"X-RapidAPI-Key": C.RAPIDAPI_KEY, "X-RapidAPI-Host": C.XOTELO_RAPIDAPI_HOST}
        if C.RAPIDAPI_KEY else {}
    )
    resp = requests.get(C.XOTELO_BASE_URL + path, params=params, headers=headers, timeout=C.HOTEL_HTTP_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    if body.get("error"):
        raise ValueError(f"Xotelo error: {body['error']}")
    return body.get("result")


# ── Hotel resolution ──────────────────────────────────────────────────────────

def resolve_hotel(destination):
    """Return hotel_ref {key, name, location_key, raw} or None."""
    dest_lower = destination.lower()

    # Check manual mapping first (case-insensitive substring match on any key/alias)
    for alias, ref in C.HOTEL_MAPPING.items():
        if alias.lower() in dest_lower or dest_lower in alias.lower():
            return {
                "key": ref["key"],
                "name": ref.get("name", alias),
                "location_key": ref.get("location_key"),
                "raw": ref,
            }

    result = _get("/search", {"query": destination, "location_type": "accommodation"})
    if not result:
        return None
    # result is a dict; items are under result["list"]
    items = result.get("list") or []
    preferred = None
    fallback = None
    for item in items:
        key = item.get("key", "")
        if not _HOTEL_KEY_RE.match(key):
            continue
        ref = {"key": key, "name": item.get("name", ""), "url": item.get("url"), "raw": item}
        accom = (item.get("accommodation_type") or "").lower()
        if any(t in accom for t in ("hotel", "resort", "inn")):
            preferred = ref
            break
        if fallback is None:
            fallback = ref
    return preferred or fallback


# ── Rates ─────────────────────────────────────────────────────────────────────

def price(hotel_key, chk_in, chk_out):
    """Return HotelRate dict or None.

    HotelRate = {name, checkin, checkout, nights, price_per_night_eur,
                 total_eur, booking_url, source, currency: "EUR"}
    """
    params = {
        "hotel_key":        hotel_key,
        "chk_in":           chk_in,
        "chk_out":          chk_out,
        "currency":         C.HOTEL_CURRENCY,
        "rooms":            C.HOTEL_ROOMS,
        "adults":           C.HOTEL_ADULTS,
        "age_of_children":  ",".join(str(a) for a in C.HOTEL_CHILDREN_AGES),
    }
    result = _get("/rates", params)
    if not result:
        return None

    # response has no "currency" field — trust the currency=EUR request param
    rates = result.get("rates") or []
    if not rates:
        return None

    # "rate" is the price for the WHOLE STAY
    best = min(rates, key=lambda r: float(r.get("rate") or float("inf")))
    total = float(best.get("rate") or 0)
    if total <= 0:
        return None

    chk_in_d  = date.fromisoformat(chk_in)
    chk_out_d = date.fromisoformat(chk_out)
    nights = (chk_out_d - chk_in_d).days
    if nights <= 0:
        return None

    booking_url = None  # rates have no url in the Xotelo schema
    ota_name    = best.get("name") or "OTA"

    return {
        "name":                hotel_key,
        "checkin":             chk_in,
        "checkout":            chk_out,
        "nights":              nights,
        "price_per_night_eur": round(total / nights, 2),
        "total_eur":           round(total, 2),
        "booking_url":         booking_url,
        "source":              ota_name,
        "currency":            "EUR",
    }


# ── Heatmap (best-effort) ─────────────────────────────────────────────────────

def heatmap(hotel_key, window):
    """Return sorted list of cheap ISO check-in date strings within the candidate window; [] on error.

    Calls /heatmap and reads result["heatmap"]["cheap_price_days"] (ISO date strings; no prices).
    Filters to dates that fall inside the parsed candidate window.
    """
    # Parse the candidate window to derive the call's chk_out and the filter bounds
    raw_dates = _extract_date_range(window)
    if raw_dates:
        win_start = date.fromisoformat(raw_dates[0])
        win_end   = date.fromisoformat(raw_dates[1])
    else:
        m = re.search(r"([A-Za-z]+)\s+(\d{4})", window)
        if not m:
            return []
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        yr  = int(m.group(2))
        if not mon:
            return []
        win_start = date(yr, mon, 1)
        nxt = date(yr + (mon // 12), mon % 12 + 1, 1) if mon < 12 else date(yr + 1, 1, 1)
        win_end = nxt - timedelta(days=1)

    try:
        result = _get("/heatmap", {"hotel_key": hotel_key, "chk_out": str(win_end)})
        if not result or not isinstance(result, dict):
            return []
        cheap_days = result.get("heatmap", {}).get("cheap_price_days") or []
        in_window = []
        for d_str in cheap_days:
            try:
                d_val = date.fromisoformat(d_str)
                if win_start <= d_val <= win_end:
                    in_window.append(d_str)
            except (ValueError, TypeError):
                pass
        return sorted(in_window)
    except Exception:
        return []


# ── Window parsing ────────────────────────────────────────────────────────────

# Matches Xotelo property keys: "g<digits>-d<digits>"
_HOTEL_KEY_RE = re.compile(r"^g\d+-d\d+$")

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
        if m.group(4):  # "10-14 Sep 2026"
            d1, d2, mon_s, yr = int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4))
        else:           # "Sep 10-14, 2026"
            mon_s, d1, d2, yr = m.group(6) or m.group(3), int(m.group(6) or m.group(2)), int(m.group(7) or m.group(2)), int(m.group(8) or m.group(4))
            # Re-parse properly
            groups = m.groups()
            if groups[4]:  # second pattern
                mon_s = groups[4]; d1 = int(groups[5]); d2 = int(groups[6]); yr = int(groups[7])
            else:
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

    - Explicit short range → use as-is.
    - Month-wide window ("Aug 1-31, 2026") → pick an in-window Fri–Sun 2-night block.
    - Unparseable → None.
    """
    dates = _extract_date_range(window)
    if dates:
        chk_in_d  = date.fromisoformat(dates[0])
        chk_out_d = date.fromisoformat(dates[1])
        nights = (chk_out_d - chk_in_d).days
        # If the range is short (≤7 nights) treat it as an explicit specific window
        if nights <= 7:
            return dates
        # Month-wide: pick a Fri–Sun block mid-window
        return _pick_weekend_block(chk_in_d, chk_out_d, destination)

    # Try to parse a bare month+year: "August 2026" / "Aug 2026"
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", window)
    if m:
        mon = _MONTH_MAP.get(m.group(1)[:3].lower())
        yr  = int(m.group(2))
        if mon:
            start = date(yr, mon, 1)
            # End of month
            nxt = date(yr + (mon // 12), mon % 12 + 1, 1) if mon < 12 else date(yr + 1, 1, 1)
            end = nxt - timedelta(days=1)
            return _pick_weekend_block(start, end, destination)
    return None


def _pick_weekend_block(start, end, destination):
    """Pick a Fri–Sun 2-night block mid-window, bounded by city min nights if known."""
    # Use config.CITIES night range when destination is known
    min_nights = 2
    if destination:
        dest_lower = destination.lower()
        for city_key, (mn, mx) in C.CITIES.items():
            if city_key.lower().split(",")[0] in dest_lower:
                min_nights = mn
                break

    nights = max(min_nights, 2)
    # Start from the midpoint, scan forward for a Friday
    mid = start + (end - start) // 2
    # weekday(): Mon=0, Fri=4
    days_to_fri = (4 - mid.weekday()) % 7
    fri = mid + timedelta(days=days_to_fri)
    sun = fri + timedelta(days=nights)
    # Ensure within window
    if sun <= end:
        return (str(fri), str(sun))
    # Fall back to start+nights if no Friday fits
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
    # Format dates with 4-digit year for downstream _dates_in_window compatibility
    chk_in  = rate["checkin"]   # already ISO: YYYY-MM-DD
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
    src  = f"Xotelo / {rate['source']} live {today}"
    burl = rate.get("booking_url")

    option = {
        "dates":               dates_str,
        "nights":              nts,
        "price_per_night_eur": ppn,
        "total_eur":           tot,
        "source":              src,
    }
    if burl:
        option["booking_url"] = burl

    if verdict == "confirm":
        summary = (
            f"Verified {hotel_name} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights) via {rate['source']}. "
            f"Price matches the Stage-1 estimate."
        )
    elif verdict == "correct":
        summary = (
            f"Found {hotel_name} for {dates_str}: €{ppn}/night "
            f"(€{tot} total, {nts} nights) via {rate['source']}. "
            f"Price is higher than initially estimated but remains under the ceiling."
        )
    else:
        summary = (
            f"Grounded price for {hotel_name} ({dates_str}) is €{ppn}/night, "
            f"which exceeds the country ceiling. Not emailing."
        )

    how_to_book = (
        f"Book via {rate['source']} at {burl}" if burl
        else f"Search for '{hotel_name}' on Booking.com, Expedia, or Hotels.com."
    )

    return {
        "destination":       hotel_name,
        "verdict":           verdict,
        "options":           [option],
        "how_to_book":       how_to_book,
        "grounding":         f"Xotelo /rates query for {chk_in}–{chk_out}, currency=EUR, "
                             f"adults={C.HOTEL_ADULTS}, children={C.HOTEL_CHILDREN_AGES}. "
                             f"Lowest OTA rate: {rate['source']} at €{tot} total.",
        "assistant_summary": summary,
        "confidence":        confidence,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def ground_api(diamond, mem_text, today):
    """Stage-3 grounding via Xotelo. Falls back to _ground_llm on any failure."""
    try:
        if not C.RAPIDAPI_KEY:
            raise ValueError("RAPIDAPI_KEY not set")

        destination = diamond.get("destination", "")
        est         = diamond.get("est_price_eur") or 0
        ceiling     = C.get_price_ceiling(destination)

        ref = resolve_hotel(destination)
        if not ref or not ref.get("key"):
            raise ValueError(f"Could not resolve hotel for: {destination}")

        window_text = diamond.get("window", "")
        dates = None
        confidence = "medium"

        # Prefer explicit short-range dates (≤7 nights) → high confidence
        raw_dates = _extract_date_range(window_text)
        if raw_dates:
            ci_d = date.fromisoformat(raw_dates[0])
            co_d = date.fromisoformat(raw_dates[1])
            if (co_d - ci_d).days <= 7:
                dates = raw_dates
                confidence = "high"

        # For month-wide windows: use heatmap cheap days → medium confidence
        if not dates:
            cheap_days = heatmap(ref["key"], window_text)
            if cheap_days:
                min_nights_city = 2
                dest_lower = destination.lower()
                for city_key, (mn, _mx) in C.CITIES.items():
                    if city_key.lower().split(",")[0] in dest_lower:
                        min_nights_city = mn
                        break
                chk_in_str  = cheap_days[0]  # earliest cheap day
                chk_out_str = str(date.fromisoformat(chk_in_str) + timedelta(days=min_nights_city))
                dates = (chk_in_str, chk_out_str)

        # Final fallback: weekend-guess from _parse_window → medium confidence
        if not dates:
            dates = _parse_window(window_text, destination)

        if not dates:
            raise ValueError(f"Could not parse window: {diamond.get('window')}")

        chk_in, chk_out = dates
        rate = price(ref["key"], chk_in, chk_out)
        if not rate:
            raise ValueError(f"No EUR rates returned for {ref['key']} {chk_in}–{chk_out}")

        verdict, _ = _decide_verdict(rate["price_per_night_eur"], est, ceiling)
        # Over-ceiling kills are certain; for confirm/correct use date-derived confidence
        final_confidence = "high" if verdict == "kill" else confidence
        return _to_stage3(rate, verdict, final_confidence, ref, today)

    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  [providers] Xotelo grounding failed ({exc}), falling back to LLM")
        import find_city_anomalies as fa  # lazy import to avoid circular dependency
        return fa._ground_llm(diamond, mem_text, today)
