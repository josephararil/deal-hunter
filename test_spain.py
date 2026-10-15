"""Offline tests for spain.py. Run: python test_spain.py

No network, no state/ writes: memory is a plain dict and M.load()/M.save() are never called.
"""

import json
from datetime import date

import requests

import config as C
import common as X
import llm_chain as L
import providers as P
import spain as S

C.RYANAIR_PAUSE_S = 0

TODAY = date(2026, 10, 19)
NOV = date(2026, 11, 1)

OUT_FARES = {date(2026, 11, 3): 19.99, date(2026, 11, 5): 45.00, date(2026, 11, 10): 60.00,
             date(2026, 11, 12): 25.00, date(2026, 11, 17): 80.00}
RET_FARES = {date(2026, 11, 6): 50.00, date(2026, 11, 7): 15.99, date(2026, 11, 8): 40.00,
             date(2026, 11, 13): 55.00, date(2026, 11, 14): 65.00, date(2026, 11, 15): 30.00,
             date(2026, 11, 16): 20.00, date(2026, 11, 17): 35.00, date(2026, 11, 20): 70.00}

REF = {"dest_id": "-406131", "search_type": "city", "kind": "city", "name": "Valencia", "match_name": ""}


def card(name, stars, review, ppn, cur="EUR"):
    return {"type": "property_card", "hotel_name": name, "class": stars, "review_score": review,
            "composite_price_breakdown": {"gross_amount_per_night": {"value": ppn, "currency": cur}}}


def cards(promo_ppn):
    return [card("Promo Hotel", 4, 8.6, promo_ppn), card("Hotel B", 3, 8.2, 95),
            card("Hotel C", 4, 8.8, 110), card("Hotel D", 4, 9.0, 120),
            card("Hotel E", 5, 9.1, 180), card("Hotel F", 3, 8.4, 100),
            card("Too Few Stars", 2, 9.5, 40),      # excluded by stars
            card("Bad Reviews", 4, 7.5, 45),        # excluded by review
            card("Dollar Hotel", 4, 8.5, 70, "USD")]  # excluded by currency


calls = {"resolve": 0, "list": []}


def fake_fetch(origin, dest, month_start):
    if month_start == NOV and (origin, dest) == ("SOF", "VLC"):
        return dict(OUT_FARES)
    if month_start == NOV and (origin, dest) == ("VLC", "SOF"):
        return dict(RET_FARES)
    return {}


def fake_fetch_alicante_down(origin, dest, month_start):
    if "ALC" in (origin, dest):
        raise requests.ConnectionError("boom")
    return fake_fetch(origin, dest, month_start)


def fake_resolve(d):
    calls["resolve"] += 1
    return dict(REF)


def fake_list(ref, chk_in, chk_out, order_by=None):
    calls["list"].append((chk_in, chk_out, order_by))
    return cards(52 if chk_in == "2026-11-03" else 90)


def llm_result(text, ok=True):
    return L.LLMResult(text=text, ok=ok, model="m", provider="p", fell_back=False,
                       grounded=False, truncated=False, attempts=1, error="" if ok else "down")


orig = (S.fetch_calendar, P.resolve_hotel, P.list_properties, L.call_llm)
try:
    S.fetch_calendar = fake_fetch
    P.resolve_hotel = fake_resolve
    P.list_properties = fake_list

    # ---- months_between ----
    assert S.months_between(date(2026, 10, 29), date(2027, 1, 2)) == [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]

    # ---- build_combos / pick_windows ----
    start, end = date(2026, 10, 29), date(2026, 12, 28)
    combos = S.build_combos(OUT_FARES, RET_FARES, start, end, C.SPAIN_NIGHTS)
    assert len(combos) == 11, len(combos)
    assert sorted(c["rt_pp"] for c in combos) == [35.98, 45.0, 55.0, 59.99, 60.0, 69.99,
                                                  85.0, 90.0, 115.0, 125.0, 150.0]
    picked = S.pick_windows(combos, 2)
    assert [(c["out"], c["nights"], c["rt_pp"]) for c in picked] == [
        (date(2026, 11, 3), 4, 35.98), (date(2026, 11, 12), 4, 45.0)], picked

    # ---- qualifying_rates ----
    rates = S.qualifying_rates(cards(52))
    assert len(rates) == 6, rates
    assert {r["name"] for r in rates} == {"Promo Hotel", "Hotel B", "Hotel C", "Hotel D", "Hotel E", "Hotel F"}

    # ---- evaluate_window (pure) ----
    combo = picked[0]
    d = S.evaluate_window("Valencia", "VLC", combo, rates, 69.99)
    assert d["hotel_name"] == "Promo Hotel" and d["hotel_ppn_eur"] == 52
    assert d["hotel_p25_ppn_eur"] == 96.25 and d["hotel_sample"] == 6
    assert d["trip_variable_eur"] == 315.94, d["trip_variable_eur"]
    assert d["typical_variable_eur"] == 594.97, d["typical_variable_eur"]
    assert round(d["ratio"], 3) == 0.531, d["ratio"]
    assert d["tier"] == "good"
    assert d["allin_total_eur"] == 485.94 and d["typical_allin_eur"] == 764.97
    assert d["flight_family_eur"] == 107.94 and d["hotel_total_eur"] == 208.0
    assert d["fixed_eur"] == 170 and d["window"] == "Nov 3-7, 2026"
    assert d["checkin"] == "2026-11-03" and d["checkout"] == "2026-11-07" and d["nights"] == 4
    assert d["realert_key"].startswith("spain:") and d["realert_price_eur"] == d["allin_total_eur"]
    assert d["destination"] == "Valencia · Promo Hotel"
    assert "dateOut=2026-11-03&dateIn=2026-11-07" in d["flight_url"] and d["flight_url"].endswith("destinationIata=VLC")
    assert d["hotel_url"].startswith("https://www.booking.com/searchresults.html?ss=Promo+Hotel")
    assert d["about"] == d["value_case"] == d["red_flags"] == ""
    json.dumps(d)

    cheap = S.qualifying_rates([card("Promo Hotel", 4, 8.6, 30)] + cards(52)[1:])
    d2 = S.evaluate_window("Valencia", "VLC", combo, cheap, 69.99)
    assert d2["ratio"] == 0.3831 and d2["tier"] == "diamond", (d2["ratio"], d2["tier"])

    assert S.evaluate_window("Valencia", "VLC", combo, rates[:5], 69.99) is None

    cross = {"out": date(2026, 11, 28), "ret": date(2026, 12, 2), "nights": 4,
             "out_eur": 20.0, "ret_eur": 20.0, "rt_pp": 40.0}
    assert S.evaluate_window("Valencia", "VLC", cross, rates, 69.99)["window"] == "Nov 28-Dec 2, 2026"

    # ---- run_sweep ----
    mem = {"baselines": {}, "ledger": [], "spain_dest": {}}
    result = S.run_sweep(mem, TODAY)
    assert result["error"] == "", result["error"]
    assert len(result["deals"]) == 1, len(result["deals"])
    assert result["flights_ok"] == 1 and result["hotels_ok"] == 2
    assert len(result["windows"]) == 2
    by_city = {c["city"]: c for c in result["cities"]}
    assert len(result["cities"]) == len(C.SPAIN_CITIES)
    assert by_city["Valencia"]["status"] == "ok"
    assert by_city["Valencia"]["fare_days"] == 5 and by_city["Valencia"]["combos"] == 11
    assert by_city["Valencia"]["windows_priced"] == 2
    assert all(c["status"] == "no direct Ryanair flights" for n, c in by_city.items() if n != "Valencia")
    assert mem["spain_dest"]["Valencia"] == {"dest_id": "-406131", "search_type": "city",
                                             "kind": "city", "name": "Valencia"}
    assert calls["resolve"] == 1
    assert calls["list"] and all(c[2] == "popularity" for c in calls["list"])
    assert [round(w["ratio"], 3) for w in result["windows"]] == [0.531, 0.832]
    assert [w["tier"] for w in result["windows"]] == ["good", "none"]
    assert result["deals"][0]["tier"] == "good"
    json.dumps(result)
    S.run_sweep(mem, TODAY)
    assert calls["resolve"] == 1, "second sweep must use the cached city ref"

    # ---- one city's flight failure does not stop the others ----
    S.fetch_calendar = fake_fetch_alicante_down
    r2 = S.run_sweep({"baselines": {}, "ledger": [], "spain_dest": {}}, TODAY)
    st = {c["city"]: c["status"] for c in r2["cities"]}
    assert st["Alicante"].startswith("flight lookup failed"), st["Alicante"]
    assert st["Valencia"] == "ok" and len(r2["deals"]) == 1
    S.fetch_calendar = fake_fetch

    # ---- add_dossiers ----
    deal = result["deals"][0]
    X.STAGE_RESULTS.clear()
    L.call_llm = lambda *a, **k: llm_result(json.dumps(
        [{"key": deal["realert_key"], "about": "A", "value_case": "B", "red_flags": "", "ratio": 0.01}]))
    S.add_dossiers(result["deals"], "2026-10-19")
    assert deal["about"] == "A" and deal["value_case"] == "B" and deal["red_flags"] == ""
    assert round(deal["ratio"], 3) == 0.531, "Invariant SP: LLM must not move a number"
    assert len(X.STAGE_RESULTS) == 1 and X.STAGE_RESULTS[0][0] == "SPAIN"

    deal["about"] = ""
    L.call_llm = lambda *a, **k: llm_result("", ok=False)
    S.add_dossiers(result["deals"], "2026-10-19")
    assert deal["about"] == ""
    L.call_llm = lambda *a, **k: llm_result("not json at all")
    S.add_dossiers(result["deals"], "2026-10-19")
    assert deal["about"] == ""
    S.add_dossiers([], "2026-10-19")  # no-op, no LLM call
    X.STAGE_RESULTS.clear()
finally:
    S.fetch_calendar, P.resolve_hotel, P.list_properties, L.call_llm = orig
    X.STAGE_RESULTS.clear()

print("All Spain assertions passed.")
