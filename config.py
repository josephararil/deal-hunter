"""Shared configuration for all three pipelines. Edit freely."""

# city -> (min_nights, max_nights). Each crawl picks a random length in range.
# Minimums encode "worth the trip": longer for far places.
CITIES = {
    # --- Bulgaria, by car (<3h) ---
    "Asenovgrad, Bulgaria": (1, 3),  "Banya, Bulgaria": (2, 4),
    "Bansko, Bulgaria": (2, 7),      "Burgas, Bulgaria": (2, 7),
    "Chiflik, Bulgaria": (2, 4),     "Hisarya, Bulgaria": (2, 4),
    "Koprivshtitsa, Bulgaria": (1, 3), "Nessebar, Bulgaria": (2, 7),
    "Pazardzhik, Bulgaria": (1, 3),  "Pamporovo, Bulgaria": (2, 7),
    "Smolyan, Bulgaria": (2, 5),     "Sofia, Bulgaria": (1, 4),
    "Sozopol, Bulgaria": (2, 7),     "Stara Zagora, Bulgaria": (1, 3),
    "Veliko Tarnovo, Bulgaria": (2, 4),
    # --- Greece, by car ---
    "Alexandroupoli, Greece": (2, 5), "Kavala, Greece": (2, 5),
    "Komotini, Greece": (2, 4),       "Xanthi, Greece": (2, 4),
    # --- Turkey ---
    "Edirne, Turkey": (2, 3),         "Istanbul, Turkey": (3, 6),
    # --- by low-cost flight (min >= 2: a 1-night trip isn't worth the flight) ---
    "Bari, Italy": (3, 7),   "Milan, Italy": (2, 7),  "Naples, Italy": (3, 7),
    "Rome, Italy": (3, 7),   "Bratislava, Slovakia": (2, 7), "Vienna, Austria": (2, 7),
    "Athens, Greece": (3, 7), "Budapest, Hungary": (2, 7), "Krakow, Poland": (3, 7),
    "Belgrade, Serbia": (2, 5), "London, United Kingdom": (3, 7),
    "Birmingham, United Kingdom": (3, 7), "Manchester, United Kingdom": (3, 7),
    # --- suggested additions (uncomment if you'd actually go) ---
    # "Thessaloniki, Greece": (3, 6),
    # "Skopje, North Macedonia": (2, 5),
}

# Guests
ADULTS, CHILDREN, ROOMS = 2, 1, 1
CHILDREN_AGES = [4]
CURRENCY  = "EUR"
PROXY_GEO = "BG"

# Quality
MIN_REVIEW_SCORE = 8.0     # HARD floor for what gets shown to you; never relaxed
MIN_REVIEW_COUNT = 200     # excludes fake / brand-new listings
BASELINE_MIN_REVIEWS = 30  # looser floor for baseline medians (we want the true class norm)

# Detection (hunt.py)
OUTLIER_Z       = -3.5     # crazy-manager detector: robust z within same-class peers
MIN_EUR_BELOW   = 40.0     # min EUR/night below peers OR below seasonal baseline to qualify
MARKET_DROP_PCT = 0.25     # market-drop detector: class median this far below its seasonal norm
LLM_CONFIDENCE  = 0.7      # keep deals the final LLM is at least this sure about
MIN_PEERS       = 6        # need this many same-class hotels for a trustworthy cross-section

# Digest (hunt.py)
DIGEST_WEEKDAY   = 6       # 0=Mon ... 6=Sun
MAX_DIGEST_ITEMS = 12
SEEN_TTL_DAYS    = 21

# Apify
APIFY_ACTOR      = "voyager~booking-scraper"  # verify field names on the actor's page
SCRAPE_MAX_HUNT  = 300     # deep crawl for the heavy pipeline
SCRAPE_MAX_BASE  = 60      # light crawl for baseline sampling

# Models
MODEL_PLANNER = "claude-sonnet-4-6"   # A: reasoning + web search
MODEL_FILTER  = "claude-sonnet-4-6"   # B: harsh final filter
