"""memory.py — self-improving price memory for the diamond finder.

state/memory.json structure:
  baselines: {destination|season → {realistic_price_eur, note, source, updated}}
  ledger:    [{date, destination, window, type, claimed_price, verdict,
               actual_price, source, note}]

Ledger is capped to MAX_LEDGER_ENTRIES entries and MAX_LEDGER_DAYS days.
summarize_for_prompt() produces a compact, bounded text block for prompt injection.
"""

import json, datetime as dt, os

STATE_DIR = "state"
_MEMORY_FILE = "memory.json"
_MEMORY_MD   = "memory.md"

MAX_LEDGER_ENTRIES  = 200    # hard cap on ledger rows
MAX_LEDGER_DAYS     = 180    # TTL for ledger entries
MAX_PROMPT_BASELINES = 10    # baselines injected per prompt
MAX_PROMPT_OUTCOMES  = 10    # recent corrections/kills injected per prompt


def _path(name):
    return os.path.join(STATE_DIR, name)


# ── load / save ────────────────────────────────────────────────────────────────

def load():
    """Load memory from state/memory.json. Returns a fresh dict on missing/corrupt file."""
    try:
        with open(_path(_MEMORY_FILE), encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("baselines", {})
        data.setdefault("ledger", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"baselines": {}, "ledger": []}


def save(memory):
    """Save memory to state/memory.json and write state/memory.md digest."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_path(_MEMORY_FILE), "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)
    _write_md(memory)


# ── write ──────────────────────────────────────────────────────────────────────

def record_baseline(memory, destination, season, realistic_price_eur, note="", source=""):
    """Upsert a realistic price baseline for a destination/season pair."""
    key = f"{destination}|{season}"
    memory["baselines"][key] = {
        "realistic_price_eur": realistic_price_eur,
        "note": note,
        "source": source,
        "updated": dt.date.today().isoformat(),
    }


def record_outcome(memory, destination, window, type_, claimed_price, verdict,
                   actual_price=None, source="", note=""):
    """Append one pipeline outcome to the rolling ledger."""
    memory["ledger"].append({
        "date":          dt.date.today().isoformat(),
        "destination":   destination,
        "window":        window,
        "type":          type_,
        "claimed_price": claimed_price,
        "verdict":       verdict,   # confirm | correct | kill | hallucinated | skeptic_kill
        "actual_price":  actual_price,
        "source":        source,
        "note":          note,
    })


def prune(memory):
    """Drop ledger entries older than MAX_LEDGER_DAYS or beyond MAX_LEDGER_ENTRIES."""
    cutoff = (dt.date.today() - dt.timedelta(days=MAX_LEDGER_DAYS)).isoformat()
    memory["ledger"] = [e for e in memory["ledger"] if e.get("date", "") >= cutoff]
    if len(memory["ledger"]) > MAX_LEDGER_ENTRIES:
        memory["ledger"] = memory["ledger"][-MAX_LEDGER_ENTRIES:]
    return memory


# ── prompt summary ─────────────────────────────────────────────────────────────

def summarize_for_prompt(memory, cities=None):
    """Return a compact text block for injection into FIND/SKEPTIC/VERIFY prompts.

    cities: optional list of city/destination strings; when given, only baselines
    whose key contains one of these strings are included (case-insensitive).
    Result is intentionally capped so prompt size stays controlled."""
    lines = []

    # --- Baselines ---
    baselines = memory.get("baselines", {})
    if baselines:
        relevant = []
        for key, b in sorted(baselines.items(), key=lambda kv: kv[1].get("updated", ""), reverse=True):
            if cities:
                dest_part = key.split("|")[0]
                if not any(c.lower() in dest_part.lower() for c in cities):
                    continue
            price = b.get("realistic_price_eur")
            note  = b.get("note", "").strip()
            entry = f"  {key}: realistic ~€{price}/night"
            if note:
                entry += f" — {note}"
            relevant.append(entry)
            if len(relevant) >= MAX_PROMPT_BASELINES:
                break
        if relevant:
            lines.append("Known realistic prices (from past verifications):")
            lines.extend(relevant)

    # --- Recent corrections and kills only (confirmations don't add signal) ---
    ledger = memory.get("ledger", [])
    recent_bad = sorted(
        [e for e in ledger if e.get("verdict") in ("correct", "kill", "hallucinated")],
        key=lambda e: e.get("date", ""),
        reverse=True,
    )[:MAX_PROMPT_OUTCOMES]
    if recent_bad:
        if lines:
            lines.append("")
        lines.append("Recent corrections/kills (past hallucinations — avoid repeating these):")
        for e in recent_bad:
            dest    = e.get("destination", "?")
            win     = e.get("window", "?")
            verdict = e.get("verdict", "?")
            claimed = e.get("claimed_price")
            actual  = e.get("actual_price")
            note    = e.get("note", "").strip()

            parts = [f"  {dest} ({win}): verdict={verdict}"]
            if claimed:
                parts.append(f"claimed €{claimed}")
            if actual:
                parts.append(f"actual €{actual}")
            if note:
                # Keep the note short; it comes from assistant_summary (possibly long)
                parts.append(note[:120])
            lines.append(", ".join(parts))

    return "\n".join(lines) if lines else "(no prior memory)"


# ── human-readable digest ──────────────────────────────────────────────────────

def _write_md(memory):
    today = dt.date.today().isoformat()
    lines = [f"# Diamond Finder Memory — updated {today}", ""]

    baselines = memory.get("baselines", {})
    lines.append(f"## Price Baselines ({len(baselines)} entries)")
    lines.append("")
    if baselines:
        for key, b in sorted(baselines.items()):
            price   = b.get("realistic_price_eur")
            note    = b.get("note", "")
            updated = b.get("updated", "?")
            src     = b.get("source", "")
            lines.append(f"### {key}")
            lines.append(f"**Realistic:** ~€{price}/night &nbsp; **Updated:** {updated}")
            if note:
                lines.append(note)
            if src:
                lines.append(f"_Source: {src}_")
            lines.append("")
    else:
        lines.append("_No baselines recorded yet._")
        lines.append("")

    ledger = memory.get("ledger", [])
    lines.append(f"## Outcome Ledger ({len(ledger)} entries)")
    lines.append("")
    if ledger:
        recent = sorted(ledger, key=lambda e: e.get("date", ""), reverse=True)
        for e in recent[:50]:
            date    = e.get("date", "?")
            dest    = e.get("destination", "?")
            win     = e.get("window", "?")
            verdict = e.get("verdict", "?")
            claimed = e.get("claimed_price")
            actual  = e.get("actual_price")
            note    = e.get("note", "").strip()

            icon = "✅" if verdict == "confirm" else "🔧" if verdict == "correct" else "❌"
            price_str = ""
            if claimed:
                price_str += f" claimed=€{claimed}"
            if actual:
                price_str += f" actual=€{actual}"
            suffix = f" — {note[:100]}" if note else ""
            lines.append(f"- {icon} {date} | {dest} | {win} | {verdict}{price_str}{suffix}")
        if len(ledger) > 50:
            lines.append(f"_... and {len(ledger) - 50} earlier entries_")
    else:
        lines.append("_No outcomes recorded yet._")

    with open(_path(_MEMORY_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
