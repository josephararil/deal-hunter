"""Offline unit tests for providers.py (Booking.com / apidojo grounding).

Monkey-patches providers._get with canned apidojo JSON so no network calls are made.
Non-destructive: snapshots and restores state/ files on entry/exit.

Run: python test_providers.py

Separately verify that the _ground_llm seam still works:
  HOTEL_PROVIDER="" python test_stub.py
When HOTEL_PROVIDER is empty, _resolve_ground_deal() returns _ground_llm directly,
so all 5 stub llm() calls in test_stub.py happen via the LLM path unchanged.
"""

import os, sys

os.makedirs("state", exist_ok=True)

# ── Snapshot state files ──────────────────────────────────────────────────────
_STATE_FILES = [
    os.path.join("state", f)
    for f in ("signals_seen.json", "city_signals.json", "city_signals.md",
              "memory.json", "memory.md")
]
_snapshots = {}
for _sf in _STATE_FILES:
    if os.path.exists(_sf):
        with open(_sf, encoding="utf-8") as _fh:
            _snapshots[_sf] = _fh.read()
    else:
        _snapshots[_sf] = None

_real_ground_llm = None
_failed = []

try:
    import requests
    import config as C
    import providers as P
    import find_city_anomalies as fa

    C.RAPIDAPI_KEY = "dummy-test-key"
    _real_ground_llm = fa._ground_llm

    # ── Canned API data ───────────────────────────────────────────────────────

    # /locations/auto-complete: list with both a city and a matching landmark
    _AC_WITH_LANDMARK = [
        {"dest_id": "city-1001", "dest_type": "city",     "name": "Bansko",              "country": "Bulgaria"},
        {"dest_id": "lmk-2001",  "dest_type": "landmark", "name": "Regnum Hotel Bansko",  "country": "Bulgaria"},
    ]
    # /locations/auto-complete: city only, no hotel/landmark entry
    _AC_CITY_ONLY = [
        {"dest_id": "city-1001", "dest_type": "city", "name": "Bansko", "country": "Bulgaria"},
    ]
    # /properties/v2/list: Regnum + Kempinski cards
    _PROPS = {
        "result": [
            {
                "type": "property_card",
                "hotel_name": "Regnum Hotel Bansko",
                "class": 5, "review_score": 8.7,
                "composite_price_breakdown": {
                    "gross_amount_per_night": {"value": 85.0, "currency": "EUR"},
                    "gross_amount":           {"value": 170.0, "currency": "EUR"},
                },
                "min_total_price": 170.0,
            },
            {
                "type": "property_card",
                "hotel_name": "Kempinski Grand Arena Bansko",
                "class": 5, "review_score": 8.9,
                "composite_price_breakdown": {
                    "gross_amount_per_night": {"value": 160.0, "currency": "EUR"},
                    "gross_amount":           {"value": 320.0, "currency": "EUR"},
                },
                "min_total_price": 320.0,
            },
        ]
    }
    # /properties/v2/list: Kempinski only — Regnum is absent
    _PROPS_NO_REGNUM = {
        "result": [
            {
                "type": "property_card",
                "hotel_name": "Kempinski Grand Arena Bansko",
                "class": 5, "review_score": 8.9,
                "composite_price_breakdown": {
                    "gross_amount_per_night": {"value": 160.0, "currency": "EUR"},
                    "gross_amount":           {"value": 320.0, "currency": "EUR"},
                },
                "min_total_price": 320.0,
            },
        ]
    }

    def _get_with_landmark(path, params):
        if path == "/locations/auto-complete":
            return _AC_WITH_LANDMARK
        return _PROPS

    def _get_city_only(path, params):
        if path == "/locations/auto-complete":
            return _AC_CITY_ONLY
        return _PROPS

    def _get_empty_ac(path, params):
        if path == "/locations/auto-complete":
            return []
        return _PROPS

    def _get_http_error(path, params):
        raise requests.RequestException("Simulated HTTP 503")

    def _get_no_regnum(path, params):
        if path == "/locations/auto-complete":
            return _AC_WITH_LANDMARK
        return _PROPS_NO_REGNUM

    # ── Stub for _ground_llm ──────────────────────────────────────────────────

    _LLM_FALLBACK = {
        "verdict": "confirm", "options": [], "confidence": "high",
        "assistant_summary": "LLM fallback", "how_to_book": "", "grounding": "",
    }

    def _stub_llm(diamond, mem_text, today):
        _stub_llm.calls += 1
        return _LLM_FALLBACK

    _stub_llm.calls = 0

    # ── Assertion helpers ─────────────────────────────────────────────────────

    def ok(name):
        print(f"  [OK] {name}")

    def chk(name, cond, detail=""):
        if cond:
            ok(name)
        else:
            msg = f"  [FAIL] {name}" + (f": {detail}" if detail else "")
            print(msg)
            _failed.append(name)

    # ── Tests ─────────────────────────────────────────────────────────────────

    print("\n=== test_providers.py ===\n")

    # 1. resolve_hotel picks a landmark/hotel result for a hotel-name query
    P._get = _get_with_landmark
    C.HOTEL_MAPPING = {}
    ref = P.resolve_hotel("Regnum Hotel, Bulgaria")
    chk("resolve_hotel ->landmark for hotel-name query",
        ref is not None
        and ref.get("search_type") in ("hotel", "landmark")
        and "Regnum" in ref.get("name", ""),
        f"got: {ref}")

    # 2. resolve_hotel falls back to city when no hotel/landmark matches
    P._get = _get_city_only
    ref_city = P.resolve_hotel("Bansko, Bulgaria")
    chk("resolve_hotel ->city fallback for city-name query",
        ref_city is not None
        and ref_city.get("search_type") == "city"
        and ref_city.get("kind") == "city",
        f"got: {ref_city}")

    # 3. resolve_hotel rejects entries whose country doesn't match the hint
    P._get = _get_with_landmark   # data has Bulgaria; query hints Greece
    ref_mm = P.resolve_hotel("Regnum Hotel, Greece")
    chk("resolve_hotel ->rejects country mismatch",
        ref_mm is None, f"expected None, got {ref_mm}")

    # 4. HOTEL_MAPPING short-circuits /locations/auto-complete entirely
    _get_calls: list = []
    P._get = lambda path, params: _get_calls.append(path) or _get_with_landmark(path, params)
    C.HOTEL_MAPPING = {"regnum bansko": {"dest_id": "99999", "search_type": "city", "name": "Regnum Custom"}}
    ref_map = P.resolve_hotel("regnum bansko")
    P._get = _get_with_landmark
    C.HOTEL_MAPPING = {}
    chk("HOTEL_MAPPING short-circuits auto-complete",
        ref_map is not None
        and ref_map.get("dest_id") == "99999"
        and len(_get_calls) == 0,
        f"dest_id={ref_map and ref_map.get('dest_id')!r}, _get_calls={_get_calls}")

    # 5. price() fuzzy-matches hotel name and reads gross_amount_per_night.value as EUR/night
    P._get = _get_with_landmark
    ref_pp = {"dest_id": "lmk-2001", "search_type": "landmark", "name": "Regnum Hotel", "kind": "specific"}
    rate = P.price(ref_pp, "2026-08-08", "2026-08-10")
    chk("price() ->fuzzy-match; reads gross_amount_per_night.value",
        rate is not None
        and rate["price_per_night_eur"] == 85.0
        and rate["nights"] == 2
        and rate["source"] == "Booking.com (apidojo)",
        f"got: {rate}")

    # 6. price() returns None when the named hotel is absent from the listing
    P._get = _get_no_regnum   # listing has Kempinski only
    rate_none = P.price(ref_pp, "2026-08-08", "2026-08-10")
    chk("price() ->None when named hotel absent from listing",
        rate_none is None, f"got: {rate_none}")

    # 7–9. _decide_verdict: confirm / correct / over-ceiling kill
    P._get = _get_with_landmark   # restore for subsequent calls

    v, conf = P._decide_verdict(80.0, 85.0, 100)
    chk("_decide_verdict ->confirm (g <= est*1.15)",
        v == "confirm" and conf == "high", f"got ({v!r}, {conf!r})")

    v, conf = P._decide_verdict(100.0, 85.0, 130)
    chk("_decide_verdict ->correct (g > est*1.15, g <= ceiling)",
        v == "correct" and conf == "high", f"got ({v!r}, {conf!r})")

    v, conf = P._decide_verdict(110.0, 85.0, 100)
    chk("_decide_verdict ->kill (over ceiling)",
        v == "kill" and conf == "high", f"got ({v!r}, {conf!r})")

    # 10–11. _to_stage3 output passes _dates_in_window; options[].dates has a 4-digit year
    ref_s3 = {"name": "Regnum Hotel", "dest_id": "lmk-2001", "search_type": "landmark", "kind": "specific"}
    rate_s3 = {
        "name": "Regnum Hotel Bansko", "checkin": "2026-08-08", "checkout": "2026-08-10",
        "nights": 2, "price_per_night_eur": 85.0, "total_eur": 170.0,
        "booking_url": "https://www.booking.com/hotel/bg/regnum.html",
        "source": "Booking.com (apidojo)", "review_score": 8.7, "stars": 5,
    }
    r = P._to_stage3(rate_s3, "confirm", "high", ref_s3, "2026-06-29")
    opt_dates = (r.get("options") or [{}])[0].get("dates", "")
    chk("_to_stage3 ->options[0].dates contains 4-digit year",
        "2026" in opt_dates, f"dates={opt_dates!r}")
    chk("_to_stage3 ->options[0].dates passes _dates_in_window",
        fa._dates_in_window(opt_dates, "Aug 2026"), f"dates={opt_dates!r} vs 'Aug 2026'")

    _diamond = {"destination": "Regnum Hotel, Bulgaria", "est_price_eur": 85, "window": "Aug 2026"}

    # 12. Fallback to _ground_llm when RAPIDAPI_KEY is empty
    fa._ground_llm = _stub_llm
    _stub_llm.calls = 0
    C.RAPIDAPI_KEY = ""
    P._get = _get_with_landmark
    result = P.ground_api(_diamond, "memory", "2026-06-29")
    C.RAPIDAPI_KEY = "dummy-test-key"
    fa._ground_llm = _real_ground_llm
    chk("ground_api ->LLM fallback on empty RAPIDAPI_KEY",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}, result={result!r}")

    # 13. Fallback when resolve_hotel returns None (empty AC list)
    fa._ground_llm = _stub_llm
    _stub_llm.calls = 0
    P._get = _get_empty_ac
    result = P.ground_api(_diamond, "memory", "2026-06-29")
    P._get = _get_with_landmark
    fa._ground_llm = _real_ground_llm
    chk("ground_api ->LLM fallback when resolve_hotel returns None",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # 14. Fallback when named hotel is absent from listing (price() returns None)
    fa._ground_llm = _stub_llm
    _stub_llm.calls = 0
    P._get = _get_no_regnum   # AC resolves Regnum landmark, but props list has no Regnum
    result = P.ground_api(_diamond, "memory", "2026-06-29")
    P._get = _get_with_landmark
    fa._ground_llm = _real_ground_llm
    chk("ground_api ->LLM fallback when hotel absent from listing",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # 15. Fallback on HTTP error from _get
    fa._ground_llm = _stub_llm
    _stub_llm.calls = 0
    P._get = _get_http_error
    result = P.ground_api(_diamond, "memory", "2026-06-29")
    P._get = _get_with_landmark
    fa._ground_llm = _real_ground_llm
    chk("ground_api ->LLM fallback on HTTP error",
        _stub_llm.calls > 0 and result.get("assistant_summary") == "LLM fallback",
        f"calls={_stub_llm.calls}")

    # ── Summary ───────────────────────────────────────────────────────────────
    total = 15
    passed = total - len(_failed)
    print(f"\n{passed}/{total} tests passed.")
    if _failed:
        print(f"FAILED: {_failed}")
        sys.exit(1)

finally:
    # Restore _ground_llm if it was patched but the restore line didn't run
    try:
        if _real_ground_llm is not None:
            import find_city_anomalies as _fa
            _fa._ground_llm = _real_ground_llm
    except Exception:
        pass

    # Restore state files
    for _sf, _content in _snapshots.items():
        if _content is None:
            if os.path.exists(_sf):
                os.remove(_sf)
        else:
            with open(_sf, "w", encoding="utf-8") as _fh:
                _fh.write(_content)
    print("State files restored.")
