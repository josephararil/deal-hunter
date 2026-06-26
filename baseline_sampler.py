"""
baseline_sampler.py  —  Pipeline 0 (the memory)

Runs daily, cheap, no LLM. Takes a light sample of each city's prices and records the
median per (city | star-class | month) into state/baselines.json. Over a few weeks this
becomes the seasonal reference that lets hunt.py tell a real market-wide drop ("cheap even
for this season") from a boring low season ("cheap because it's always cheap now").

Keeps a small rolling buffer of recent samples per key, so it adapts as the year moves.
"""

import statistics, datetime as dt
import config as C
import common as X

BUFFER = 8          # rolling samples kept per (city|class|month)
BASE_ANCHORS = (14, 45, 90)   # days out — samples three different months each run


def record(baselines, city, stars, month, median):
    key = f"{city}|{stars}|{month}"
    entry = baselines.get(key, {"samples": [], "updated": ""})
    entry["samples"] = (entry["samples"] + [round(median, 2)])[-BUFFER:]
    entry["updated"] = X.today_iso()
    baselines[key] = entry


def main():
    baselines = X.load_json("baselines.json", {})
    today = dt.date.today()

    for city in C.CITIES:
        for days in BASE_ANCHORS:
            ci = today + dt.timedelta(days=days)
            co = ci + dt.timedelta(days=2)        # fixed 2-night probe for a stable unit price
            month = ci.month
            try:
                items = X.scrape(city, ci, co, C.SCRAPE_MAX_BASE)
                hotels = X.normalize(items, 2, C.BASELINE_MIN_REVIEWS)
            except Exception as e:
                print(f"[skip] baseline {city} +{days}d: {e}")
                continue

            # median per star class
            by_class = {}
            for h in hotels:
                by_class.setdefault(h["stars"], []).append(h["per_night"])
            for stars, prices in by_class.items():
                if len(prices) >= C.MIN_PEERS:
                    record(baselines, city, stars, month, statistics.median(prices))

    X.save_json("baselines.json", baselines)
    print(f"baselines.json now holds {len(baselines)} city|class|month cells")


if __name__ == "__main__":
    main()
