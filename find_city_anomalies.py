"""
find_city_anomalies.py  —  Diamond Finder (daily, LLM-only, no Apify)

Three-stage gate:
  Stage 1 (find):    one llm() call with web search. Asks for travel-arbitrage
                     candidates scored 0-100 across hotels, cruises, flight fares,
                     packages, and currency plays reachable from Plovdiv.
  Stage 2 (skeptic): one llm() call, no search. Hostile reviewer confirms only
                     genuinely exceptional candidates (verdict: keep / kill).
  Stage 3 (verify):  one llm() call per Stage-2 survivor, with web search.
                     Grounds the deal in real prices at specific bookable dates;
                     corrects or kills hallucinations. (verdict: confirm/correct/kill)

Outputs every run:
  state/city_signals.json  — Stage 1 candidate list (hunt=False always; schema kept for reference)
  state/city_signals.md    — human-readable log including Stage 3 outcomes; useful even on silent days
  state/signals_seen.json  — anti-spam TTL state, committed by CI

Emails immediately when diamonds survive all three stages. Silence is the normal outcome.
"""

import json, datetime as dt
import config as C
import common as X
import memory as M


# --- anti-spam state helpers ---

def load_seen():
    return X.load_json("signals_seen.json", {"seen": {}, "monthly_count": {}})


def prune_seen(state):
    cutoff = (dt.date.today() - dt.timedelta(days=C.SIGNAL_TTL_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    return state


def seen_key(destination, window):
    return f"{destination}|{window}"


def is_already_seen(state, destination, window):
    return seen_key(destination, window) in state.get("seen", {})


def mark_seen(state, destination, window):
    state.setdefault("seen", {})[seen_key(destination, window)] = X.today_iso()


def this_month():
    return dt.date.today().strftime("%Y-%m")


def monthly_email_count(state):
    return state.get("monthly_count", {}).get(this_month(), 0)


def increment_monthly(state):
    state.setdefault("monthly_count", {})[this_month()] = monthly_email_count(state) + 1


# --- email builders ---

def build_email_html(diamonds, month_count):
    rows = ""
    for d in diamonds:
        type_label = d.get("type", "").replace("_", " ").title()
        summary = d.get("assistant_summary") or d.get("reason", "")

        # Options list — each with dates, price, and a booking link or how-to-book text
        opts_html = ""
        options = d.get("options") or []
        if options:
            items = ""
            for opt in options:
                dates = opt.get("dates", "")
                pn = opt.get("price_per_night_eur")
                total = opt.get("total_eur")
                url = opt.get("booking_url") or ""
                source = opt.get("source", "")
                price_str = f"€{pn}/night · €{total} total" if (pn is not None and total is not None) else ""
                if url:
                    book_part = f"<a href='{url}' style='color:#1a56db;text-decoration:none'>Book now</a>"
                    src_note = (
                        f" &nbsp;<span style='color:#999;font-size:12px'>({source})</span>"
                        if source else ""
                    )
                else:
                    how = d.get("how_to_book") or source or "see grounding below"
                    book_part = f"<span style='color:#555'>{how}</span>"
                    src_note = ""
                cells = " &nbsp;·&nbsp; ".join(p for p in [dates, price_str, book_part + src_note] if p)
                items += f"<li style='margin:5px 0;font-size:14px'>{cells}</li>"
            opts_html = f"<ul style='margin:6px 0 6px 20px;padding:0'>{items}</ul>"
        elif d.get("how_to_book"):
            opts_html = (
                f"<div style='font-size:14px;color:#444;margin:6px 0'>"
                f"<b>How to book:</b> {d['how_to_book']}</div>"
            )

        grounding_html = (
            f"<div style='font-size:12px;color:#777;margin:4px 0'>Source: {d['grounding']}</div>"
            if d.get("grounding") else ""
        )
        red_flags_html = (
            f"<div style='font-size:13px;color:#c00;margin:4px 0'>Red flags: {d['red_flags']}</div>"
            if d.get("red_flags") else ""
        )

        rows += (
            f"<tr><td style='padding:14px 0;border-bottom:1px solid #eee'>"
            f"<div style='font-size:17px;font-weight:bold'>{d['destination']}</div>"
            f"<div style='font-size:13px;color:#777;margin:3px 0'>"
            f"{type_label} &nbsp;·&nbsp; {d.get('window', '')}</div>"
            f"<div style='font-size:14px;color:#222;margin:6px 0'>{summary}</div>"
            f"{opts_html}"
            f"{grounding_html}"
            f"{red_flags_html}"
            f"</td></tr>"
        )

    conscience = ""
    if month_count >= 3:
        conscience = (
            f"<p style='color:#999;font-size:12px;margin-top:16px'>"
            f"Note: {month_count} email(s) sent this month — firing more than usual. "
            f"All are genuine finds, but worth checking if the threshold needs tuning.</p>"
        )
    return (
        f"<div style='font-family:system-ui,sans-serif;max-width:640px;padding:8px'>"
        f"<h2 style='margin-bottom:4px'>Diamond Finder</h2>"
        f"<p style='color:#555;margin:0 0 16px'>"
        f"{len(diamonds)} exceptional travel window(s) confirmed today</p>"
        f"<table style='width:100%;border-collapse:collapse'>{rows}</table>"
        f"{conscience}"
        f"<p style='color:#bbb;font-size:11px;margin-top:16px'>"
        f"Silence is the normal outcome. This fired because something is genuinely unusual. "
        f"Verify before booking.</p>"
        f"</div>"
    )


def build_email_text(diamonds):
    parts = []
    for d in diamonds:
        summary = d.get("assistant_summary") or d.get("reason", "")
        lines = [
            f"{d['destination']} ({d.get('type', '')})",
            f"Window: {d.get('window', '')}",
            summary,
        ]
        options = d.get("options") or []
        if options:
            lines.append("Options:")
            for opt in options:
                dates = opt.get("dates", "")
                pn = opt.get("price_per_night_eur")
                total = opt.get("total_eur")
                url = opt.get("booking_url") or ""
                source = opt.get("source", "")
                price_str = f"€{pn}/night · €{total} total" if (pn is not None and total is not None) else ""
                if url:
                    book_str = url
                    src_note = f" ({source})" if source else ""
                else:
                    book_str = d.get("how_to_book") or source or ""
                    src_note = ""
                cells = " · ".join(p for p in [dates, price_str, book_str + src_note] if p)
                lines.append(f"  - {cells}")
        elif d.get("how_to_book"):
            lines.append(f"How to book: {d['how_to_book']}")
        if d.get("grounding"):
            lines.append(f"Source: {d['grounding']}")
        if d.get("red_flags"):
            lines.append(f"Red flags: {d['red_flags']}")
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)


# --- markdown log ---

def write_md(today, candidates, diamonds, stage3_results=None, over_ceiling=None):
    diamond_dests = {d["destination"] for d in diamonds}
    over_ceiling_dests = {c["destination"] for c in (over_ceiling or [])}
    lines = [f"# Diamond Finder — {today}", ""]
    if not candidates:
        lines.append("_No candidates found today._")
    else:
        oc_count = len(over_ceiling or [])
        lines.append(
            f"_Stage 1: {len(candidates)} candidate(s). "
            f"{len(diamonds)} Stage-2 diamond(s). "
            + (f"{oc_count} over-ceiling (logged only). " if oc_count else "")
            + f"{len(stage3_results or [])} Stage-3 verified._"
        )
        lines.append("")
        for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
            dest = c.get("destination", "?")
            if dest in diamond_dests:
                marker = " 💎"
            elif dest in over_ceiling_dests:
                marker = " 🔒"
            else:
                marker = ""
            score = c.get("score", 0)
            conf = c.get("confidence", "?")
            est = c.get("est_price_eur")
            est_str = f" · est €{est}/night" if est is not None else ""
            lines.append(f"### {dest}{marker} — {score}/100 ({conf}){est_str}")
            lines.append(
                f"**Type:** {c.get('type', '?')} &nbsp; **Window:** {c.get('window', '?')}"
            )
            lines.append(f"{c.get('reason', '')}")
            if dest in over_ceiling_dests:
                ceiling = C.get_price_ceiling(dest)
                lines.append(f"_🔒 Over ceiling — est €{est}/night > €{ceiling} ceiling. Logged only, not emailed._")
            lines.append("")
    if stage3_results:
        lines.append("## Stage 3 Verification")
        lines.append("")
        for r in stage3_results:
            verdict3 = r.get("verdict", "?")
            icon = "✅" if verdict3 == "confirm" else "🔧" if verdict3 == "correct" else "❌"
            dest3 = r.get("destination", "?")
            conf3 = r.get("confidence", "?")
            lines.append(f"### {icon} {dest3} — {verdict3.upper()} (confidence: {conf3})")
            if r.get("assistant_summary"):
                lines.append(f"**Summary:** {r['assistant_summary']}")
            opts = r.get("options", [])
            if opts:
                lines.append("**Options:**")
                for opt in opts:
                    dates = opt.get("dates", "?")
                    pn = opt.get("price_per_night_eur", "?")
                    total = opt.get("total_eur", "?")
                    url = opt.get("booking_url") or ""
                    src = opt.get("source", "")
                    link = f" · [book]({url})" if url else ""
                    src_note = f" · _{src}_" if src else ""
                    lines.append(f"  - {dates} · €{pn}/night · €{total} total{link}{src_note}")
            if r.get("how_to_book"):
                lines.append(f"**How to book:** {r['how_to_book']}")
            if r.get("grounding"):
                lines.append(f"**Grounding:** {r['grounding']}")
            if r.get("_block_reason"):
                lines.append(f"_🔒 Email blocked: {r['_block_reason']}_")
            lines.append("")
    with open("state/city_signals.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- helpers ---

def _dates_in_window(option_dates, candidate_window):
    """Rough sanity check: option dates should fall in the same YYYY-MM as the candidate window.
    If either string can't be parsed to YYYY-MM, let it through (don't block on ambiguity)."""
    import re
    opt_season = M.season_key(option_dates)
    win_season = M.season_key(candidate_window)
    ym = re.compile(r'^\d{4}-\d{2}$')
    if ym.match(opt_season) and ym.match(win_season):
        return opt_season == win_season
    return True


# --- Layer-3 grounding ---

def _ground_llm(diamond, mem_text, today):
    """Layer-3 grounding via LLM concierge + web search (current active implementation)."""
    candidate_json = json.dumps(diamond, ensure_ascii=False, indent=2)
    verify_prompt = C.VERIFY_PROMPT.format(
        today=today,
        candidate=candidate_json,
        memory=mem_text,
    )
    raw3 = X.llm(
        messages=[{"role": "user", "content": verify_prompt}],
        model=C.MODEL_VERIFY, max_tokens=C.MAX_TOKENS_VERIFY, want_search=True,
        response_schema=C.STAGE3_RESPONSE_SCHEMA,
        provider=C.PROVIDER_VERIFY,
    )
    return X.parse_json_block(raw3) or {}


# ── GROUNDING SEAM ──────────────────────────────────────────────────────────
# ground_deal is the active Layer-3 grounding function. Defaults to the apidojo
# Booking.com provider; falls back to _ground_llm on any import or runtime failure.
# Set HOTEL_PROVIDER="" to force LLM-only grounding.

def _resolve_ground_deal():
    if (C.HOTEL_PROVIDER or "").strip().lower() == "apidojo":
        try:
            from providers import ground_api
            return ground_api          # ground_api falls back to _ground_llm at runtime
        except Exception as e:
            print(f"  [providers] import failed, using LLM grounding: {e}")
    return _ground_llm

ground_deal = _resolve_ground_deal()


# --- main ---

def main():
    today = X.today_iso()
    print(f"=== Diamond Finder — {today} | provider: {X.PROVIDER} | find: {C.MODEL_FIND} | skeptic: {C.MODEL_SKEPTIC} | verify: {C.MODEL_VERIFY} ===")

    # Load memory once; inject into all three stage prompts
    mem = M.load()
    mem_text = M.summarize_for_prompt(mem)

    # Stage 1: find candidates with web search
    print("Stage 1: calling LLM with web search...")
    try:
        raw1 = X.llm(
            messages=[{"role": "user", "content": C.FIND_PROMPT.format(
                today=today, cities=C.cities_prompt_text(), memory=mem_text
            )}],
            model=C.MODEL_FIND, max_tokens=C.MAX_TOKENS_FIND, want_search=True,
            response_schema=C.STAGE1_RESPONSE_SCHEMA,
            provider=C.PROVIDER_FIND,
        )
        candidates = (X.parse_json_block(raw1) or {}).get("candidates", [])
    except Exception as e:
        print(f"Stage 1 failed: {e} — treating as 0 candidates (silent day)")
        candidates = []
    print(f"Stage 1: {len(candidates)} candidate(s) returned")
    for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
        print(f"  {c.get('score', '?'):>3}/100  {c.get('destination', '?')}  [{c.get('type', '?')}]")

    high_score = [c for c in candidates if c.get("score", 0) >= C.STAGE1_MIN_SCORE]
    # Ceiling gate: candidates priced above their country ceiling are logged + recorded,
    # but never forwarded to Stage 2/3. If est_price_eur is missing, let through.
    stage2_candidates, over_ceiling = [], []
    for c in high_score:
        est = c.get("est_price_eur")
        ceiling = C.get_price_ceiling(c.get("destination", ""))
        if est is not None and est > ceiling:
            over_ceiling.append(c)
        else:
            stage2_candidates.append(c)
    print(f"Stage 1 gate (>= {C.STAGE1_MIN_SCORE}): {len(high_score)} above threshold, "
          f"{len(over_ceiling)} over-ceiling, {len(stage2_candidates)} forwarded to skeptic")

    # Record over-ceiling candidates in memory now (before Stage 2)
    for c in over_ceiling:
        ceiling = C.get_price_ceiling(c.get("destination", ""))
        M.record_outcome(
            mem, c.get("destination", ""), c.get("window", ""), c.get("type", ""),
            claimed_price=c.get("est_price_eur"),
            verdict="over_ceiling",
            source=f"est_price_eur {c.get('est_price_eur')} > ceiling {ceiling}",
            note=M._clip(c.get("reason", ""), 200),
        )

    # Stage 2: skeptic review, no search
    diamonds = []
    if stage2_candidates:
        print("Stage 2: calling skeptic LLM...")
        skeptic = C.SKEPTIC_PROMPT.format(
            today=today,
            min_score=C.STAGE1_MIN_SCORE,
            candidates=json.dumps(stage2_candidates, ensure_ascii=False, indent=2),
            memory=mem_text,
        )
        try:
            raw2 = X.llm(
                messages=[{"role": "user", "content": skeptic}],
                model=C.MODEL_SKEPTIC, max_tokens=C.MAX_TOKENS_SKEPTIC, want_search=False,
                response_schema=C.STAGE2_RESPONSE_SCHEMA,
                provider=C.PROVIDER_SKEPTIC,
            )
            verdicts = X.parse_json_block(raw2) or []
        except Exception as e:
            print(f"Stage 2 failed: {e} — treating as 0 diamonds (silent day)")
            verdicts = []
        if not isinstance(verdicts, list):
            verdicts = []
        for v in verdicts:
            verdict = v.get("verdict", "?") if isinstance(v, dict) else "?"
            dest = v.get("destination", "?") if isinstance(v, dict) else "?"
            print(f"  {verdict.upper():>4}  {dest}")
            if isinstance(v, dict) and verdict == "keep":
                orig = next(
                    (c for c in stage2_candidates if c.get("destination") == v.get("destination")), {}
                )
                if orig:
                    diamonds.append({**orig, "why": v.get("why", ""), "red_flags": v.get("red_flags", "")})
        # Record skeptic kills in memory so future runs learn from them
        for v in verdicts:
            if not isinstance(v, dict) or v.get("verdict") != "kill":
                continue
            dest = v.get("destination", "")
            orig = next((c for c in stage2_candidates if c.get("destination") == dest), None)
            if orig:
                M.record_outcome(
                    mem, dest, orig.get("window", ""), orig.get("type", ""),
                    claimed_price=orig.get("est_price_eur"),
                    verdict="skeptic_kill",
                    source=v.get("why", ""),
                    note=M._clip(v.get("red_flags") or "", 200),
                )
    print(f"Stage 2: {len(diamonds)} diamond(s)")

    # Stage 3: verify each Stage-2 survivor with a focused web-search call.
    # One ground_deal() call per deal (rare — almost always 0-2 per run).
    # Merges verified fields onto the diamond dict; drops verdict=="kill".
    verified_diamonds = []
    stage3_results = []
    if diamonds:
        print(f"Stage 3: verifying {len(diamonds)} diamond(s)...")
        for diamond in diamonds:
            try:
                result = ground_deal(diamond, mem_text, today)
            except Exception as e:
                print(f"  Stage 3 failed for {diamond.get('destination', '?')}: {e} — treating as kill")
                result = {}
            if not result:
                result = {}
            verdict3 = result.get("verdict", "kill")
            dest3 = diamond.get("destination", "?")
            print(f"  Stage 3 {verdict3.upper():>7}  {dest3}")

            # Email guards: confidence must be medium/high, dates must be in window,
            # and grounded price must be under the country ceiling.
            if verdict3 in ("confirm", "correct"):
                conf3 = result.get("confidence", "low")
                options3 = result.get("options") or []
                first_dates = options3[0].get("dates", "") if options3 else ""
                grounded_price = options3[0].get("price_per_night_eur") if options3 else None
                ceiling = C.get_price_ceiling(diamond.get("destination", ""))
                block_reason = None
                if conf3 == "low":
                    block_reason = "low confidence"
                elif not _dates_in_window(first_dates, diamond.get("window", "")):
                    block_reason = f"dates out of window ({first_dates!r} vs window {diamond.get('window', '')!r})"
                elif grounded_price is not None and grounded_price > ceiling:
                    block_reason = f"grounded €{grounded_price} > ceiling €{ceiling}"
                if block_reason:
                    result["_block_reason"] = block_reason
                    print(f"  Stage 3 email blocked: {block_reason}")
                else:
                    verified_diamonds.append({**diamond, **{
                        "verdict": verdict3,
                        "options": result.get("options", []),
                        "how_to_book": result.get("how_to_book", ""),
                        "grounding": result.get("grounding", ""),
                        "assistant_summary": result.get("assistant_summary", ""),
                        "confidence": result.get("confidence", "low"),
                    }})
            stage3_results.append(result)
    print(f"Stage 3: {len(verified_diamonds)} diamond(s) verified (email-eligible)")

    # Record outcomes and baselines in memory — every run, including silent days.
    # diamonds and stage3_results are parallel lists (same order, same length).
    for diamond, r3 in zip(diamonds, stage3_results):
        dest     = diamond.get("destination", "")
        window   = diamond.get("window", "")
        type_    = diamond.get("type", "")
        verdict3 = r3.get("verdict", "kill") if r3 else "kill"
        options  = (r3.get("options") or []) if r3 else []
        actual_price = options[0].get("price_per_night_eur") if options else None
        source3  = (options[0].get("source", "") if options
                    else (r3.get("grounding", "") if r3 else ""))
        summary  = M._clip((r3.get("assistant_summary") or "") if r3 else "", 200)

        M.record_outcome(
            mem, dest, window, type_,
            claimed_price=diamond.get("est_price_eur"),
            verdict=verdict3,
            actual_price=actual_price,
            source=source3,
            note=summary,
        )
        # Only write a baseline when confidence is high AND grounded dates are in-window.
        # Out-of-window or low-confidence verifications produce unreliable price data.
        conf3 = (r3.get("confidence", "low") if r3 else "low")
        first_dates = options[0].get("dates", "") if options else ""
        if (verdict3 in ("confirm", "correct") and actual_price
                and conf3 == "high"
                and _dates_in_window(first_dates, window)):
            season = M.season_key(first_dates or window)
            M.record_baseline(mem, dest, season, actual_price,
                              note=M._clip(summary, 300), source=source3)

    M.prune(mem)
    M.save(mem)
    print(f"Memory updated: {len(mem['baselines'])} baseline(s), {len(mem['ledger'])} ledger entry(s)")

    # Write city_signals.json — hunt=False always; field kept for schema compatibility
    # "anomaly" only for deals that survived all three stages (not just Stage-2)
    verified_dests = {d["destination"] for d in verified_diamonds}
    signals = [
        {
            "city": c.get("destination", ""),
            "window": c.get("window", ""),
            "reason": c.get("reason", ""),
            "type": "anomaly" if c.get("destination") in verified_dests else "reminder",
            "confidence": c.get("confidence", "low"),
            "hunt": False,
        }
        for c in candidates
    ]
    X.save_json("city_signals.json", {"generated": today, "signals": signals})

    # Write markdown every run regardless of email outcome
    write_md(today, candidates, diamonds, stage3_results, over_ceiling=over_ceiling)

    # Anti-spam check + email — uses verified_diamonds (Stage-3 survivors only)
    seen_state = load_seen()
    seen_state = prune_seen(seen_state)

    new_diamonds = [
        d for d in verified_diamonds
        if not is_already_seen(seen_state, d["destination"], d["window"])
    ]
    print(f"New (not seen within {C.SIGNAL_TTL_DAYS}d TTL): {len(new_diamonds)}")

    if new_diamonds:
        to_email = new_diamonds[:C.MAX_EMAILS_PER_RUN]
        month_count = monthly_email_count(seen_state)
        subject = f"Diamond Find: {len(to_email)} exceptional travel window(s) — {today}"
        html = build_email_html(to_email, month_count)
        text = build_email_text(to_email)
        try:
            X.send_email(subject, html, text)
            for d in to_email:
                mark_seen(seen_state, d["destination"], d["window"])
            increment_monthly(seen_state)
            print(f"Emailed {len(to_email)} diamond(s)")
        except Exception as e:
            print(f"Email failed: {e}")
    else:
        print("No new diamonds to email — silence is correct")

    X.save_json("signals_seen.json", seen_state)


if __name__ == "__main__":
    main()
