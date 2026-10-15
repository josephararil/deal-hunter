"""
Stub verification for the diamond finder pipeline (deterministic scoring model).

Runs the real pipeline in a throwaway temp directory (non-destructive) with:
  - common.llm stubbed by response_schema — Stage 1 FIND candidates, Stage 3 scorer scores.
  - find_city_anomalies.ground_deal stubbed per destination — live grounding results.

Coverage:
  - Discount-vs-normal scoring: DIAMOND requires a standout LLM score AND a genuine discount
    below the property's own normal rate. Kempinski (normal €180) grounds at €85 → big
    discount → DIAMOND; Antalya (normal €150) grounds at €70 → DIAMOND.
  - No hard ceiling: Kempinski (FIND est €158) is NOT gate-dropped; it grounds at €85.
  - Regnum grounds at €112 vs an €80 normal → priced ABOVE normal → uncapped penalty → SKIP.
  - Neutral no-reference fallback + diamond bar: an ordinary Hisarya spa weekend (no
    normal_price_eur given) grounds at a fair €95 and lands in SKIP: with no reference price
    the pipeline stays neutral (discount 0.0, price_adj 0) rather than substituting a
    regional par (which used to manufacture a false discount/diamond), and a 0.0 discount is
    below GOOD_MIN_DISCOUNT, so it cannot be "good" either.
  - No wildcard badge: the wildcard feature is deleted.
  - Arte Spa: grounding KILL (hallucination) → dropped before scoring.
  - Sofia: grounding low-confidence → data-quality guard blocks it before scoring.
  - Deterministic tiers: final = llm + price_adj + transit_adj.
  - Scores recorded in memory (llm_score/final_score) for every scored candidate.
  - Email digest shows EVERY scored candidate (diamond/good/skip) with its score breakdown,
    plus a "seen & dropped" footer for grounding kills (Arte) and guard blocks (Sofia).
  - Email digest: tier badges, baseline comparison, child-price caveat.

Run: python test_stub.py
"""

import json, os, sys, tempfile, shutil, datetime as dt

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

_cwd = os.getcwd()
sandbox = tempfile.mkdtemp(prefix="dh_stub_")
os.makedirs(os.path.join(sandbox, "state"))
os.chdir(sandbox)

# Seed a prior Antalya baseline so the email "typically ~€X/night" line renders.
with open("state/memory.json", "w", encoding="utf-8") as f:
    # Keyed by memory.baseline_key (identity+season) — matching both _baseline_note's read
    # path and M.record_baseline's write path in find_city_anomalies.py. Antalya's
    # hotel_name is "Rixos Premium Antalya" (see _STAGE1 below), so its identity is
    # "rixospremiumantalya" (memory.identity lowercases + strips punctuation).
    json.dump({"baselines": {"rixospremiumantalya|2027-01": {
        "realistic_price_eur": 100, "note": "seeded",
        "source": "Booking.com (apidojo) live 2026-06-01",
        "updated": "2026-12-01"}}, "ledger": []}, f)
for _name, _seed in [("signals_seen.json", {"seen": {}, "monthly_count": {}}),
                     ("city_signals.json", {})]:
    with open(f"state/{_name}", "w", encoding="utf-8") as f:
        json.dump(_seed, f)

import config as C
C.SPAIN_SWEEP_ENABLED = False   # the Spain sweep hits Ryanair; test_spain.py covers it offline
import common as X
import llm_chain as L
import memory as M
import find_city_anomalies as fa


def _as_chain(fn):
    """Adapt an old-style common.llm stub (messages -> str, or raise) to the
    llm_chain.call_llm contract (prompt -> LLMResult, never raises on a provider failure).

    A stub that raises RuntimeError stands for a provider outage, which under the new
    contract is LLMResult(ok=False), NOT an exception -- so the degraded-run assertions
    keep testing the same behaviour they always did. AssertionError still propagates:
    that means the test itself is wrong, not the provider."""
    def _call(prompt, *, stage="", max_tokens=4000, want_search=False, search_prompt=None,
              search_preamble=None, response_schema=None, provider=None,
              web_search_max_uses=6):
        messages = [{"role": "user", "content": prompt}]
        try:
            text = fn(messages, "stub-model", max_tokens, want_search, response_schema,
                      provider, search_prompt)
        except AssertionError:
            raise
        except Exception as exc:
            return L.LLMResult(text="", ok=False, model="stub-model", provider="stub",
                               fell_back=True, grounded=False, truncated=False,
                               attempts=3, error=f"{type(exc).__name__}: {exc}")
        return L.LLMResult(text=text, ok=True, model="stub-model", provider="stub",
                           fell_back=False, grounded=False, truncated=False,
                           attempts=1, error="")
    return _call

# ── Canned Stage 1 (FIND) ─────────────────────────────────────────────────────
_STAGE1 = {"candidates": [
    {"destination": "Antalya 5-Star All-Inclusive", "hotel_name": "Rixos Premium Antalya",
     "city": "Antalya", "country": "Turkey", "score": 88, "type": "hotel",
     "window": "Jan 10-14, 2027", "est_price_eur": 98,
     "reason": "5-star AI, indoor pools + kids club open in January.", "confidence": "high"},
    {"destination": "Kempinski Hotel Grand Arena, Bansko, Bulgaria", "hotel_name": "Kempinski Grand Arena",
     "city": "Bansko", "country": "Bulgaria", "score": 83, "type": "hotel",
     "window": "Jul 2026", "est_price_eur": 158,
     "reason": "5-star ski resort, spa open year-round.", "confidence": "high"},
    # city is Pamporovo (not Bansko) so the per-city cap keeps both Regnum and Kempinski;
    # the label and hotel_name stay "Bansko" because they key _GROUND and the ledger asserts.
    {"destination": "Regnum Bansko, Bulgaria", "hotel_name": "Regnum Bansko", "city": "Pamporovo",
     "country": "Bulgaria", "score": 84, "type": "hotel", "window": "Aug 8-10, 2026",
     "est_price_eur": 84, "reason": "Luxury alpine resort, indoor pool.", "confidence": "high"},
    {"destination": "Arte Spa & Park, Velingrad, Bulgaria", "hotel_name": "Arte Spa Park",
     "city": "Velingrad", "country": "Bulgaria", "score": 81, "type": "hotel",
     "window": "Jul 15-18, 2026", "est_price_eur": 80,
     "reason": "Thermal spa package.", "confidence": "medium"},
    {"destination": "Sofia City Break, Bulgaria", "hotel_name": "Sofia Balkan Palace",
     "city": "Sofia", "country": "Bulgaria", "score": 82, "type": "hotel",
     "window": "Sep 5-7, 2026", "est_price_eur": 90,
     "reason": "City weekend.", "confidence": "medium"},
    {"destination": "Hisarya Ultra-Local Thermal Retreat", "hotel_name": "Sana Spa Hotel",
     "city": "Hisarya", "country": "Bulgaria", "score": 81, "type": "hotel",
     "window": "Aug 28-31, 2026", "est_price_eur": 100,
     "reason": "Ordinary local spa weekend.", "confidence": "high"},
]}

# ── Canned Stage 3 (SCORER) — desirability scores, price held neutral ─────────
_SCORES = [
    {"deal_id": 1, "destination": "Antalya 5-Star All-Inclusive", "score": 86,
     "why": "Standout AI resort, high in-window utility.",
     "about": "Rixos Premium is a flagship all-inclusive on the Antalya coast with indoor pools and a kids' club.",
     "value_case": "€70/night AI for a 5-star is exceptional — these rooms trade at €150+ in summer.",
     "normal_price_eur": 150,
     "red_flags": "Confirm kids club Jan hours."},
    {"deal_id": 2, "destination": "Kempinski Hotel Grand Arena, Bansko, Bulgaria", "score": 90,
     "why": "Genuinely special 5-star property with full family spa.",
     "about": "The Kempinski is Bansko's landmark ski-in/ski-out 5-star, with a large spa and family pools.",
     "value_case": "€85/night for the top hotel in town is a steal — it usually sits near €180 in season.",
     "normal_price_eur": 180,
     "red_flags": "Confirm pool heating."},
    {"deal_id": 3, "destination": "Regnum Bansko, Bulgaria", "score": 80,
     "why": "Comfortable resort, pleasant but not exceptional.",
     "about": "Regnum is a solid mid-upper Bansko resort with an indoor pool.",
     "value_case": "At €112/night it is priced above the Bansko norm of ~€80 — no real discount here.",
     "normal_price_eur": 80,
     "red_flags": "Check August weekend rates."},
    # Arte is a grounding kill and Sofia is guard-blocked, so they never reach the scorer;
    # include them anyway to prove the pipeline ignores scores for non-scored candidates.
    {"deal_id": 4, "destination": "Arte Spa & Park, Velingrad, Bulgaria", "score": 70, "why": "x", "red_flags": "x"},
    {"deal_id": 5, "destination": "Sofia City Break, Bulgaria", "score": 75, "why": "x", "red_flags": "x"},
    # Ordinary frictionless local spa weekend at a fair (not discounted) price. The label
    # deliberately OMITS the country and NO normal_price_eur is given — this exercises both
    # the country-aware par fallback (must use the €80 Bulgaria par via city/country, not the
    # €110 default) and the discount gate. Under the old model this auto-diamonded; now it
    # must land in GOOD, not diamond.
    {"deal_id": 6, "destination": "Hisarya Ultra-Local Thermal Retreat", "score": 82,
     "why": "Pleasant frictionless local spa weekend, but ordinary and fairly priced.",
     "about": "Sana Spa is a solid 4-star thermal hotel in Hisarya with family mineral pools.",
     "value_case": "€95/night is about the usual Hisarya rate — a fair price, not a real steal.",
     "red_flags": "Confirm child pool hours."},
]

def _stub_llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
              provider=None, search_prompt=None):
    if response_schema is C.STAGE1_RESPONSE_SCHEMA:
        print("  [stub] llm: Stage 1 FIND")
        return json.dumps(_STAGE1)
    if response_schema is C.STAGE2_RESPONSE_SCHEMA:
        print("  [stub] llm: Stage 3 SCORER")
        return json.dumps(_SCORES)
    raise AssertionError(f"unexpected llm schema={response_schema}")

def _opt(ppn, total, dates, nights):
    return {"dates": dates, "nights": nights, "price_per_night_eur": ppn, "total_eur": total,
            "booking_url": "https://www.booking.com/hotel/x.html",
            "source": "Booking.com (apidojo) live 2026-06-28"}

_GROUND = {
    "Antalya 5-Star All-Inclusive": {"destination": "Rixos Premium Antalya", "verdict": "correct",
        "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Rixos Premium Antalya, Jan 10-14: €70/night (€280 total).",
        "options": [_opt(70, 280, "Jan 10-14, 2027", 4)]},
    "Kempinski Hotel Grand Arena, Bansko, Bulgaria": {"destination": "Kempinski Grand Arena",
        "verdict": "correct", "confidence": "high", "grounding_method": "apidojo",
        "how_to_book": "Book at booking.com",
        "grounding": "apidojo live", "assistant_summary": "Kempinski Grand Arena, Jul 10-13: €85/night.",
        "options": [_opt(85, 255, "Jul 10-13, 2026", 3)]},
    "Regnum Bansko, Bulgaria": {"destination": "Regnum Bansko", "verdict": "correct",
        "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Regnum Bansko, Aug 8-10: €112/night.",
        "options": [_opt(112, 224, "Aug 8-10, 2026", 2)]},
    # Grounding kill (hallucination) → dropped before scoring.
    "Arte Spa & Park, Velingrad, Bulgaria": {"destination": "Arte Spa Park", "verdict": "kill",
        "confidence": "high", "options": [], "how_to_book": "", "grounding": "overpriced for market",
        "assistant_summary": "At €165/night, top of the Velingrad market — no arbitrage."},
    # Low-confidence grounding → data-quality guard blocks it before scoring.
    "Sofia City Break, Bulgaria": {"destination": "Sofia Balkan Palace", "verdict": "correct",
        "confidence": "low", "how_to_book": "", "grounding": "search returned no firm rate",
        "assistant_summary": "Could not verify a firm Sofia rate.",
        "options": [_opt(88, 176, "Sep 5-7, 2026", 2)]},
    # Ordinary local spa at a fair price → SKIP (no reference price → discount 0.0).
    "Hisarya Ultra-Local Thermal Retreat": {"destination": "Sana Spa Hotel", "verdict": "confirm",
        "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Sana Spa Hotel, Aug 28-31: €95/night.",
        "options": [_opt(95, 285, "Aug 28-31, 2026", 3)]},
}
def _stub_ground(diamond, mem_text, today):
    return _GROUND.get(diamond.get("destination"), {})

_email = {}
def _stub_send(subject, html, text):
    _email["subject"], _email["html"], _email["text"] = subject, html, text

L.call_llm = _as_chain(_stub_llm)
fa.ground_deal = _stub_ground
X.send_email = _stub_send

try:
    print("\n=== Running stub test (scoring model) ===\n")
    fa.main()

    print("\n=== Assertions ===")
    assert _email, "send_email was never called — no diamonds/goods reached email"
    html, text = _email["html"], _email["text"]

    # Kempinski: standout property at €85 → DIAMOND (the key context-dependence demo).
    assert "Kempinski" in html, "Kempinski (diamond at €85) should be emailed"
    assert "💎 Diamond" in html, "diamond badge missing"
    # Antalya diamond too (cheap + standout).
    assert "Rixos" in html or "Antalya" in html, "Antalya diamond missing from email"
    assert "2 diamond" in _email["subject"], _email["subject"]
    print("Kempinski + Antalya emailed as diamonds [OK]")

    # Regnum: grounded €112 sinks to SKIP via price penalty (no ceiling), but the digest now
    # SHOWS skips (with their score breakdown) so the reader sees what the pipeline weighed.
    assert "Regnum" in html, "Regnum (skip) should now appear in the digest body"
    assert "· Skipped" in html, "skip badge missing"
    assert "→ <b>63</b>/100" in html, "Regnum score breakdown missing/incorrect"
    print("Regnum skip shown in digest with score breakdown [OK]")

    # Arte (grounding kill) and Sofia (guard block) appear in the 'seen & dropped' footer.
    assert "seen &amp; dropped" in html.lower(), "dropped footer missing"
    assert "Arte" in html and "killed" in html, "killed Arte missing from footer"
    assert "Sofia" in html and "blocked" in html, "guard-blocked Sofia missing from footer"
    print("Arte/Sofia shown in 'seen & dropped' footer [OK]")

    # Scorer dossier: 'about' description + 'value_case' callout reach the email (HTML + text).
    assert "landmark ski-in/ski-out 5-star" in html, "Kempinski 'about' description missing from email HTML"
    assert "Why it's a deal:" in html, "'Why it's a deal' value-case callout missing from email HTML"
    assert "usually sits near €180" in html, "Kempinski value_case text missing from email HTML"
    assert "Why it's a deal:" in text and "landmark ski-in/ski-out 5-star" in text, "dossier missing from plain-text email"
    print("Scorer dossier (about + value_case) rendered in email [OK]")

    # Baseline comparison + child caveat present.
    assert "30% under" in html, f"Antalya baseline comparison wrong: {html[html.find('Antalya'):html.find('Antalya')+300]}"
    assert "reconfirm the 4-year-old" in html, "child-price caveat missing"
    print("Baseline comparison + child caveat present [OK]")

    # Memory: scores recorded, verdicts reflect tiers, no over_ceiling.
    mem = json.load(open("state/memory.json", encoding="utf-8"))
    led = {e["destination"]: e for e in mem["ledger"]}
    assert led["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["verdict"] == "diamond"
    assert led["Regnum Bansko, Bulgaria"]["verdict"] == "skip", led["Regnum Bansko, Bulgaria"]
    assert led["Regnum Bansko, Bulgaria"]["final_score"] == 63, led["Regnum Bansko, Bulgaria"]
    assert led["Arte Spa & Park, Velingrad, Bulgaria"]["verdict"] == "kill"
    assert led["Sofia City Break, Bulgaria"]["verdict"] == "blocked"
    assert all(e.get("verdict") != "over_ceiling" for e in mem["ledger"]), "over_ceiling should be gone"
    assert led["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["llm_score"] == 90
    print("Memory ledger: tiers + scores recorded, no over_ceiling [OK]")

    # CORE REGRESSION: with no normal_price_eur given, the pipeline must stay NEUTRAL (no
    # discount, no par substitution) rather than comparing the grounded price against a
    # regional floor. Hisarya: llm_score 82, no normal_price_eur -> discount 0.0, price_adj 0;
    # Tier-1 (drivable) -> transit_adj +3; final = 82 + 0 + 3 = 85. It lands in SKIP: no
    # reference price -> discount 0.0 < GOOD_MIN_DISCOUNT 0.10 (and < DIAMOND_MIN_DISCOUNT).
    hisarya = led["Hisarya Ultra-Local Thermal Retreat"]
    assert hisarya["verdict"] == "skip", f"ordinary local spa must be SKIP not {hisarya['verdict']}: {hisarya}"
    assert hisarya["final_score"] == 85, f"expected neutral-fallback final 85, got {hisarya['final_score']}"
    print("Ordinary local spa scored SKIP (no discount) via neutral no-reference fallback [OK]")

    # Wildcard feature is deleted.
    assert "🃏" not in html, "wildcard badge must be gone from the digest"
    print("No wildcard badge in digest [OK]")

    # city_signals.json carries the full score breakdown.
    sig = {s["city"]: s for s in json.load(open("state/city_signals.json", encoding="utf-8"))["signals"]}
    assert sig["Regnum Bansko, Bulgaria"]["price_adj"] == -20, sig["Regnum Bansko, Bulgaria"]
    assert sig["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["tier"] == "diamond"
    print("city_signals.json: score breakdown present [OK]")

    md = open("state/city_signals.md", encoding="utf-8").read()
    assert "💎" in md and "final" in md.lower() and "over ceiling" not in md.lower()
    print("city_signals.md: scores shown, no ceiling language [OK]")

    # deals_history.json: one record per emailed deal (the browsable web/ UI's data source).
    hist = json.load(open("state/deals_history.json", encoding="utf-8"))["entries"]
    hist_by_dest = {e["destination"]: e for e in hist}
    assert len(hist) == 4, f"expected 4 emailed deals (Antalya/Kempinski/Regnum/Hisarya), got {len(hist)}"
    assert hist_by_dest["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["tier"] == "diamond"
    assert "landmark ski-in/ski-out 5-star" in hist_by_dest["Kempinski Hotel Grand Arena, Bansko, Bulgaria"]["about"]
    assert hist_by_dest["Regnum Bansko, Bulgaria"]["final_score"] == 63
    assert hist_by_dest["Antalya 5-Star All-Inclusive"]["options"][0]["price_per_night_eur"] == 70
    print("deals_history.json: emailed deals persisted with full dossier [OK]")

    print("\nAll assertions passed (baseline scoring model).")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox, ignore_errors=True)


# ── Part 2: new coverage for the outage-resilience / trip-log rewrite ─────────
#
# Each case below gets its own fresh sandbox (tempfile.mkdtemp + chdir + freshly seeded
# state/*.json) so state never leaks between passes. config/common/find_city_anomalies/
# memory are reused (already imported above) — only L.call_llm / fa.ground_deal / X.send_email
# and the seed files are swapped per pass.

def _seed_empty_state():
    """Seed state/ with empty-but-valid shapes, matching the top-of-file seed convention."""
    with open("state/memory.json", "w", encoding="utf-8") as f:
        json.dump({"baselines": {}, "ledger": []}, f)
    with open("state/signals_seen.json", "w", encoding="utf-8") as f:
        json.dump({"seen": {}, "monthly_count": {}}, f)
    with open("state/city_signals.json", "w", encoding="utf-8") as f:
        json.dump({}, f)


def _new_sandbox(prefix):
    box = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(box, "state"))
    os.chdir(box)
    _seed_empty_state()
    return box


print("\n=== Case 1: unscored, total Stage-3 failure ===\n")
sandbox1 = _new_sandbox("dh_stub_c1_")
try:
    _STAGE1_C1 = {"candidates": [
        {"destination": "Pamporovo Ski Break, Bulgaria", "hotel_name": "Perelik Hotel",
         "city": "Pamporovo", "country": "Bulgaria", "score": 85, "type": "hotel",
         "window": "Feb 10-13, 2027", "est_price_eur": 90,
         "reason": "Ski resort weekend.", "confidence": "high"},
    ]}

    def _stub_llm_c1(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                     provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            return json.dumps(_STAGE1_C1)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            raise RuntimeError("Simulated HTTP 503")
        raise AssertionError(f"unexpected llm schema={response_schema}")

    def _stub_ground_c1(diamond, mem_text, today):
        return {"destination": "Perelik Hotel", "verdict": "confirm", "confidence": "high",
                "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
                "grounding": "apidojo live",
                "assistant_summary": "Perelik Hotel, Feb 10-13: €75/night.",
                "options": [_opt(75, 225, "Feb 10-13, 2027", 3)]}

    _email_c1 = {}
    def _stub_send_c1(subject, html, text):
        _email_c1["subject"], _email_c1["html"], _email_c1["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c1)
    fa.ground_deal = _stub_ground_c1
    X.send_email = _stub_send_c1

    fa.main()

    mem1 = json.load(open("state/memory.json", encoding="utf-8"))
    led1 = mem1["ledger"]
    assert all(e.get("verdict") != "skip" for e in led1), \
        f"a total Stage-3 failure must never be coerced into a fabricated 'skip': {led1}"
    assert len(led1) == 1, led1
    row1 = led1[0]
    assert row1["verdict"] == "unscored", row1
    assert row1["llm_score"] is None, row1
    assert row1["final_score"] is None, row1

    hist1 = json.load(open("state/deals_history.json", encoding="utf-8"))
    assert hist1["entries"] == [], f"deals_history.json must stay empty when nothing was scored: {hist1['entries']}"

    assert _email_c1, "email should still be sent for a degraded run"
    assert _email_c1["subject"].startswith("⚠"), _email_c1["subject"]
    assert "pipeline degraded" in _email_c1["subject"], _email_c1["subject"]
    html1 = _email_c1["html"]
    for bad in ("💎 Diamond", "· Skipped", "/100"):
        assert bad not in html1, f"'{bad}' must not appear in an all-unscored digest"

    seen1 = json.load(open("state/signals_seen.json", encoding="utf-8"))
    assert any(v.get("tier") == "unscored" for v in seen1.get("props", {}).values()), seen1
    month_key = fa.this_month()
    assert seen1.get("monthly_count", {}).get(month_key) is None, \
        f"a fully-degraded send must not count toward the genuine-pick monthly total: {seen1.get('monthly_count')}"
    assert seen1.get("degraded_count", {}).get(month_key) == 1, seen1.get("degraded_count")

    print("Case 1: total Stage-3 failure -> unscored (never a fabricated skip), "
          "empty deals_history, degraded subject, degraded_count (not monthly_count) [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox1, ignore_errors=True)


print("\n=== Case 2: unscored, partial (scorer omits one deal_id) ===\n")
sandbox2 = _new_sandbox("dh_stub_c2_")
try:
    # Three different cities: the per-city cap would otherwise ground only the top-scored one.
    _STAGE1_C2 = {"candidates": [
        {"destination": "Bansko Weekend One, Bulgaria", "hotel_name": "Alpine Lodge One",
         "city": "Bansko", "country": "Bulgaria", "score": 82, "type": "hotel",
         "window": "Mar 5-8, 2027", "est_price_eur": 90,
         "reason": "Ski lodge.", "confidence": "high"},
        {"destination": "Bansko Weekend Two, Bulgaria", "hotel_name": "Alpine Lodge Two",
         "city": "Pamporovo", "country": "Bulgaria", "score": 83, "type": "hotel",
         "window": "Mar 5-8, 2027", "est_price_eur": 92,
         "reason": "Ski lodge.", "confidence": "high"},
        {"destination": "Bansko Weekend Three, Bulgaria", "hotel_name": "Alpine Lodge Three",
         "city": "Velingrad", "country": "Bulgaria", "score": 84, "type": "hotel",
         "window": "Mar 5-8, 2027", "est_price_eur": 94,
         "reason": "Ski lodge.", "confidence": "high"},
    ]}
    _SCORES_C2 = [
        {"deal_id": 1, "destination": "Bansko Weekend One, Bulgaria", "score": 80,
         "why": "Solid lodge.", "about": "A pleasant ski lodge.", "value_case": "Fair price.",
         "red_flags": "None."},
        {"deal_id": 2, "destination": "Bansko Weekend Two, Bulgaria", "score": 81,
         "why": "Solid lodge.", "about": "A pleasant ski lodge.", "value_case": "Fair price.",
         "red_flags": "None."},
        # deal_id 3 deliberately omitted — the scorer returned no verdict for it this run.
    ]

    def _stub_llm_c2(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                     provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            return json.dumps(_STAGE1_C2)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C2)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    _GROUND_C2 = {
        "Bansko Weekend One, Bulgaria": {"destination": "Alpine Lodge One", "verdict": "confirm",
            "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
            "grounding": "apidojo live", "assistant_summary": "Alpine Lodge One: €72/night.",
            "options": [_opt(72, 216, "Mar 5-8, 2027", 3)]},
        "Bansko Weekend Two, Bulgaria": {"destination": "Alpine Lodge Two", "verdict": "confirm",
            "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
            "grounding": "apidojo live", "assistant_summary": "Alpine Lodge Two: €78/night.",
            "options": [_opt(78, 234, "Mar 5-8, 2027", 3)]},
        "Bansko Weekend Three, Bulgaria": {"destination": "Alpine Lodge Three", "verdict": "confirm",
            "confidence": "high", "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
            "grounding": "apidojo live", "assistant_summary": "Alpine Lodge Three: €81/night.",
            "options": [_opt(81, 243, "Mar 5-8, 2027", 3)]},
    }
    def _stub_ground_c2(diamond, mem_text, today):
        return _GROUND_C2.get(diamond.get("destination"), {})

    _email_c2 = {}
    def _stub_send_c2(subject, html, text):
        _email_c2["subject"], _email_c2["html"], _email_c2["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c2)
    fa.ground_deal = _stub_ground_c2
    X.send_email = _stub_send_c2

    fa.main()

    mem2 = json.load(open("state/memory.json", encoding="utf-8"))
    led2 = {e["destination"]: e for e in mem2["ledger"]}
    for dest in ("Bansko Weekend One, Bulgaria", "Bansko Weekend Two, Bulgaria"):
        row = led2[dest]
        assert row["verdict"] in ("diamond", "good", "skip"), row
        assert isinstance(row["llm_score"], (int, float)), row
        assert isinstance(row["final_score"], (int, float)), row
    row3 = led2["Bansko Weekend Three, Bulgaria"]
    assert row3["verdict"] == "unscored", row3
    assert row3["llm_score"] is None, row3

    assert _email_c2, "email should have been sent"
    html2 = _email_c2["html"]
    assert any(badge in html2 for badge in ("💎 Diamond", "👍 Good find", "· Skipped")), \
        "candidates 1/2 should show a tier badge in the digest"
    assert "Priced but not scored" in html2, \
        "the unscored section heading is missing from the digest"

    print("Case 2: partial scorer response -> 1/2 tiered normally, "
          "3 (omitted deal_id) unscored, both sections shown in one digest [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox2, ignore_errors=True)


print("\n=== Case 3: Invariant Z — compute_final_score stays neutral with no reference price ===\n")
res_hisarya = C.compute_final_score(82, 95, "Hisarya Bulgaria", None)
assert res_hisarya == (85, 0, 3, 0.0), res_hisarya
res_chania = C.compute_final_score(0, 475.81, "Chania Greece", None)
assert res_chania[1] == 0, \
    f"price_adj must be 0 with no normal_price_eur (old par-fallback bug gave -166): {res_chania}"
print("Case 3: no normal_price_eur -> price_adj 0 (no regional-par substitution) [OK]")


print("\n=== Case 4: Invariant B — baseline provenance gate (apidojo only) ===\n")
sandbox4 = _new_sandbox("dh_stub_c4_")
try:
    _STAGE1_C4 = {"candidates": [
        {"destination": "Sunny Beach Getaway, Bulgaria", "hotel_name": "Marina Grand Beach Hotel",
         "city": "Sunny Beach", "country": "Bulgaria", "score": 82, "type": "hotel",
         "window": "Jun 5-8, 2026", "est_price_eur": 70,
         "reason": "Beach resort.", "confidence": "high"},
        {"destination": "Varna City Break, Bulgaria", "hotel_name": "Grand Hotel Varna",
         "city": "Varna", "country": "Bulgaria", "score": 83, "type": "hotel",
         "window": "Jun 5-8, 2026", "est_price_eur": 80,
         "reason": "City break.", "confidence": "high"},
    ]}
    _SCORES_C4 = [
        {"deal_id": 1, "destination": "Sunny Beach Getaway, Bulgaria", "score": 80,
         "why": "Fine beach resort.", "about": "A beach hotel.", "value_case": "Fair.",
         "red_flags": "None."},
        {"deal_id": 2, "destination": "Varna City Break, Bulgaria", "score": 81,
         "why": "Fine city hotel.", "about": "A city hotel.", "value_case": "Fair.",
         "red_flags": "None."},
    ]

    def _stub_llm_c4(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                     provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            return json.dumps(_STAGE1_C4)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C4)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    _GROUND_C4 = {
        # A: grounded via the LLM concierge fallback — must NEVER write a baseline.
        "Sunny Beach Getaway, Bulgaria": {"destination": "Marina Grand Beach Hotel",
            "verdict": "confirm", "confidence": "high", "grounding_method": "llm",
            "how_to_book": "Contact the hotel directly.", "grounding": "LLM concierge",
            "assistant_summary": "Marina Grand Beach Hotel: €65/night.",
            "options": [_opt(65, 195, "Jun 5-8, 2026", 3)]},
        # B: grounded via apidojo (live Booking.com) — must write a baseline.
        "Varna City Break, Bulgaria": {"destination": "Grand Hotel Varna",
            "verdict": "confirm", "confidence": "high", "grounding_method": "apidojo",
            "how_to_book": "Book at booking.com", "grounding": "apidojo live",
            "assistant_summary": "Grand Hotel Varna: €68/night.",
            "options": [_opt(68, 204, "Jun 5-8, 2026", 3)]},
    }
    def _stub_ground_c4(diamond, mem_text, today):
        return _GROUND_C4.get(diamond.get("destination"), {})

    _email_c4 = {}
    def _stub_send_c4(subject, html, text):
        _email_c4["subject"], _email_c4["html"], _email_c4["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c4)
    fa.ground_deal = _stub_ground_c4
    X.send_email = _stub_send_c4

    fa.main()

    mem4 = json.load(open("state/memory.json", encoding="utf-8"))
    baselines4 = mem4["baselines"]
    key_a = M.baseline_key({"hotel_name": "Marina Grand Beach Hotel", "window": "Jun 5-8, 2026"})
    key_b = M.baseline_key({"hotel_name": "Grand Hotel Varna", "window": "Jun 5-8, 2026"})
    assert key_b in baselines4, f"apidojo-grounded candidate B should have written a baseline: {baselines4}"
    assert key_a not in baselines4, f"LLM-grounded candidate A must NOT write a baseline: {baselines4}"

    print("Case 4: baseline written only for the apidojo-grounded candidate, "
          "never for the LLM-concierge-grounded one [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox4, ignore_errors=True)


print("\n=== Case 6: Invariant S — identity derivation is shared, not duplicated ===\n")
assert M.identity({"hotel_name": "SPA Hotel Olymp"}) == "spahotelolymp", \
    M.identity({"hotel_name": "SPA Hotel Olymp"})
assert fa._identity is M.identity, "find_city_anomalies._identity must alias memory.identity"
assert (M.identity_key({"hotel_name": "Hotel Arte SPA & Park"})
        == M.identity_key({"hotel_name": "Arte SPA and Park Hotel"})
        == "aaaadeehklnopprrstt"), M.identity_key({"hotel_name": "Hotel Arte SPA & Park"})
print("Case 6: memory.identity() normalizes as expected and find_city_anomalies._identity "
      "is a literal alias of it [OK]")


print("\n=== Case 7: Invariant P — est-gap flag is display-only, never a price gate ===\n")
sandbox7 = _new_sandbox("dh_stub_c7_")
try:
    _STAGE1_C7 = {"candidates": [
        {"destination": "Ohrid Lakeside Break, North Macedonia", "hotel_name": "Lake Ohrid Resort",
         "city": "Ohrid", "country": "North Macedonia", "score": 84, "type": "hotel",
         "window": "May 1-4, 2026", "est_price_eur": 100,
         "reason": "Lakeside resort.", "confidence": "high"},
    ]}
    _SCORES_C7 = [
        {"deal_id": 1, "destination": "Ohrid Lakeside Break, North Macedonia", "score": 80,
         "why": "Pleasant lakeside resort.", "about": "A lakeside resort.",
         "value_case": "Priced above the estimate but plausible for the season.",
         "red_flags": "Confirm the live rate before booking."},
    ]

    def _stub_llm_c7(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                     provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            return json.dumps(_STAGE1_C7)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C7)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    def _stub_ground_c7(diamond, mem_text, today):
        # Grounded at €150/night vs FIND's €100 estimate -> ratio 1.5x >= EST_GAP_FLAG_MULTIPLE (1.4).
        assert C.EST_GAP_FLAG_MULTIPLE == 1.4, C.EST_GAP_FLAG_MULTIPLE
        return {"destination": "Lake Ohrid Resort", "verdict": "confirm", "confidence": "high",
                "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
                "grounding": "apidojo live",
                "assistant_summary": "Lake Ohrid Resort: €150/night.",
                "options": [_opt(150, 450, "May 1-4, 2026", 3)]}

    _email_c7 = {}
    def _stub_send_c7(subject, html, text):
        _email_c7["subject"], _email_c7["html"], _email_c7["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c7)
    fa.ground_deal = _stub_ground_c7
    X.send_email = _stub_send_c7

    fa.main()

    mem7 = json.load(open("state/memory.json", encoding="utf-8"))
    row7 = mem7["ledger"][0]
    assert row7["destination"] == "Ohrid Lakeside Break, North Macedonia", row7
    assert row7["verdict"] in ("diamond", "good", "skip"), \
        f"a candidate with a large est-gap must still be scored/tiered normally: {row7}"

    assert _email_c7, "email should have been sent"
    html7 = _email_c7["html"]
    assert "× FIND's €" in html7, "the est-gap warning text is missing from the digest"

    print("Case 7: a live price far above FIND's estimate is flagged in the digest "
          "but still scored and emailed — never silently gated on price [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox7, ignore_errors=True)


print("\n=== Case 8 (+ Case 5) — trips: anchors, prompt injection, backtest, read-only ===\n")
sandbox8 = _new_sandbox("dh_stub_c8_")
try:
    trips_payload = {"trips": [
        {"hotel_name": "Sana Spa Hotel", "city": "Hisarya", "country": "Bulgaria",
         "checkin": "2026-09-18", "checkout": "2026-09-21", "total_paid": 293.28, "currency": "EUR"},
        # Malformed: missing the required "hotel_name" field — trips.valid_trips must drop
        # this entry (with a warning) and the pipeline must not crash on it.
        {"city": "Nowhere", "country": "Nowhereland", "checkin": "2026-01-01",
         "checkout": "2026-01-03", "total_paid": 100, "currency": "EUR"},
    ]}
    with open("state/trips.json", "w", encoding="utf-8") as f:
        json.dump(trips_payload, f)

    # Arithmetic sanity check for the well-formed trip: 3 nights, 293.28/3 = 97.76/night.
    assert (dt.date.fromisoformat("2026-09-21") - dt.date.fromisoformat("2026-09-18")).days == 3
    assert round(293.28 / 3, 2) == 97.76

    trips_path = os.path.join("state", "trips.json")
    with open(trips_path, "rb") as f:
        trips_bytes_before = f.read()

    _STAGE1_C8 = {"candidates": [
        {"destination": "Hisarya Retreat, Bulgaria", "hotel_name": "Sana Spa Hotel",
         "city": "Hisarya", "country": "Bulgaria", "score": 85, "type": "hotel",
         "window": "Sep 18-21, 2026", "est_price_eur": 95,
         "reason": "Thermal retreat.", "confidence": "high"},
    ]}
    _SCORES_C8 = [
        {"deal_id": 1, "destination": "Hisarya Retreat, Bulgaria", "score": 82,
         "why": "Pleasant retreat.", "about": "A thermal hotel.", "value_case": "Fair price.",
         "red_flags": "None."},
    ]

    _captured_prompt_c8 = {}
    def _stub_llm_c8(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                     provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            _captured_prompt_c8["text"] = messages[0]["content"]
            return json.dumps(_STAGE1_C8)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C8)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    def _stub_ground_c8(diamond, mem_text, today):
        return {"destination": "Sana Spa Hotel", "verdict": "confirm", "confidence": "high",
                "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
                "grounding": "apidojo live", "assistant_summary": "Sana Spa Hotel: €96/night.",
                "options": [_opt(96, 288, "Sep 18-21, 2026", 3)]}

    _email_c8 = {}
    def _stub_send_c8(subject, html, text):
        _email_c8["subject"], _email_c8["html"], _email_c8["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c8)
    fa.ground_deal = _stub_ground_c8
    X.send_email = _stub_send_c8

    fa.main()

    assert "Trips this family has actually booked" in _captured_prompt_c8.get("text", ""), \
        "the trips block must reach the FIND prompt (a present-but-empty {trips} slot would " \
        "silently drop this heading)"

    assert _email_c8, "email should have been sent"
    assert "Of 1 trip(s) you've logged" in _email_c8["html"], \
        "the back-test line is missing/wrong — only 1 of the 2 logged trips is well-formed"

    with open(trips_path, "rb") as f:
        trips_bytes_after = f.read()
    assert trips_bytes_after == trips_bytes_before, \
        "Invariant T violated: state/trips.json must never be written by the pipeline"

    print("Case 8: malformed trip entry dropped without crashing, well-formed trip reaches "
          "the FIND prompt and the digest's back-test line, state/trips.json left byte-identical "
          "(Invariant T) [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox8, ignore_errors=True)


# ── Case 9: prior_baselines must be a deep, not shallow, snapshot ──────────────────────────
# record_baseline (memory.py) mutates each baseline entry's dict IN PLACE via setdefault(),
# so a shallow {**mem["baselines"]} copy still shares the same nested dict objects — this
# run's own write would silently corrupt "prior_baselines" mid-run, comparing a deal against
# the very price it just recorded (self-comparison) instead of the real prior normal.
sandbox9 = tempfile.mkdtemp(prefix="dh_stub_")
os.makedirs(os.path.join(sandbox9, "state"))
os.chdir(sandbox9)
try:
    with open("state/memory.json", "w", encoding="utf-8") as f:
        json.dump({"baselines": {"hotelc9|2027-01": {
            "samples": [{"price": 100, "date": "2026-07-01",
                        "source": "Booking.com (apidojo) live 2026-07-01"}],
            "realistic_price_eur": 100, "note": "seeded",
            "source": "Booking.com (apidojo) live 2026-07-01",
            "updated": "2026-07-01"}}, "ledger": []}, f)
    for _name, _seed in [("signals_seen.json", {"seen": {}, "monthly_count": {}}),
                         ("city_signals.json", {})]:
        with open(f"state/{_name}", "w", encoding="utf-8") as f:
            json.dump(_seed, f)

    _STAGE1_C9 = {"candidates": [
        {"destination": "Case9 Hotel Deal", "hotel_name": "Hotel C9", "city": "Antalya",
         "country": "Turkey", "score": 88, "type": "hotel", "window": "Jan 10-14, 2027",
         "est_price_eur": 98, "reason": "x", "confidence": "high"},
    ]}
    _SCORES_C9 = [{"deal_id": 1, "destination": "Case9 Hotel Deal", "score": 90, "why": "x",
                   "about": "x", "value_case": "x", "normal_price_eur": 150, "red_flags": ""}]

    def _stub_llm_c9(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                      provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            return json.dumps(_STAGE1_C9)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C9)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    # Same identity ("hotelc9") re-grounds THIS run at a much lower price (70 vs the seeded
    # 100) — the exact shape that exposed the in-place-mutation bug.
    def _stub_ground_c9(diamond, mem_text, today):
        return {"destination": "Hotel C9", "verdict": "confirm", "confidence": "high",
                "grounding_method": "apidojo", "how_to_book": "Book at booking.com",
                "grounding": "apidojo live", "assistant_summary": "Hotel C9: €70/night.",
                "options": [_opt(70, 280, "Jan 10-14, 2027", 4)]}

    _email_c9 = {}
    def _stub_send_c9(subject, html, text):
        _email_c9["html"] = html

    L.call_llm = _as_chain(_stub_llm_c9)
    fa.ground_deal = _stub_ground_c9
    X.send_email = _stub_send_c9

    fa.main()

    assert _email_c9, "email should have been sent"
    assert "30% under" in _email_c9["html"], (
        "prior_baselines was corrupted mid-run (self-comparison bug): the email should "
        "compare €70 against the PRIOR €100 baseline (-30%), not the price this same run "
        "just wrote"
    )
    print("Case 9: prior_baselines snapshot survives this run's own record_baseline call "
          "(no self-comparison) [OK]")
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox9, ignore_errors=True)


# ── Case 10: STAGE_RESULTS is shared across BOTH module copies ────────────────
# CI runs `python find_city_anomalies.py`, so that module is __main__, while providers.py
# does a lazy `import find_city_anomalies as fa` to reach _ground_llm. Python therefore
# builds TWO module objects with separate globals. If STAGE_RESULTS lived in
# find_city_anomalies, FIND/SKEPTIC would land in one copy and every VERIFY in the other,
# and the email's run-health footer would silently under-report which models served the run.
# It lives in `common` (imported under the same name by both copies) to prevent that.
import types as _types
_src = open(os.path.join(REPO, "find_city_anomalies.py"), encoding="utf-8").read()
_src = _src.replace('if __name__ == "__main__":\n    main()', '')
_as_main = _types.ModuleType("__main__")
_as_main.__dict__.update({"__name__": "__main__",
                          "__file__": os.path.join(REPO, "find_city_anomalies.py")})
try:
    import dotenv as _dotenv
    _dotenv.load_dotenv = lambda *a, **k: None   # its stack-walk breaks under exec()
except ImportError:
    pass
exec(compile(_src, "find_city_anomalies.py", "exec"), _as_main.__dict__)

assert _as_main is not fa, "expected two distinct module objects to reproduce the CI shape"
_probe = L.LLMResult(text="x", ok=True, model="probe", provider="stub", fell_back=False,
                     grounded=False, truncated=False, attempts=1, error="")
X.STAGE_RESULTS.clear()
_as_main._stage_llm("FIND", _probe)   # the __main__ copy
fa._stage_llm("VERIFY", _probe)       # the copy providers.py lazy-imports
_names = [n for n, _ in X.STAGE_RESULTS]
assert _names == ["FIND", "VERIFY"], (
    f"STAGE_RESULTS is not shared across module copies: got {_names}. VERIFY outcomes "
    f"would be missing from the run-health footer in production.")
assert _as_main.STAGE_RESULTS is fa.STAGE_RESULTS is X.STAGE_RESULTS
X.STAGE_RESULTS.clear()
print("Case 10: STAGE_RESULTS shared across __main__ and imported module copies [OK]")


print("\n=== Case 11: re-alert rule (fa.is_new) ===\n")
_key11 = M.identity_key({"hotel_name": "Hotel Arte SPA & Park"})
_state11 = {"props": {_key11: {"date": "2026-10-07", "tier": "good", "price_eur": 195.1,
                               "name": "Hotel Arte SPA & Park"}}}
_base11 = {"hotel_name": "Arte SPA and Park Hotel", "tier": "good",
           "grounded_price_per_night_eur": 195.1}
for _label, _d, _today, _want in [
    ("same price, 12 days later", _base11, "2026-10-19", False),
    ("price 165.0 (>=15% drop, <= 165.835)", {**_base11, "grounded_price_per_night_eur": 165.0}, "2026-10-19", True),
    ("price 166.0 (drop just under 15%)", {**_base11, "grounded_price_per_night_eur": 166.0}, "2026-10-19", False),
    ("higher tier (diamond)", {**_base11, "tier": "diamond"}, "2026-10-19", True),
    ("lower tier (skip)", {**_base11, "tier": "skip"}, "2026-10-19", False),
    ("89 days later", _base11, "2027-01-04", False),
    ("90 days later", _base11, "2027-01-05", True),
    ("never-seen property", {"hotel_name": "Some New Hotel", "tier": "skip"}, "2026-10-19", True),
]:
    assert fa.is_new(_state11, _d, _today) is _want, f"is_new wrong for: {_label}"
print("Case 11: re-alert rule (word-order-proof key, 90 days, >=15% drop, higher tier) [OK]")


print("\n=== Case 12: legacy signals_seen seeding ===\n")
_legacy12 = {"hotelartespaandpark|2026-10|good": "2026-10-07",
             "artespaandparkhotel|2026-10|good": "2026-09-19",
             "luckybanskoaparthotelspaandrelax|2026-11|good": "2026-09-19",
             "luckybanskoaparthotelspaandrelax|2026-10|good": "2026-10-04",
             "luckybanskoaparthotelspaandrelax|2026-11|diamond": "2026-10-10"}
_seeded12 = fa.seed_props_from_legacy(_legacy12)
assert _seeded12 == {
    "aaaadeehklnopprrstt": {"date": "2026-10-07", "tier": "good", "price_eur": None,
                            "name": "hotelartespaandpark"},
    "aaaaaabcdeehkklllnnoopprrssttuxy": {"date": "2026-10-10", "tier": "diamond", "price_eur": None,
                                         "name": "luckybanskoaparthotelspaandrelax"},
}, _seeded12
sandbox12 = _new_sandbox("dh_stub_c12_")
try:
    with open("state/signals_seen.json", "w", encoding="utf-8") as f:
        json.dump({"seen": _legacy12, "monthly_count": {}}, f)
    _loaded12 = fa.load_seen()
    assert len(_loaded12.get("props", {})) == 2, _loaded12
    assert "seen" not in _loaded12, _loaded12
finally:
    os.chdir(_cwd)
    shutil.rmtree(sandbox12, ignore_errors=True)
print("Case 12: legacy 'seen' keys collapse to one props entry per property on load [OK]")


print("\n=== Case 13: pipeline end to end — history reference, caps, provenance, no re-alert ===\n")
sandbox13 = _new_sandbox("dh_stub_c13_")
_saved13 = (L.call_llm, fa.ground_deal, X.send_email)
try:
    T0 = dt.date.today()
    _lucky_key = "luckybanskoaparthotelspaandrelax|2026-11"
    with open("state/memory.json", "w", encoding="utf-8") as f:
        json.dump({"baselines": {_lucky_key: {
            "samples": [{"price": 122.12, "date": T0.isoformat(), "source": "Booking.com (apidojo)"}
                        for _ in range(3)],
            "realistic_price_eur": 122.12, "note": "seeded",
            "source": "Booking.com (apidojo)", "updated": T0.isoformat()}}, "ledger": []}, f)
    with open("state/signals_seen.json", "w", encoding="utf-8") as f:
        json.dump({"props": {M.identity_key({"hotel_name": "Electra Palace Thessaloniki"}): {
            "date": (T0 - dt.timedelta(days=5)).isoformat(), "tier": "good", "price_eur": 203.0,
            "name": "Electra Palace Thessaloniki"}}, "monthly_count": {}}, f)

    def _cand13(dest, hotel, city, country, score, window, est):
        return {"destination": dest, "hotel_name": hotel, "city": city, "country": country,
                "score": score, "type": "hotel", "window": window, "est_price_eur": est,
                "reason": "x", "confidence": "high"}

    _STAGE1_C13 = {"candidates": [   # order matters: deal_ids are assigned 1..5 in this order
        _cand13("Lucky Bansko Spa Break", "Aparthotel Lucky Bansko SPA & Relax", "Bansko", "Bulgaria", 96, "Nov 20-23, 2026", 122),
        _cand13("Prestige Varna", "Prestige Hotel And Aquapark", "Varna", "Bulgaria", 85, "Oct 23-26, 2026", 75),
        _cand13("Rome Test", "Hotel Roma Test", "Rome", "Italy", 85, "Nov 3-8, 2026", 110),
        _cand13("Kempinski Bansko", "Kempinski Grand Arena", "Bansko", "Bulgaria", 90, "Nov 20-23, 2026", 150),
        _cand13("Hissar Test", "Hissar Test Hotel", "Hisarya", "Bulgaria", 88, "Dec 4-7, 2026", 90),
    ]}

    def _score13(deal_id, dest, score, normal):
        return {"deal_id": deal_id, "destination": dest, "score": score, "normal_price_eur": normal,
                "why": "x", "about": "x", "value_case": "x", "red_flags": "",
                "flight_cost_eur_total": 0, "ground_transport_eur": 0}

    _SCORES_C13 = [_score13(1, "Lucky Bansko Spa Break", 88, 140),
                   _score13(3, "Rome Test", 80, 130),
                   _score13(5, "Hissar Test", 86, 120)]

    _find_prompt13 = {}
    def _stub_llm_c13(messages, model, max_tokens=2000, want_search=False, response_schema=None,
                      provider=None, search_prompt=None):
        if response_schema is C.STAGE1_RESPONSE_SCHEMA:
            _find_prompt13.setdefault("text", messages[0]["content"])
            return json.dumps(_STAGE1_C13)
        if response_schema is C.STAGE2_RESPONSE_SCHEMA:
            return json.dumps(_SCORES_C13)
        raise AssertionError(f"unexpected llm schema={response_schema}")

    def _ground13(name, method, verdict, ppn, total, dates, nights):
        return {"destination": name, "verdict": verdict, "confidence": "high",
                "grounding_method": method, "how_to_book": "Book at booking.com",
                "grounding": f"{method} live", "assistant_summary": f"{name}: €{ppn}/night.",
                "options": [_opt(ppn, total, dates, nights)]}

    _GROUND_C13 = {
        "Lucky Bansko Spa Break": _ground13("Aparthotel Lucky Bansko SPA & Relax", "apidojo", "confirm", 122.12, 366.36, "Nov 20-23, 2026", 3),
        "Prestige Varna": _ground13("Prestige Hotel And Aquapark", "apidojo", "correct", 1171.63, 3514.9, "Oct 23-26, 2026", 3),
        "Rome Test": _ground13("Hotel Roma Test", "apidojo", "correct", 100, 500, "Nov 3-8, 2026", 5),
        "Hissar Test": _ground13("Hissar Test Hotel", "llm", "confirm", 70, 210, "Dec 4-7, 2026", 3),
    }
    _called13 = []
    def _stub_ground_c13(diamond, mem_text, today):
        # Do NOT assert/raise here: main() wraps ground_deal in `except Exception`, which would
        # swallow it. Record the call and assert afterwards.
        dest = diamond.get("destination")
        _called13.append(dest)
        return _GROUND_C13.get(dest, {})

    _email13 = {}
    def _stub_send_c13(subject, html, text):
        _email13["subject"], _email13["html"], _email13["text"] = subject, html, text

    L.call_llm = _as_chain(_stub_llm_c13)
    fa.ground_deal = _stub_ground_c13
    X.send_email = _stub_send_c13

    fa.main()

    assert _email13, "run 1 should have emailed the new skips"
    html13, text13 = _email13["html"], _email13["text"]
    mem13 = json.load(open("state/memory.json", encoding="utf-8"))
    led13 = {e["destination"]: e for e in mem13["ledger"]}

    # Lucky: history reference 122.12 == grounded -> discount 0 -> price_adj 0, transit +3;
    # 88 + 0 + 3 = 91. Good is blocked by GOOD_MIN_DISCOUNT.
    lucky = led13["Lucky Bansko Spa Break"]
    assert (lucky["verdict"], lucky["final_score"], lucky["llm_score"]) == ("skip", 91, 88), lucky

    # Prestige: grounded 15.6x FIND's estimate -> Invariant Q blocks it.
    assert led13["Prestige Varna"]["verdict"] == "blocked", led13["Prestige Varna"]
    assert "15.6× FIND's €75 estimate" in html13, "Invariant Q reason missing from dropped footer"

    # Rome: ref 130 -> discount 0.2308 -> price_adj 12; transit -3; 80 + 12 - 3 = 89. tier_for
    # says good (llm 80 < 85 so not diamond); Invariant F demotes it because Rome is not drivable.
    rome = led13["Rome Test"]
    assert (rome["verdict"], rome["final_score"]) == ("skip", 89), rome
    assert fa.NOTE_FAR in html13, "far-away demotion note missing"

    # Kempinski: same city as Lucky (FIND 90 < 96) -> never grounded, never ledgered.
    assert "Kempinski Bansko" not in _called13, _called13
    assert "Kempinski Bansko" not in led13, led13.keys()

    # Hissar: LLM-grounded -> Invariant U caps diamond at skip. 86 + 15 + 3 = 104 -> clamp 100.
    hissar = led13["Hissar Test"]
    assert (hissar["verdict"], hissar["final_score"]) == ("skip", 100), hissar
    assert fa.NOTE_UNVERIFIED in html13, "unverified-price demotion note missing"
    _hissar_id = M.identity({"hotel_name": "Hissar Test Hotel"})
    assert not any(k.startswith(_hissar_id) for k in mem13["baselines"]), \
        f"Invariant B: LLM-grounded Hissar must not write a baseline: {list(mem13['baselines'])}"

    for body in (html13, text13):
        assert "Prices: 2 live Booking.com · 1 LLM estimates" in body, "health-footer price line missing"

    p13 = _find_prompt13["text"]
    assert "ALREADY EMAILED IN THE LAST 90 DAYS" in p13
    assert "Electra Palace Thessaloniki — emailed" in p13
    assert "Known realistic prices" not in p13

    seen13 = json.load(open("state/signals_seen.json", encoding="utf-8"))
    _lucky_id = M.identity_key({"hotel_name": "Aparthotel Lucky Bansko SPA & Relax"})
    assert seen13["props"][_lucky_id]["price_eur"] == 122.12, seen13["props"].get(_lucky_id)

    # Second run, same stubs: the three skips are at unchanged prices -> not new -> no email.
    _email13.clear()
    fa.main()
    assert not _email13, f"run 2 must not re-email unchanged skips: {_email13.get('subject')}"
    assert "Kempinski Bansko" not in _called13, _called13

    print("Case 13: history reference, Q block, per-city cap, F/U demotions, Invariant B, "
          "prompt do-not-repeat list, health price line, no re-alert on run 2 [OK]")
finally:
    L.call_llm, fa.ground_deal, X.send_email = _saved13
    os.chdir(_cwd)
    shutil.rmtree(sandbox13, ignore_errors=True)


# ── Case 14: Spain section renders the sweep's numbers verbatim (HTML + text) ──
print("\n=== Case 14: Spain email section + footer line ===\n")
_deal14 = {
    "kind": "spain", "city": "Valencia", "country": "Spain", "airport": "VLC",
    "hotel_name": "Promo Hotel", "stars": 4, "review_score": 8.6,
    "checkin": "2026-11-03", "checkout": "2026-11-07", "nights": 4, "window": "Nov 3-7, 2026",
    "destination": "Valencia · Promo Hotel", "hotel_ppn_eur": 52.0, "hotel_total_eur": 208.0,
    "hotel_p25_ppn_eur": 96.25, "hotel_sample": 6, "flight_out_eur": 19.99, "flight_ret_eur": 15.99,
    "flight_rt_pp_eur": 35.98, "flight_median_rt_pp_eur": 69.99, "flight_family_eur": 107.94,
    "fixed_eur": 170, "trip_variable_eur": 315.94, "typical_variable_eur": 594.97, "ratio": 0.531,
    "allin_total_eur": 485.94, "typical_allin_eur": 764.97, "tier": "good",
    "hotel_url": "https://www.booking.com/searchresults.html?ss=Promo+Hotel",
    "flight_url": "https://www.ryanair.com/gb/en/trip/flights/select?adults=2&teens=0&children=1&infants=0&dateOut=2026-11-03&dateIn=2026-11-07&isConnectedFlight=false&discount=0&isReturn=true&promoCode=&originIata=SOF&destinationIata=VLC",
    "realert_key": "spain:" + M.identity_key({"hotel_name": "Promo Hotel"}),
    "realert_price_eur": 485.94, "about": "", "value_case": "", "red_flags": "",
}
_health14 = {"counts": {}, "spain": {"flights_ok": 1, "cities": [{}] * 7, "hotels_ok": 2,
                                     "deals": [_deal14], "windows": [_deal14], "error": ""}}
_html14 = fa.build_email_html([], [], [], 0, {}, _health14, None, spain=[_deal14])
_text14 = fa.build_email_text([], [], [], {}, _health14, None, spain=[_deal14])
for _blob, _kind in ((_html14, "html"), (_text14, "text")):
    assert "€486 all-in for 3 · typical for these dates ~€765 · flights + hotel 47% below typical" in _blob, _kind
    assert ("Spain sweep: 1/7 cities with Ryanair flights from SOF · 2 windows priced · 1 deal(s) · "
            "best Valencia at 53% of typical") in _blob, _kind
    assert "destinationIata=VLC" in _blob, _kind
    assert "🇪🇸 Spain — flight + hotel deals" in _blob, _kind
assert "Spain sweep: off" in fa._health_footer_text({"counts": {}, "spain": None})
assert "Spain sweep" not in fa._health_footer_text({"counts": {}})
print("Case 14: Spain section + footer render the sweep's numbers in HTML and text [OK]")


# ── Case 15: the re-alert rule keys a Spain deal by realert_key, priced all-in ──
print("\n=== Case 15: re-alert rule for Spain deals ===\n")
_today15 = dt.date.today()
_state15 = {"props": {_deal14["realert_key"]: {
    "date": (_today15 - dt.timedelta(days=3)).isoformat(), "tier": "good", "price_eur": 485.94,
    "name": "Promo Hotel"}}}
assert not fa.is_new(_state15, _deal14, _today15.isoformat()), "same Spain deal re-alerted"
assert fa.is_new(_state15, {**_deal14, "realert_price_eur": 410.0}, _today15.isoformat()), \
    "Spain deal >=15% cheaper all-in (410 <= 413.049) must re-alert"
assert not fa.is_new(_state15, {**_deal14, "realert_price_eur": 414.0}, _today15.isoformat())
print("Case 15: Spain deals re-alert on realert_key + all-in total [OK]")


print("\nAll assertions passed (Part 1 + Part 2).")
