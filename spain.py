"""Spain sweep: a deterministic flight + hotel price sweep for a family that plans to move there.

Flights first. Ryanair's public fare calendar (keyless, one call per route-month) shows the
whole SOF<->city fare distribution. Hotels (Booking.com via providers.py) are priced only for
the SPAIN_WINDOWS_PER_CITY cheapest non-overlapping flight windows per city. Each window is
then measured as a ratio against the typical price for the SAME dates:

    ratio = (best hotel ppn x nights + passengers x round-trip fare)
          / (p25 hotel ppn x nights + passengers x median round-trip fare)

The arithmetic decides the tier. The LLM is called once afterwards (add_dossiers) to write
descriptive prose and must never touch a number (Invariant SP): only about / value_case /
red_flags are ever copied back from its answer.
"""

import json
import statistics
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import requests

import config as C
import common as X
import llm_chain as L
import memory as M
import providers as P


def months_between(start, end):
    """First-of-month dates covering [start, end]."""
    out = []
    m = start.replace(day=1)
    while m <= end:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def fetch_calendar(origin, dest, month_start):
    """One Ryanair cheapest-per-day call: {date: EUR one-way fare} for bookable days only.
    HTTP and JSON errors propagate to the caller."""
    r = requests.get(
        C.RYANAIR_FARES_URL.format(origin=origin, dest=dest),
        params={"outboundMonthOfDate": month_start.isoformat(), "currency": "EUR"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=C.RYANAIR_HTTP_TIMEOUT,
    )
    r.raise_for_status()
    fares = {}
    for entry in r.json()["outbound"]["fares"]:
        price = entry.get("price")
        if not isinstance(price, dict) or not _is_num(price.get("value")):
            continue
        if entry.get("soldOut") or entry.get("unavailable"):
            continue
        if price.get("currencyCode", "EUR") != "EUR":
            continue
        fares[date.fromisoformat(entry["day"])] = float(price["value"])
    return fares


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def build_combos(out_fares, ret_fares, start, end, nights):
    """Every round trip (out in [start, end], return n nights later) where both legs have a fare."""
    combos = []
    for d in sorted(out_fares):
        if not start <= d <= end:
            continue
        for n in nights:
            r = d + timedelta(days=n)
            if r in ret_fares:
                o, f = out_fares[d], ret_fares[r]
                combos.append({"out": d, "ret": r, "nights": n,
                               "out_eur": o, "ret_eur": f, "rt_pp": round(o + f, 2)})
    return combos


def pick_windows(combos, k):
    """The k cheapest round trips whose [out, ret) ranges do not overlap."""
    taken = []
    for c in sorted(combos, key=lambda c: (c["rt_pp"], c["out"], c["nights"])):
        if any(c["out"] < t["ret"] and t["out"] < c["ret"] for t in taken):
            continue
        taken.append(c)
        if len(taken) == k:
            break
    return taken


def qualifying_rates(cards):
    """Property cards that are EUR-priced, star-rated and reviewed well enough to count."""
    rates = []
    for card in cards:
        breakdown = card.get("composite_price_breakdown") or {}
        ppn = (breakdown.get("gross_amount_per_night") or {}).get("value")
        if not _is_num(ppn):
            continue
        if (P.card_currency(card) or "EUR").upper() != "EUR":
            continue
        stars, review = card.get("class"), card.get("review_score")
        if not _is_num(stars) or stars < C.SPAIN_MIN_STARS:
            continue
        if not _is_num(review) or review < C.SPAIN_MIN_REVIEW:
            continue
        rates.append({"name": card.get("hotel_name") or "", "ppn": round(float(ppn), 2),
                      "stars": int(stars), "review": float(review)})
    return rates


def _window_label(out, ret):
    if (out.year, out.month) == (ret.year, ret.month):
        return f"{out:%b} {out.day}-{ret.day}, {ret.year}"
    return f"{out:%b} {out.day}-{ret:%b} {ret.day}, {ret.year}"


def evaluate_window(city, airport, combo, rates, flight_median):
    """Price one flight window against the same-dates typical price. None if too few hotels."""
    if len(rates) < C.SPAIN_MIN_HOTEL_CARDS:
        return None
    best = min(rates, key=lambda r: (r["ppn"], -r["review"]))
    out, ret, nights = combo["out"], combo["ret"], combo["nights"]
    checkin, checkout = out.isoformat(), ret.isoformat()

    hotel_ppn = best["ppn"]
    p25 = round(statistics.quantiles([r["ppn"] for r in rates], n=4, method="inclusive")[0], 2)
    rt_pp = round(combo["out_eur"] + combo["ret_eur"], 2)
    fixed = C.SPAIN_BAGS_EUR + C.SPAIN_GROUND_EUR
    trip_var = round(hotel_ppn * nights + C.SPAIN_PASSENGERS * rt_pp, 2)
    typical_var = round(p25 * nights + C.SPAIN_PASSENGERS * flight_median, 2)
    ratio = round(trip_var / typical_var, 4)
    if ratio <= C.SPAIN_DIAMOND_RATIO:
        tier = "diamond"
    elif ratio <= C.SPAIN_GOOD_RATIO:
        tier = "good"
    else:
        tier = "none"

    hotel_url = "https://www.booking.com/searchresults.html?" + urlencode({
        "ss":             best["name"],
        "checkin":        checkin,
        "checkout":       checkout,
        "group_adults":   C.HOTEL_ADULTS,
        "group_children": len(C.HOTEL_CHILDREN_AGES),
        "age":            ",".join(map(str, C.HOTEL_CHILDREN_AGES)),
    })
    flight_url = (f"https://www.ryanair.com/gb/en/trip/flights/select?adults=2&teens=0&children=1"
                  f"&infants=0&dateOut={checkin}&dateIn={checkout}&isConnectedFlight=false&discount=0"
                  f"&isReturn=true&promoCode=&originIata={C.SPAIN_ORIGIN}&destinationIata={airport}")
    allin = round(trip_var + fixed, 2)
    return {
        "kind": "spain",
        "city": city,
        "country": "Spain",
        "airport": airport,
        "hotel_name": best["name"],
        "stars": best["stars"],
        "review_score": best["review"],
        "checkin": checkin,
        "checkout": checkout,
        "nights": nights,
        "window": _window_label(out, ret),
        "destination": f"{city} · {best['name']}",
        "hotel_ppn_eur": hotel_ppn,
        "hotel_total_eur": round(hotel_ppn * nights, 2),
        "hotel_p25_ppn_eur": p25,
        "hotel_sample": len(rates),
        "flight_out_eur": combo["out_eur"],
        "flight_ret_eur": combo["ret_eur"],
        "flight_rt_pp_eur": rt_pp,
        "flight_median_rt_pp_eur": flight_median,
        "flight_family_eur": round(C.SPAIN_PASSENGERS * rt_pp, 2),
        "fixed_eur": fixed,
        "trip_variable_eur": trip_var,
        "typical_variable_eur": typical_var,
        "ratio": ratio,
        "allin_total_eur": allin,
        "typical_allin_eur": round(typical_var + fixed, 2),
        "tier": tier,
        "hotel_url": hotel_url,
        "flight_url": flight_url,
        "realert_key": "spain:" + M.identity_key({"hotel_name": best["name"]}),
        "realert_price_eur": allin,
        "about": "",
        "value_case": "",
        "red_flags": "",
    }


def _merge_calendars(origin, dest, months):
    fares = {}
    for m in months:
        fares.update(fetch_calendar(origin, dest, m))
        time.sleep(C.RYANAIR_PAUSE_S)
    return fares


def _sweep_city(city, airport, mem, start, end, ret_end, out):
    """Sweep one city, appending to out["deals"/"windows"] and returning its summary row."""
    row = {"city": city, "airport": airport, "status": "ok",
           "fare_days": 0, "combos": 0, "windows_priced": 0}
    try:
        out_fares = _merge_calendars(C.SPAIN_ORIGIN, airport, months_between(start, end))
        ret_fares = _merge_calendars(airport, C.SPAIN_ORIGIN, months_between(start, ret_end))
    except (requests.RequestException, ValueError, KeyError) as exc:
        row["status"] = f"flight lookup failed: {exc}"
        return row

    row["fare_days"] = sum(1 for d in out_fares if start <= d <= end)
    if row["fare_days"] == 0:
        row["status"] = "no direct Ryanair flights"
        return row
    combos = build_combos(out_fares, ret_fares, start, end, C.SPAIN_NIGHTS)
    row["combos"] = len(combos)
    if len(combos) < C.SPAIN_MIN_COMBOS:
        row["status"] = "thin calendar"
        return row
    out["flights_ok"] += 1
    flight_median = round(statistics.median(c["rt_pp"] for c in combos), 2)

    cache = mem.setdefault("spain_dest", {})
    ref = cache.get(city)
    try:
        if not ref:
            found = P.resolve_hotel({"city": city, "country": "Spain", "hotel_name": "", "destination": city})
            if not found:
                row["status"] = "hotel lookup failed: city not resolved"
                return row
            ref = {k: found.get(k) for k in ("dest_id", "search_type", "kind", "name")}
            cache[city] = ref
        for combo in pick_windows(combos, C.SPAIN_WINDOWS_PER_CITY):
            cards = P.list_properties(ref, combo["out"].isoformat(), combo["ret"].isoformat(),
                                      order_by="popularity")
            res = evaluate_window(city, airport, combo, qualifying_rates(cards), flight_median)
            if res is None:
                continue
            out["windows"].append(res)
            out["hotels_ok"] += 1
            row["windows_priced"] += 1
            if res["tier"] != "none":
                out["deals"].append(res)
    except (requests.RequestException, ValueError, KeyError) as exc:
        row["status"] = f"hotel lookup failed: {exc}"
    return row


def run_sweep(mem, today):
    """Sweep every SPAIN_CITIES entry. Never raises; one city's failure never stops the others."""
    try:
        start = today + timedelta(days=C.SPAIN_LEAD_DAYS)
        end = today + timedelta(days=C.SPAIN_HORIZON_DAYS)
        ret_end = end + timedelta(days=max(C.SPAIN_NIGHTS))
        result = {"deals": [], "windows": [], "cities": [], "flights_ok": 0, "hotels_ok": 0, "error": ""}
        for city, airport in C.SPAIN_CITIES:
            try:
                row = _sweep_city(city, airport, mem, start, end, ret_end, result)
            except Exception as exc:  # last resort: keep the other cities running
                row = {"city": city, "airport": airport, "status": f"unexpected: {type(exc).__name__}: {exc}",
                       "fare_days": 0, "combos": 0, "windows_priced": 0}
            result["cities"].append(row)
            ratios = [w["ratio"] for w in result["windows"] if w["city"] == city]
            best = f"{min(ratios):.3f}" if ratios else "-"
            print(f"  Spain {city}: {row['status']} | fare days {row['fare_days']} | "
                  f"combos {row['combos']} | best ratio {best}")
        return result
    except Exception as e:
        return {"deals": [], "windows": [], "cities": [], "flights_ok": 0, "hotels_ok": 0,
                "error": f"{type(e).__name__}: {e}"}


def add_dossiers(deals, today):
    """One LLM call writes about / value_case / red_flags. No other key is ever copied (Invariant SP)."""
    if not deals:
        return
    trips = json.dumps([
        {"key": d["realert_key"], "city": d["city"], "hotel_name": d["hotel_name"],
         "stars": d["stars"], "review_score": d["review_score"], "window": d["window"],
         "nights": d["nights"], "hotel_ppn_eur": d["hotel_ppn_eur"],
         "hotel_p25_ppn_eur": d["hotel_p25_ppn_eur"], "flight_rt_pp_eur": d["flight_rt_pp_eur"],
         "flight_median_rt_pp_eur": d["flight_median_rt_pp_eur"],
         "allin_total_eur": d["allin_total_eur"], "typical_allin_eur": d["typical_allin_eur"]}
        for d in deals
    ], ensure_ascii=False, indent=2)
    try:
        res = L.call_llm(C.SPAIN_DOSSIER_PROMPT.format(today=today, trips=trips), stage="SPAIN",
                         max_tokens=C.MAX_TOKENS_SPAIN, want_search=False,
                         response_schema=C.SPAIN_DOSSIER_SCHEMA)
        X.STAGE_RESULTS.append(("SPAIN", res))
        if not res.ok:
            return
        items = X.parse_json_block(res.text)
        if not isinstance(items, list):
            return
        by_key = {d["realert_key"]: d for d in deals}
        for item in items:
            if not isinstance(item, dict):
                continue
            deal = by_key.get(item.get("key"))
            if deal is None:
                continue
            for field in ("about", "value_case", "red_flags"):
                deal[field] = str(item.get(field) or "")
    except Exception as e:
        print(f"  Spain dossier failed: {type(e).__name__}: {e}")
