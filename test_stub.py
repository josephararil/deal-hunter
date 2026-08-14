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
    normal_price_eur given) grounds at a fair €95 and lands in GOOD, not diamond — with no
    reference price the pipeline stays neutral (discount 0.0, price_adj 0) rather than
    substituting a regional par, which used to manufacture a false discount/diamond.
  - Wildcard: the most interesting non-local find (Antalya) is badged in the digest.
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
    {"destination": "Regnum Bansko, Bulgaria", "hotel_name": "Regnum Bansko", "city": "Bansko",
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
        "confidence": "high", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
        "assistant_summary": "Rixos Premium Antalya, Jan 10-14: €70/night (€280 total).",
        "options": [_opt(70, 280, "Jan 10-14, 2027", 4)]},
    "Kempinski Hotel Grand Arena, Bansko, Bulgaria": {"destination": "Kempinski Grand Arena",
        "verdict": "correct", "confidence": "high", "how_to_book": "Book at booking.com",
        "grounding": "apidojo live", "assistant_summary": "Kempinski Grand Arena, Jul 10-13: €85/night.",
        "options": [_opt(85, 255, "Jul 10-13, 2026", 3)]},
    "Regnum Bansko, Bulgaria": {"destination": "Regnum Bansko", "verdict": "correct",
        "confidence": "high", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
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
    # Ordinary local spa at a fair price → GOOD, not diamond (no discount, ordinary desirability).
    "Hisarya Ultra-Local Thermal Retreat": {"destination": "Sana Spa Hotel", "verdict": "confirm",
        "confidence": "high", "how_to_book": "Book at booking.com", "grounding": "apidojo live",
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
    # Tier-1 (drivable) -> transit_adj +3; final = 82 + 0 + 3 = 85. It still lands in GOOD not
    # diamond because it's double-gated: final 85 < DIAMOND_SCORE_THRESHOLD 88 AND
    # discount 0.0 < DIAMOND_MIN_DISCOUNT 0.25.
    hisarya = led["Hisarya Ultra-Local Thermal Retreat"]
    assert hisarya["verdict"] == "good", f"ordinary local spa must be GOOD not {hisarya['verdict']}: {hisarya}"
    assert hisarya["final_score"] == 85, f"expected neutral-fallback final 85, got {hisarya['final_score']}"
    print("Ordinary local spa scored GOOD (not diamond) via neutral no-reference fallback [OK]")

    # Wildcard: the most interesting NON-LOCAL find (Antalya) is badged in the digest.
    assert "🃏 Wildcard" in html, "wildcard badge missing from digest"
    print("Wildcard badge surfaced for the non-local find [OK]")

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
    assert any(k.endswith("|unscored") for k in seen1.get("seen", {})), seen1.get("seen")
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
    _STAGE1_C2 = {"candidates": [
        {"destination": "Bansko Weekend One, Bulgaria", "hotel_name": "Alpine Lodge One",
         "city": "Bansko", "country": "Bulgaria", "score": 82, "type": "hotel",
         "window": "Mar 5-8, 2027", "est_price_eur": 90,
         "reason": "Ski lodge.", "confidence": "high"},
        {"destination": "Bansko Weekend Two, Bulgaria", "hotel_name": "Alpine Lodge Two",
         "city": "Bansko", "country": "Bulgaria", "score": 83, "type": "hotel",
         "window": "Mar 5-8, 2027", "est_price_eur": 92,
         "reason": "Ski lodge.", "confidence": "high"},
        {"destination": "Bansko Weekend Three, Bulgaria", "hotel_name": "Alpine Lodge Three",
         "city": "Bansko", "country": "Bulgaria", "score": 84, "type": "hotel",
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


print("\nAll assertions passed (Part 1 + Part 2).")
