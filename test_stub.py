"""
Stub verification for Phases A2 + A3 + A4.

Monkey-patches common.llm with canned responses covering:
  - Stage 1: 3 candidates all >= 80
  - Stage 2: all 3 kept (go to Stage 3)
  - Stage 3 call 1 (Antalya):   confirm → must reach email (both options have booking URLs)
  - Stage 3 call 2 (Regnum):    correct → must reach email (second option has no URL → how_to_book fallback)
  - Stage 3 call 3 (Velingrad): kill    → must NOT reach email

Run: python test_stub.py
Expected: email attempted for 2 diamonds (fails gracefully with no SMTP),
city_signals.md shows 3 Stage 3 entries (✅ 🔧 ❌), email HTML/text shows concrete dates,
prices, booking links, and sensible no-URL fallback for the second Regnum option.
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
            "booking_url": None,  # no URL — exercises the how_to_book fallback in build_email_*
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

# Capture email output so we can assert on it (SMTP is unset, so send_email will raise;
# we patch it here to grab html/text before the exception path would swallow them).
_captured_email = {}

_real_send_email = X.send_email


def _stub_send_email(subject, html, text):
    _captured_email["subject"] = subject
    _captured_email["html"] = html
    _captured_email["text"] = text
    raise KeyError("SMTP_HOST")  # simulate missing SMTP config exactly as production does


common.send_email = _stub_send_email

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

# A4: assert email renders grounded structure (assistant_summary, dates, prices, links)
assert _captured_email, "send_email was never called — no diamonds reached email path"
html = _captured_email["html"]
text = _captured_email["text"]
print("\n=== A4 email assertions ===")

# assistant_summary leads each diamond block
assert "I confirmed Rixos Premium Antalya" in html, "Antalya assistant_summary missing from HTML"
assert "The claimed" in html and "understates" in html, "Regnum assistant_summary missing from HTML"
assert "I confirmed Rixos Premium Antalya" in text, "Antalya assistant_summary missing from text"
print("assistant_summary: present in HTML and text [OK]")

# Concrete dates appear
assert "Jan 10-14, 2027" in html, "Antalya dates missing from HTML"
assert "Aug 8-10, 2026" in html, "Regnum dates missing from HTML"
assert "Jan 10-14, 2027" in text, "Antalya dates missing from text"
assert "Aug 8-10, 2026" in text, "Regnum dates missing from text"
print("Concrete dates: present in HTML and text [OK]")

# Prices appear
assert "€98" in html, "Antalya €98/night missing from HTML"
assert "€112" in html, "Regnum €112/night missing from HTML"
assert "€392" in html, "Antalya €392 total missing from HTML"
assert "€98" in text and "€392" in text, "Antalya prices missing from text"
print("Prices: present in HTML and text [OK]")

# Booking URL (option with URL)
assert "booking.com/hotel/tr/rixos-premium-antalya" in html, "Antalya booking URL missing from HTML"
assert "booking.com/hotel/bg/regnum-bansko" in html, "Regnum booking URL missing from HTML"
assert "booking.com/hotel/tr/rixos-premium-antalya" in text, "Antalya booking URL missing from text"
print("Booking URLs: present in HTML and text [OK]")

# No-URL fallback for Regnum Aug 22-25 option (uses how_to_book)
assert "Book at booking.com" in html, "Regnum no-URL how_to_book fallback missing from HTML"
assert "Book at booking.com" in text, "Regnum no-URL how_to_book fallback missing from text"
print("No-URL how_to_book fallback: present in HTML and text [OK]")

# Grounding appears
assert "Source:" in html, "Grounding 'Source:' label missing from HTML"
assert "Source:" in text, "Grounding 'Source:' label missing from text"
print("Grounding: present in HTML and text [OK]")

# Red flags still appear
assert "Confirm kids club" in html, "Antalya red_flags missing from HTML"
assert "Verify exact August" in html, "Regnum red_flags missing from HTML"
print("Red flags: present in HTML [OK]")

# Footer and heading preserved
assert "Verify before booking" in html, "Footer missing from HTML"
assert "Diamond Finder" in html, "Heading missing from HTML"
print("Footer and heading: preserved [OK]")

# Velingrad (killed) must not appear in email
assert "Velingrad" not in html, "Killed deal (Velingrad) leaked into email HTML"
assert "Velingrad" not in text, "Killed deal (Velingrad) leaked into email text"
print("Killed deal not in email: [OK]")

print("\n--- HTML preview (first 1200 chars) ---")
print(html[:1200])
print("\n--- text preview ---")
print(text[:800])

print("\nAll assertions passed.")
