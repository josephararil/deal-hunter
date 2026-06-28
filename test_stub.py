"""
Stub verification for Phases A2 + A3.

Monkey-patches common.llm with canned responses covering:
  - Stage 1: 3 candidates all >= 80
  - Stage 2: all 3 kept (go to Stage 3)
  - Stage 3 call 1 (Antalya):  confirm  → must reach email
  - Stage 3 call 2 (Regnum):   correct  → must reach email (price adjusted but still good)
  - Stage 3 call 3 (Velingrad): kill     → must NOT reach email

Run: python test_stub.py
Expected: email attempted for 2 diamonds (fails gracefully with no SMTP),
city_signals.md shows 3 Stage 3 entries (✅ ✓ ❌), Velingrad absent from email path.
"""

import json, os, sys

# Ensure state/ directory exists
os.makedirs("state", exist_ok=True)

# Seed minimal seen state so anti-spam doesn't block our new test diamonds
import common as X
X.save_json("signals_seen.json", {"seen": {}, "monthly_count": {}})

# ── Canned LLM responses ────────────────────────────────────────────────────

_STAGE1 = json.dumps({
    "candidates": [
        {
            "destination": "Antalya 5-Star All-Inclusive",
            "score": 88,
            "type": "hotel",
            "window": "Jan 10-20, 2027",
            "reason": "Rixos Premium drops to €98/night (down from €420 peak) — indoor pools and kids club fully open in January.",
            "confidence": "high",
        },
        {
            "destination": "Regnum Bansko",
            "score": 84,
            "type": "hotel",
            "window": "Aug 1-31, 2026",
            "reason": "Claimed at €72-95/night (down from €220 peak) — luxury ski resort, indoor pool open year-round.",
            "confidence": "high",
        },
        {
            "destination": "Arte Spa & Park, Velingrad",
            "score": 81,
            "type": "hotel",
            "window": "Jul 15 - Aug 15, 2026",
            "reason": "Listed at €165/night, framed as 35% off peak €255. Thermal pools open. Drive 1.5h from Plovdiv.",
            "confidence": "medium",
        },
    ]
})

_STAGE2 = json.dumps([
    {
        "destination": "Antalya 5-Star All-Inclusive",
        "verdict": "keep",
        "why": "5-star all-inclusive at €98 with fully operational indoor kids infrastructure — exceptional utility-to-price play.",
        "red_flags": "Confirm kids club hours in January.",
    },
    {
        "destination": "Regnum Bansko",
        "verdict": "keep",
        "why": "Luxury alpine resort at claimed €72-95 with indoor pool — needs price verification.",
        "red_flags": "Verify exact August weekend prices — may vary from monthly low.",
    },
    {
        "destination": "Arte Spa & Park, Velingrad",
        "verdict": "keep",
        "why": "Thermal spa with family facilities 1.5h drive.",
        "red_flags": "Absolute price of €165/night may be unremarkable for the market.",
    },
])

_STAGE3_CONFIRM = json.dumps({
    "destination": "Antalya 5-Star All-Inclusive",
    "verdict": "confirm",
    "options": [
        {
            "dates": "Jan 10-14, 2027",
            "nights": 4,
            "price_per_night_eur": 98,
            "total_eur": 392,
            "booking_url": "https://www.booking.com/hotel/tr/rixos-premium-antalya.html",
            "source": "booking.com live search 2026-06-28",
        },
        {
            "dates": "Jan 15-20, 2027",
            "nights": 5,
            "price_per_night_eur": 95,
            "total_eur": 475,
            "booking_url": "https://www.booking.com/hotel/tr/rixos-premium-antalya.html",
            "source": "booking.com live search 2026-06-28",
        },
    ],
    "how_to_book": "Book at the URLs above. All-inclusive rate includes meals and kids club.",
    "grounding": "Searched booking.com for Rixos Premium Antalya, Jan 10-20 2027. Confirmed €95-98/night all-inclusive.",
    "assistant_summary": "I confirmed Rixos Premium Antalya for Jan 10-14 at €98/night (€392 for 4 nights, all-inclusive). Kids club confirmed open. Book at booking.com.",
    "confidence": "high",
})

_STAGE3_CORRECT = json.dumps({
    "destination": "Regnum Bansko",
    "verdict": "correct",
    "options": [
        {
            "dates": "Aug 8-10, 2026",
            "nights": 2,
            "price_per_night_eur": 112,
            "total_eur": 224,
            "booking_url": "https://www.booking.com/hotel/bg/regnum-bansko.html",
            "source": "booking.com live search 2026-06-28",
        },
        {
            "dates": "Aug 22-25, 2026",
            "nights": 3,
            "price_per_night_eur": 105,
            "total_eur": 315,
            "booking_url": "https://www.booking.com/hotel/bg/regnum-bansko.html",
            "source": "booking.com live search 2026-06-28",
        },
    ],
    "how_to_book": "Book at booking.com link above. Breakfast included.",
    "grounding": "Searched booking.com for Regnum Bansko August 2026. The claimed €72-95 rate does not appear for Aug weekends — actual rates are €105-112/night. Still significantly below the €220 peak, and represents genuine value for a 5-star resort.",
    "assistant_summary": "The claimed €72-95 understates actual August prices: I found €105-112/night (not €72). Still a solid deal at 50% below peak. Book at booking.com for Aug 8-10 (€224 total) or Aug 22-25 (€315 total).",
    "confidence": "high",
})

_STAGE3_KILL = json.dumps({
    "destination": "Arte Spa & Park, Velingrad",
    "verdict": "kill",
    "options": [],
    "how_to_book": "",
    "grounding": "Searched booking.com and arte-spa.com. Found Arte Spa & Park rates of €140-170/night for summer 2026 — consistent with the claim. However, comparable Velingrad spa hotels (Orpheus, Spa Hotel Velingrad) charge €65-110/night for equivalent amenities. €165 is the normal top-market rate for this town, not an exceptional deal.",
    "assistant_summary": "Killing this one. At €165/night Arte Spa is priced at the top of the Velingrad market, not below it. You can get equivalent thermal spa facilities 10 minutes away for €80-110/night. No arbitrage here.",
    "confidence": "high",
})

# ── Monkey-patch common.llm ─────────────────────────────────────────────────

_call_idx = 0
_RESPONSES = [_STAGE1, _STAGE2, _STAGE3_CONFIRM, _STAGE3_CORRECT, _STAGE3_KILL]


def _stub_llm(messages, model, max_tokens=2000, want_search=False, provider=None):
    global _call_idx
    resp = _RESPONSES[_call_idx]
    stage = ["Stage1-find", "Stage2-skeptic", "Stage3-confirm", "Stage3-correct", "Stage3-kill"][_call_idx]
    print(f"  [stub] llm call #{_call_idx + 1} ({stage}) model={model} want_search={want_search}")
    _call_idx += 1
    return resp


import common
common.llm = _stub_llm

# ── Run ─────────────────────────────────────────────────────────────────────

import find_city_anomalies as fa

print("\n=== Running stub test ===\n")
fa.main()

# ── Assert ──────────────────────────────────────────────────────────────────

print("\n=== Assertions ===")

with open("state/city_signals.md", encoding="utf-8") as f:
    md = f.read()

assert "Stage 3 Verification" in md, "city_signals.md missing Stage 3 section"
assert "CONFIRM" in md, "CONFIRM outcome not in md"
assert "CORRECT" in md, "CORRECT outcome not in md"
assert "KILL" in md, "KILL outcome not in md"
assert "€98" in md or "€95" in md, "Antalya price not in md"
assert "€112" in md or "€105" in md, "Regnum corrected price not in md"
assert "Velingrad" in md, "Velingrad should appear in md (as killed)"
print("city_signals.md: Stage 3 outcomes present [OK]")

seen = X.load_json("signals_seen.json", {})
# No email was sent (no SMTP), so seen should be empty
assert seen.get("seen", {}) == {}, f"seen should be empty (email failed), got: {seen['seen']}"
print("signals_seen.json: no entries (email failed gracefully) [OK]")

# Stub call count confirms Stage 3 ran exactly 3 times (once per Stage-2 diamond)
assert _call_idx == 5, f"Expected 5 llm() calls, got {_call_idx}"
print(f"llm() call count: {_call_idx} (stage1 + stage2 + 3x stage3) [OK]")

print("All assertions passed.")
