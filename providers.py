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
    resp = requests.get(C.XOTELO_BASE_URL + path, params=params, timeout=C.HOTEL_HTTP_TIMEOUT)
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
    # result is typically a list of matches; take the first accommodation match
    items = result if isinstance(result, list) else result.get("items") or []
    for item in items:
        if item.get("type", "").lower() in ("accommodation", "hotel", "property", ""):
            return {
                "key": item.get("hotel_key") or item.get("key"),
                "name": item.get("name", ""),
                "location_key": item.get("location_key"),
                "raw": item,
            }
    # Fall back to the very first result if type is absent
    if items:
        first = items[0]
        return {
            "key": first.get("hotel_key") or first.get("key"),
            "name": first.get("name", ""),
            "location_key": first.get("location_key"),
            "raw": first,
        }
    return None


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

    # Validate currency
    resp_currency = (result.get("currency") or "").upper()
    if resp_currency and resp_currency != "EUR":
        return None  # never mislabel a non-EUR total

    # result.rates is a list of OTA offers; pick the lowest total
    rates = result.get("rates") or []
    if not rates:
        return None

    best = min(rates, key=lambda r: float(r.get("price") or r.get("total") or float("inf")))
    total = float(best.get("price") or best.get("total") or 0)
    if total <= 0:
        return None

    chk_in_d  = date.fromisoformat(chk_in)
    chk_out_d = date.fromisoformat(chk_out)
    nights = (chk_out_d - chk_in_d).days
    if nights <= 0:
        return None

    booking_url = best.get("url") or best.get("deal_url") or best.get("booking_url") or None
    ota_name    = best.get("name") or best.get("ota") or "OTA"

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
    """Return list of DateBlock dicts; [] on any error or unexpected shape."""
    # Derive a rough chk_out from window (any date-like string)
    dates = _extract_date_range(window)
    chk_out = dates[1] if dates else None
    if not chk_out:
        return []
    try:
        result = _get("/heatmap", {"hotel_key": hotel_key, "chk_out": chk_out})
        if not result or not isinstance(result, (list, dict)):
            return []
        items = result if isinstance(result, list) else result.get("days") or []
        blocks = []
        for item in items:
            ci = item.get("checkin") or item.get("date")
            price_val = item.get("price") or item.get("price_per_night")
            if ci and price_val:
                blocks.append({
                    "checkin":             ci,
                    "checkout":            item.get("checkout", ci),
                    "nights":              item.get("nights", 1),
                    "price_per_night_eur": float(price_val),
                })
        return blocks
    except Exception:
        return []


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
        destination = diamond.get("destination", "")
        est         = diamond.get("est_price_eur") or 0
        ceiling     = C.get_price_ceiling(destination)

        ref = resolve_hotel(destination)
        if not ref or not ref.get("key"):
            raise ValueError(f"Could not resolve hotel for: {destination}")

        dates = _parse_window(diamond.get("window", ""), destination)

        # Optionally refine dates via heatmap when window was month-wide
        if not dates:
            hm = heatmap(ref["key"], diamond.get("window", ""))
            if hm:
                # Pick the cheapest in-window block
                best_block = min(hm, key=lambda b: b["price_per_night_eur"])
                ci = best_block["checkin"]
                co = best_block.get("checkout") or str(
                    date.fromisoformat(ci) + timedelta(days=max(2, best_block.get("nights", 2)))
                )
                dates = (ci, co)

        if not dates:
            raise ValueError(f"Could not parse window: {diamond.get('window')}")

        chk_in, chk_out = dates
        rate = price(ref["key"], chk_in, chk_out)
        if not rate:
            raise ValueError(f"No EUR rates returned for {ref['key']} {chk_in}–{chk_out}")

        verdict, confidence = _decide_verdict(rate["price_per_night_eur"], est, ceiling)
        return _to_stage3(rate, verdict, confidence, ref, today)

    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  [providers] Xotelo grounding failed ({exc}), falling back to LLM")
        import find_city_anomalies as fa  # lazy import to avoid circular dependency
        return fa._ground_llm(diamond, mem_text, today)
