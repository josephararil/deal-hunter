"""
find_city_anomalies.py  —  Pipeline A (the planner / "where should I look?")

Cheap, no hotel scraping. One Claude call with web search, given: today's date, the
recurring-pattern priors (patterns.json), and a summary of whatever baseline data exists.
It reasons about where prices are unusually low RIGHT NOW — recurring troughs (post-holiday,
shoulder, local-exodus) and live shocks (events ending, unrest, currency, new routes) — and
emits a ranked list of city signals.

Outputs:
  state/city_signals.json  — machine-readable, consumed by hunt.py
  state/city_signals.md    — human-readable, committed so you can glance at it anytime

This is useful ON ITS OWN: even with the heavy pipeline off, it tells you which city to go
search manually on Booking today.
"""

import json, statistics, datetime as dt
import config as C
import common as X


def baseline_digest():
    """Compact summary of current baselines so the model can spot 'cheap for the season'."""
    baselines = X.load_json("baselines.json", {})
    month = dt.date.today().month
    rows = []
    for key, entry in baselines.items():
        city, stars, m = key.split("|")
        if int(m) == month and entry.get("samples"):
            rows.append(f"{city} [{stars}*]: ~EUR{round(statistics.median(entry['samples']))}/night")
    return "\n".join(sorted(rows)) or "(no baseline data captured yet)"


PLANNER_PROMPT = """Today is {today}. You are a sharp travel-arbitrage analyst helping a \
traveller based near Plovdiv, Bulgaria find destinations whose hotel prices are UNUSUALLY LOW \
right now — so they can grab a great-value trip. They travel as 2 adults + a 4-year-old.

You care about TWO things:
1. Recurring price troughs that are open right now or in the next ~4 weeks (post-holiday \
slumps, shoulder seasons, cities the locals leave, the week after a big event).
2. Live, current shocks suppressing prices right now (an event that just ended, unrest or a \
safety scare, a heatwave, a currency slide making a place cheap for EUR holders, a new \
low-cost route, aggressive new-hotel launch pricing). USE WEB SEARCH to find these — check \
for anything in the candidate cities or their countries in the last few weeks.

Candidate cities (only recommend from these): {cities}

Recurring-pattern priors (hypotheses to confirm or reject, NOT facts):
{patterns}

Current captured baseline medians for THIS month (use to judge 'cheap even for the season'; \
may be sparse early on):
{baselines}

Rules:
- Recommend a city ONLY if there's a real reason prices are low NOW or imminently. Do not pad.
- Distinguish "cheap because it's always cheap this month" (low value as a signal) from \
"cheap even for this season / something unusual is happening" (high value). Prefer the latter.
- A recurring trough the traveller already knows (e.g. shoulder-season coast) is still worth \
listing as a REMINDER, but mark it type "reminder". A genuine current anomaly is type "anomaly".
- Be concrete about timing and the reason. Cite what you found via search in the reason text.

Return ONLY a JSON object (no prose, no markdown):
{{"signals": [
  {{"city": <one of the candidate cities, exact string>,
    "window": <when to go, e.g. "next 2-3 weeks" or "Jan 4-12">,
    "reason": <one or two sharp sentences; name the event/cause and cite search findings>,
    "type": "anomaly" | "reminder",
    "confidence": "high" | "medium" | "low",
    "hunt": <true if worth a deep hotel crawl now, false if just a heads-up>}}
]}}
Order signals best-first. Return an empty list if nothing stands out."""


def main():
    with open("patterns.json") as f:
        patterns_raw = f.read()

    prompt = PLANNER_PROMPT.format(
        today=X.today_iso(),
        cities=", ".join(C.CITIES.keys()),
        patterns=patterns_raw,
        baselines=baseline_digest(),
    )

    resp = X.anthropic(
        messages=[{"role": "user", "content": prompt}],
        model=C.MODEL_PLANNER, max_tokens=3000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
    )
    parsed = X.parse_json_block(X.text_of(resp)) or {"signals": []}
    signals = parsed.get("signals", [])

    out = {"generated": X.today_iso(), "signals": signals}
    X.save_json("city_signals.json", out)

    # human-readable
    lines = [f"# City signals — {X.today_iso()}", ""]
    if not signals:
        lines.append("_Nothing unusual stood out today._")
    for s in signals:
        tag = "🔥 ANOMALY" if s.get("type") == "anomaly" else "📌 reminder"
        hunt = " · will deep-crawl" if s.get("hunt") else ""
        lines.append(f"## {s.get('city')} — {tag} ({s.get('confidence')}){hunt}")
        lines.append(f"**When:** {s.get('window')}")
        lines.append(f"{s.get('reason')}")
        lines.append("")
    with open("state/city_signals.md", "w") as f:
        f.write("\n".join(lines))

    print(f"{len(signals)} signal(s); "
          f"{sum(1 for s in signals if s.get('hunt'))} flagged for deep crawl")


if __name__ == "__main__":
    main()
