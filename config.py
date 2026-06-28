"""Configuration for the diamond-finder pipeline. Edit freely."""

# ── City list ───────────────────────────────────────────────────────────────
# Cities the diamond finder uses as a search anchor. The LLM receives these as
# the preferred destinations but can extend to nearby or thematically related
# places when a confirmed opportunity exists (e.g. a cruise from a regional port).
#
# Values are (min_nights, max_nights). The diamond finder uses only the city
# names (CITIES.keys()); the night-range values are kept for reference and are
# used by the dormant Apify/hunt pipeline in _dormant/.
CITIES = {
    # --- Bulgaria, by car (<3h) ---
    "Asenovgrad, Bulgaria": (1, 3),   "Banya, Bulgaria": (2, 4),
    "Bansko, Bulgaria": (2, 7),       "Burgas, Bulgaria": (2, 7),
    "Chiflik, Bulgaria": (2, 4),      "Hisarya, Bulgaria": (2, 4),
    "Koprivshtitsa, Bulgaria": (1, 3), "Nessebar, Bulgaria": (2, 7),
    "Pazardzhik, Bulgaria": (1, 3),   "Pamporovo, Bulgaria": (2, 7),
    "Smolyan, Bulgaria": (2, 5),      "Sofia, Bulgaria": (1, 4),
    "Sozopol, Bulgaria": (2, 7),      "Stara Zagora, Bulgaria": (1, 3),
    "Veliko Tarnovo, Bulgaria": (2, 4),
    # --- Greece, by car ---
    "Alexandroupoli, Greece": (2, 5), "Kavala, Greece": (2, 5),
    "Komotini, Greece": (2, 4),       "Xanthi, Greece": (2, 4),
    # --- Turkey ---
    "Edirne, Turkey": (2, 3),         "Istanbul, Turkey": (3, 6),
    # --- by low-cost flight (min >= 2: a 1-night trip isn't worth the flight) ---
    "Bari, Italy": (3, 7),    "Milan, Italy": (2, 7),   "Naples, Italy": (3, 7),
    "Rome, Italy": (3, 7),    "Bratislava, Slovakia": (2, 7), "Vienna, Austria": (2, 7),
    "Athens, Greece": (3, 7), "Budapest, Hungary": (2, 7),   "Krakow, Poland": (3, 7),
    "Belgrade, Serbia": (2, 5), "London, United Kingdom": (3, 7),
    "Birmingham, United Kingdom": (3, 7), "Manchester, United Kingdom": (3, 7),
}

# Cities grouped by transit tier — used to build the structured city block in
# FIND_PROMPT. Keeps country labels out of the individual city names so the LLM
# receives a clean, hierarchy-aware list instead of a flat comma-separated mess.
CITY_TIER_GROUPS = [
    ("Tier 1 — Drive ≤3h from Plovdiv or Direct PDV Flight", [
        ("Bulgaria", [
            "Asenovgrad", "Chepelare", "Velingrad", "Bansko", "Pamporovo", "Borovets", "Hisarya", 
            "Banya", "Burgas", "Sozopol", "Nessebar"
        ]),
        ("Greece (Drive)", [
            "Kavala", "Alexandroupoli", "Thassos"
        ]),
        ("Turkey (Drive)", [
            "Edirne", "Kirklareli"
        ]),
        ("Direct Flights from PDV", [
            "London", "Birmingham", "Manchester", "Milan", "Bratislava"
        ])
    ]),
    ("Tier 2 — Low-cost/Direct Flight from SOF", [
        ("Turkey", ["Istanbul", "Antalya", "Bodrum"]),
        ("Italy", ["Rome", "Bari", "Naples", "Bologna", "Venice"]),
        ("Greece", ["Athens", "Thessaloniki", "Chania", "Corfu"]),
        ("Central Europe", ["Vienna", "Budapest", "Prague", "Bratislava"]),
        ("Spain", ["Barcelona", "Madrid", "Valencia", "Malaga"]),
        ("Cyprus", ["Larnaca", "Paphos"]),
        ("Malta", ["Valetta"]),
        ("Germany", ["Munich", "Frankfurt", "Memmingen", "Berlin"]),
        ("France", ["Paris", "Nice"]),
        ("United Kingdom", ["London", "Bristol", "Edinburgh"])
    ]),
]


def cities_prompt_text():
    """Return cities formatted as a structured tier block for the find prompt."""
    lines = []
    for tier, regions in CITY_TIER_GROUPS:
        lines.append(f"{tier}:")
        for region, cities in regions:
            lines.append(f"  {region}: {', '.join(cities)}")
    return "\n".join(lines)


# ── LLM models ──────────────────────────────────────────────────────────────
# Stage 1 (find + web search): faster model with grounding.
# Stage 2 (skeptic): more capable model for hostile review, no search.
# These are Anthropic model names; Gemini equivalents live in GEMINI_MODEL_MAP.
MODEL_DIAMOND         = "claude-sonnet-4-6"
MODEL_DIAMOND_SKEPTIC = "claude-opus-4-8"

# Maps Anthropic model names (the canonical identifiers used throughout the code)
# to their Gemini equivalents. Used when LLM_PROVIDER=gemini. Add a new entry
# here whenever a new model role is added to config; never hard-code Gemini model
# names anywhere else.
GEMINI_MODEL_MAP = {
    "claude-sonnet-4-6": "gemini-3.5-flash",
    "claude-opus-4-8":   "gemini-3.1-pro-preview",
}

# ── LLM token budgets ────────────────────────────────────────────────────────
# Stage 1 (find) needs more room: it receives web-search grounding text and must
# reason across many candidate types before outputting a scored JSON list.
MAX_TOKENS_FIND    = 4000

# Stage 2 (skeptic) is a compact verdict-only call: keep/kill + one sentence per candidate.
# Must be large enough to absorb thinking-model overhead (gemini-3.5-flash uses ~2k tokens
# for internal reasoning before writing output — 2000 was not enough).
MAX_TOKENS_SKEPTIC = 8000

# ── Web search ───────────────────────────────────────────────────────────────
# Maximum number of individual web-search tool uses allowed in a single Stage 1
# call (Anthropic provider only; Gemini's google_search has no per-call cap).
# Higher values improve grounding quality but increase latency and token cost.
WEB_SEARCH_MAX_USES = 6

# ── Diamond finder gate ──────────────────────────────────────────────────────
# Minimum score (0–100) a Stage 1 candidate must reach to be forwarded to the
# skeptic. Candidates below this threshold still appear in city_signals.md but
# never trigger email. Raise this to make email rarer; lower with caution.
STAGE1_MIN_SCORE = 80

# Maximum number of diamonds included in the single email sent per run. Because
# surviving Stage 2 is rare, this cap is almost never reached in practice.
MAX_EMAILS_PER_RUN = 3

# Days before the same destination+window pair can trigger another email.
# Prevents daily spam about a deal that persists for weeks.
SIGNAL_TTL_DAYS = 30

# ── LLM prompts ─────────────────────────────────────────────────────────────
# Placeholders filled at runtime by find_city_anomalies.py:
#   FIND_PROMPT    → {today}, {cities}
#   SKEPTIC_PROMPT → {today}, {min_score}, {candidates}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

FIND_PROMPT = """Today is {today}. You are a pragmatic, data-driven Travel Arbitrage Analyst. Your job is to perform live web searches to find 3-5 concrete, actionable travel opportunities for a family of 3 (2 adults, 1 child aged 4) based in Plovdiv, Bulgaria.

Your objective is to find high-utility value plays where a premium experience or location drops dramatically in price while maintaining high utility and comfort for a 4-year-old.

---

### SCORING CALIBRATION (Threshold: >= 80 Triggers an Email Alert)

Your scoring dictates the pipeline routing. A score of 80 or above means the deal is so strong it warrants immediately emailing the user. Be conservative.

- **Score 90-100 (Absolute No-Brainer):** Rare, highly actionable, massive price-to-utility disconnect. Zero logistical flaws.
- **Score 80-89 (High-Value Play):** Clearly above-average value with solid live evidence. Either a low-friction local play (Tier 1) with an excellent discount, or a higher-friction play (Tier 2) with a discount so steep it completely offsets the transit hassle.
- **Score 60-79 (Log Only - No Email):** Interesting low-season or standard budget pricing, but transit friction or utility loss doesn't justify interrupting the user.
- **Score Below 60:** Weak deals or unverified data included purely for logging purposes.

---

### GEOGRAPHIC & LOGISTICAL HIERARCHY

1. Tier 1: Local High Utility (Drive <= 3 hours from Plovdiv OR flights from Plovdiv Airport [PDV])
   - Baseline: Low transit friction for a 4-year-old. 
   - Evaluation: If an elite domestic spa resort (e.g., Velingrad, Bansko) or regional destination drops to entry-level pricing while keeping family infrastructure fully open, score it **80-95**.

2. Tier 2: High-Friction Transit (Flights from Sofia Airport [SOF] OR Drives > 3 hours)
   - Baseline: High transit friction for a 4-year-old. 
   - Evaluation: Standard discounts or normal cheap flights from SOF must be scored **below 80**. To cross the **80+ email threshold**, a Sofia transit option must offer a staggering price drop on a premium experience (e.g., a 5-star Antalya resort collapsing to €100/night with active indoor kid facilities).

Target destinations, grouped by transit tier (aligns with the scoring hierarchy above):
{cities}

---

### HUNTING CATEGORIES
Perform active web searches across these specific categories:
- Premium Off-Season Troughs: 4 to 5-star family resorts with massive predictable price drops post-holidays or between seasons where indoor/kids infrastructure remains fully open.
- Regional Cruises: Family-friendly itineraries departing from Istanbul, Athens (Piraeus), or Thessaloniki.
- Flight Error/Sale Fares: Confirmed active low fares from SOF or PDV.
- Package Dumps: Last-minute unsold flight+hotel bundles.

---

### SEARCH & VERIFICATION RULES
- YOU MUST USE THE WEB SEARCH TOOL. Every candidate must be backed by real pricing found on the live web within the last 48 hours.
- Never invent prices, hotel names, or flight availability. If you cannot find live, verifiable data for a target city, emit an empty list for that city.
- Check child-safety/amenities: Ensure any off-season resort has an operating indoor heated pool, kids' area, or relevant infrastructure active *during* the specified travel window.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json. Output a single JSON object matching the schema below.

JSON Schema:
{{
  "candidates": [
    {{
      "destination": "City, resort name, or cruise line/route",
      "score": 82,
      "type": "hotel",
      "window": "Specific exact dates or tight window (e.g., Jan 10-17)",
      "reason": "Cite live prices found via search. Quantify the utility vs. price play (e.g., peak price vs current live price). State why it fits a 4-year-old.",
      "confidence": "high"
    }}
  ]
}}

If no verifiable deals meet these criteria today, return `{{"candidates": []}}`."""


SKEPTIC_PROMPT = """You are a pragmatic, high-utility Travel Value Analyst. Your job is to filter daily travel alerts and identify high-value plays/arbitrage opportunities for a young family. 

Your objective is to maintain a strict 5-10% acceptance rate (roughly 2-3 kept candidates per month out of 100+ inputs). You achieve this not by hunting for pricing glitches, but by ruthlessly eliminating options that lack massive, undeniable value or fail basic family logistics.

Today is {today}.
Target Demographics & Logistics:
- Party: Family of 3 (2 adults, 1 child aged 4). 
- Location Base: Plovdiv, Bulgaria.
- Transit Limits: Departure strictly from Plovdiv Airport (PDV), Sofia Airport (SOF), or a reasonable drive from Plovdiv.
- Currency: EUR.

Input Candidates (scored >= {min_score}/100 in preliminary filtering):
{candidates}

---

### CONCRETE EXAMPLES FOR CALIBRATION

#### EXAMPLE 1: ANATOMY OF A "KEEP" (Off-Season High Utility)
- **Candidate:** Antalya, Turkey. 5-Star All-Inclusive Resort, mid-January after the NYE peak but before the spring break.
- **Price:** €100/night (Down from €400/night peak/NYE).
- **Analysis:** This matches predictable, standard historical low-season pricing for January. However, it is a KEEP. The weather is too cold for the beach, but the resort offers indoor heated pools, operating kids' clubs, and unlimited premium dining. The utility drop is only 20% and requires additional logistics/flights, but the price drop is 75%. This is an exceptional utility-to-price play for a 4-year-old.

#### EXAMPLE 2: ANATOMY OF A "KEEP" (Drive-Distance Luxury Play)
- **Candidate:** Bansko or Velingrad, Bulgaria. Luxury Spa Hotel, late October (Inter-season).
- **Price:** €80/night (Down from €250/night peak ski/winter season).
- **Analysis:** This is a KEEP. While there is no skiing in October, the indoor thermal pools, children's play areas, and massive price drop unlock a premium weekend getaway with zero flight logistics.

#### EXAMPLE 3: ANATOMY OF A "KILL" (The Low-Season Trap)
- **Candidate:** Sunny Beach, Bulgaria. 4-Star Beachfront Hotel, October.
- **Price:** €40/night (Down from €150/night July peak).
- **Analysis:** This is a KILL. While incredibly cheap, the utility drops to near zero: outdoor pools are freezing, kids' entertainment is completely closed, and the town is a ghost town. The price drop does not compensate for the complete loss of family utility.

#### EXAMPLE 4: ANATOMY OF A "KILL" (The Toddler Tax)
- **Candidate:** 7-Night Mediterranean Cruise (Athens to Venice/Ravenna) on Norwegian Pearl.
- **Price:** $449/person ($64/night) due to a last-minute cancellation sale.
- **Analysis:** This is a KILL. It is clearly an outstanding deal on paper. However, the open-jaw itinerary (Athens to Ravenna) creates a logistical and financial nightmare for a family with a 4-year-old, as flights from and back to Bulgaria will wipe out any cruise savings and then some.

---

### EVALUATION PROTOCOL

You must KEEP a candidate if it represents a High-Utility Value Play. This is defined as either:
- A predictable or seasonal price drop where the price plummets dramatically (e.g., peak €400 down to off-peak €100), but the core utility remains high for a family. 
- A rare, verifiable, time-sensitive opportunity where the price drop is massive and the logistics are manageable for a family with a 4-year-old.

You must ruthlessly KILL a candidate if it triggers any of the following:

1. The "False Value" Low-Season Trap:
   - A low price where the drop in utility matches or exceeds the drop in price. (e.g., a cheap waterpark resort when the waterparks are closed, or an outdoor beach holiday during a freezing/monsoon month with zero indoor amenities).
2. The "Toddler Tax" & Logistics Flaw:
   - Destinations requiring >4 hours total transit time (door-to-door from Plovdiv) or budget flights where unavoidable extras (cabin bags, family seat selection) kill any savings.
   - Places with steep topography, zero child-friendly infrastructure, or heavy logistical friction.
3. Hidden Cost Creep:
   - Cheap flights paired with predatory local accommodation rates, or a cheap hotel in a region where basic dining and transit costs erase the savings.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json. Output a single JSON array containing one object per input candidate, maintaining the exact input order. 

JSON Schema:
[
  {{
    "destination": "Exact string from input",
    "verdict": "kill",
    "why": "One direct sentence highlighting the specific logistical flaw or why the price drop doesn't justify the loss in seasonal utility.",
    "red_flags": "Specific hidden cost or logistical risk to verify before booking."
  }},
  {{
    "destination": "Exact string from input",
    "verdict": "keep",
    "why": "One direct sentence quantifying the massive value play (e.g., premium amenities/infrastructure unlocked at entry-level pricing).",
    "red_flags": "What must be double-checked immediately (e.g., confirm indoor pool heating or kids club off-season hours)."
  }}
]

Remember: An empty or all-kill batch for today's run is the standard statistical outcome. Keep only the highest utility-to-price plays."""


# ── Response schemas (Gemini response_format) ────────────────────────────────
# Passed to _gemini() via llm(response_schema=...) to constrain output to valid
# JSON. The Anthropic path ignores these — prompt engineering suffices there.
# Keep in sync with the JSON schemas documented in FIND_PROMPT / SKEPTIC_PROMPT.

STAGE1_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "score":       {"type": "integer"},
                    "type":        {"type": "string"},
                    "window":      {"type": "string"},
                    "reason":      {"type": "string"},
                    "confidence":  {"type": "string"},
                },
                "required": ["destination", "score", "type", "window", "reason", "confidence"],
            },
        },
    },
    "required": ["candidates"],
}

STAGE2_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "destination": {"type": "string"},
            "verdict":     {"type": "string", "enum": ["keep", "kill"]},
            "why":         {"type": "string"},
            "red_flags":   {"type": "string"},
        },
        "required": ["destination", "verdict", "why", "red_flags"],
    },
}