"""One-off, idempotent repair for the 2026-08-10 Stage-3 outage and the pre-existing
LLM-seeded-baseline problem it exposed. Run once from the repo root:

    python tools/migrate_memory.py

Rewrites state/memory.json, state/signals_seen.json, state/deals_history.json, and seeds
state/trips.json. Re-running is a no-op (idempotence is checked at the bottom of this file).

Step A purges every baseline whose source is not apidojo-sourced (40 of 73) — those are LLM
concierge guesses, several of which simply restated the candidate's own estimate back with
false confidence. Step B re-keys the 33 apidojo survivors from their old free-text
"destination|season" keys to identity+season keys (33 -> 25, merging 6 duplicate groups),
recovering each property's name from its verification note and keeping a rolling sample list
per merged key. NOTE: pre-migration price history is NOT reconstructed from git — `samples`
starts from what's on disk today, so merged keys start with 1-3 samples rather than a true
history. Step C reverts exactly the five 2026-08-10 outage rows in the ledger, signals_seen,
and deals_history (the run where the scorer's HTTP 503s got coerced into llm_score=0 ->
skip). Step D seeds state/trips.json with {"trips": []} — after this, Invariant T means no
automated process ever writes that file again.
"""

import json
import os
import re
import statistics

STATE_DIR = "state"


def _path(name):
    return os.path.join(STATE_DIR, name)


def _load(name):
    with open(_path(name), encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _identity(name):
    ident = (name or "").lower().replace("&", "and")
    return re.sub(r'[^a-z0-9]+', '', ident)


_NOTE_NAME_RE = re.compile(r'^Verified (.+?)(?:, \d-star)? for ')


def migrate_memory():
    mem = _load("memory.json")
    baselines = mem.get("baselines", {})

    # Idempotence: a baseline already carrying a "samples" list has already been through
    # Step A+B on a prior run — pass it through unchanged rather than re-collapsing its
    # sample history down to a single already-merged price.
    already_migrated = {k: v for k, v in baselines.items() if "samples" in v}
    pending = {k: v for k, v in baselines.items() if "samples" not in v}

    # Step A — purge non-apidojo baselines (only among the not-yet-migrated ones).
    apidojo = {k: v for k, v in pending.items() if "apidojo" in v.get("source", "")}
    purged = len(pending) - len(apidojo)

    # Step B — re-key the apidojo survivors by identity + season, merging collisions.
    groups = {}
    for old_key, b in apidojo.items():
        note = b.get("note", "")
        m = _NOTE_NAME_RE.match(note)
        name = m.group(1) if m else old_key.split("|")[0]
        season = old_key.split("|")[-1] if "|" in old_key else ""
        new_key = f"{_identity(name)}|{season}"
        groups.setdefault(new_key, []).append(b)

    new_baselines = dict(already_migrated)
    for new_key, entries in groups.items():
        samples = [{"price": e.get("realistic_price_eur"),
                    "date": e.get("updated", ""),
                    "source": e.get("source", "")} for e in entries]
        median = round(statistics.median(s["price"] for s in samples), 2)
        latest = max(entries, key=lambda e: e.get("updated", ""))
        if new_key in new_baselines:
            # Collides with an already-migrated key from a prior run — merge samples in.
            existing = new_baselines[new_key]
            existing["samples"] = (existing.get("samples", []) + samples)[-5:]
            existing["realistic_price_eur"] = round(
                statistics.median(s["price"] for s in existing["samples"]), 2)
        else:
            new_baselines[new_key] = {
                "samples": samples,
                "realistic_price_eur": median,
                "note": latest.get("note", ""),
                "source": latest.get("source", ""),
                "updated": latest.get("updated", ""),
            }
    mem["baselines"] = new_baselines

    # Step C — revert the 2026-08-10 outage ledger rows (all four conditions).
    before_ledger = len(mem.get("ledger", []))
    mem["ledger"] = [
        e for e in mem.get("ledger", [])
        if not (e.get("date") == "2026-08-10" and e.get("verdict") == "skip"
                and e.get("llm_score") == 0 and e.get("final_score") == 0)
    ]
    reverted_ledger = before_ledger - len(mem["ledger"])

    _save("memory.json", mem)

    return {
        "baselines_purged": purged,
        "baselines_kept": len(apidojo),
        "baselines_regrouped": len(new_baselines),
        "ledger_reverted": reverted_ledger,
    }


_OUTAGE_SEEN_KEYS = [
    "sanaspahotel|2026-09|skip",
    "premierluxurymountainresort|2026-09|skip",
    "spahotelolymp|2026-09|skip",
    "limaklaradeluxehotelandresort|2026-09|skip",
    "hiltonchaniaoldtownresortandspa|2026-09|skip",
]


def migrate_signals_seen():
    seen_state = _load("signals_seen.json")
    seen = seen_state.get("seen", {})
    removed = [k for k in _OUTAGE_SEEN_KEYS if k in seen]
    for k in _OUTAGE_SEEN_KEYS:
        seen.pop(k, None)
    seen_state["seen"] = seen

    monthly = seen_state.get("monthly_count", {})
    if monthly.get("2026-08") == 2:
        monthly["2026-08"] = 1
    seen_state["monthly_count"] = monthly

    _save("signals_seen.json", seen_state)
    return {"seen_keys_removed": removed}


def migrate_deals_history():
    hist = _load("deals_history.json")
    before = len(hist.get("entries", []))
    hist["entries"] = [e for e in hist.get("entries", []) if e.get("date") != "2026-08-10"]
    removed = before - len(hist["entries"])
    _save("deals_history.json", hist)
    return {"history_entries_removed": removed}


def seed_trips():
    path = _path("trips.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"trips": []}) + "\n")
        return True
    return False


def main():
    print("== Step A+B: purge non-apidojo baselines, re-key survivors ==")
    r1 = migrate_memory()
    print(f"  baselines: purged {r1['baselines_purged']}, kept {r1['baselines_kept']}, "
          f"regrouped to {r1['baselines_regrouped']} keys")
    print(f"  ledger: reverted {r1['ledger_reverted']} outage row(s)")

    print("== Step C: revert signals_seen.json ==")
    r2 = migrate_signals_seen()
    print(f"  seen keys removed: {r2['seen_keys_removed']}")

    print("== Step C: revert deals_history.json ==")
    r3 = migrate_deals_history()
    print(f"  history entries removed: {r3['history_entries_removed']}")

    print("== Step D: seed state/trips.json ==")
    seeded = seed_trips()
    print(f"  seeded: {seeded}")


if __name__ == "__main__":
    main()
