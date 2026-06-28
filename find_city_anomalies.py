"""
find_city_anomalies.py  —  Diamond Finder (daily, LLM-only, no Apify)

Two-stage gate:
  Stage 1 (find):    one llm() call with web search. Asks for travel-arbitrage
                     candidates scored 0-100 across hotels, cruises, flight fares,
                     packages, and currency plays reachable from Plovdiv.
  Stage 2 (skeptic): one llm() call, no search. Hostile reviewer confirms only
                     genuinely exceptional candidates (verdict: keep / kill).

Outputs every run:
  state/city_signals.json  — Stage 1 candidate list (hunt=False always; schema kept for reference)
  state/city_signals.md    — human-readable log; useful even on silent days
  state/signals_seen.json  — anti-spam TTL state, committed by CI

Emails immediately when diamonds survive. Silence is the normal outcome.
"""

import json, datetime as dt
import config as C
import common as X


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
        rows += (
            f"<tr><td style='padding:14px 0;border-bottom:1px solid #eee'>"
            f"<div style='font-size:17px;font-weight:bold'>{d['destination']}</div>"
            f"<div style='font-size:13px;color:#777;margin:3px 0'>"
            f"{type_label} &nbsp;·&nbsp; {d.get('window', '')}</div>"
            f"<div style='font-size:14px;color:#222;margin:6px 0'>{d.get('reason', '')}</div>"
            f"<div style='font-size:14px;color:#1a6a1a;margin:4px 0'>"
            f"<b>Why it's exceptional:</b> {d.get('why', '')}</div>"
            + (f"<div style='font-size:13px;color:#c00;margin:4px 0'>"
               f"Red flags: {d['red_flags']}</div>" if d.get("red_flags") else "")
            + "</td></tr>"
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
        part = (
            f"{d['destination']} ({d.get('type', '')})\n"
            f"Window: {d.get('window', '')}\n"
            f"{d.get('reason', '')}\n"
            f"Why exceptional: {d.get('why', '')}"
        )
        if d.get("red_flags"):
            part += f"\nRed flags: {d['red_flags']}"
        parts.append(part)
    return "\n\n---\n\n".join(parts)


# --- markdown log ---

def write_md(today, candidates, diamonds):
    diamond_dests = {d["destination"] for d in diamonds}
    lines = [f"# Diamond Finder — {today}", ""]
    if not candidates:
        lines.append("_No candidates found today._")
    else:
        lines.append(
            f"_Stage 1: {len(candidates)} candidate(s). "
            f"{len(diamonds)} diamond(s) confirmed._"
        )
        lines.append("")
        for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
            dest = c.get("destination", "?")
            gem = " 💎" if dest in diamond_dests else ""
            score = c.get("score", 0)
            conf = c.get("confidence", "?")
            lines.append(f"### {dest}{gem} — {score}/100 ({conf})")
            lines.append(
                f"**Type:** {c.get('type', '?')} &nbsp; **Window:** {c.get('window', '?')}"
            )
            lines.append(f"{c.get('reason', '')}")
            lines.append("")
    with open("state/city_signals.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --- main ---

def main():
    today = X.today_iso()
    print(f"=== Diamond Finder — {today} | provider: {X.PROVIDER} | find: {C.MODEL_FIND} | skeptic: {C.MODEL_SKEPTIC} ===")

    # Stage 1: find candidates with web search
    print("Stage 1: calling LLM with web search...")
    raw1 = X.llm(
        messages=[{"role": "user", "content": C.FIND_PROMPT.format(today=today, cities=C.cities_prompt_text())}],
        model=C.MODEL_FIND, max_tokens=C.MAX_TOKENS_FIND, want_search=True,
        provider=C.PROVIDER_FIND,
    )
    parsed1 = X.parse_json_block(raw1) or {}
    candidates = parsed1.get("candidates", [])
    print(f"Stage 1: {len(candidates)} candidate(s) returned")
    for c in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
        print(f"  {c.get('score', '?'):>3}/100  {c.get('destination', '?')}  [{c.get('type', '?')}]")

    high_score = [c for c in candidates if c.get("score", 0) >= C.STAGE1_MIN_SCORE]
    print(f"Stage 1 gate (>= {C.STAGE1_MIN_SCORE}): {len(high_score)} forwarded to skeptic")

    # Stage 2: skeptic review, no search
    diamonds = []
    if high_score:
        print("Stage 2: calling skeptic LLM...")
        skeptic = C.SKEPTIC_PROMPT.format(
            today=today,
            min_score=C.STAGE1_MIN_SCORE,
            candidates=json.dumps(high_score, ensure_ascii=False, indent=2),
        )
        raw2 = X.llm(
            messages=[{"role": "user", "content": skeptic}],
            model=C.MODEL_SKEPTIC, max_tokens=C.MAX_TOKENS_SKEPTIC, want_search=False,
            provider=C.PROVIDER_SKEPTIC,
        )
        verdicts = X.parse_json_block(raw2) or []
        if not isinstance(verdicts, list):
            verdicts = []
        for v in verdicts:
            verdict = v.get("verdict", "?") if isinstance(v, dict) else "?"
            dest = v.get("destination", "?") if isinstance(v, dict) else "?"
            print(f"  {verdict.upper():>4}  {dest}")
            if isinstance(v, dict) and verdict == "keep":
                orig = next(
                    (c for c in high_score if c.get("destination") == v.get("destination")), {}
                )
                if orig:
                    diamonds.append({**orig, "why": v.get("why", ""), "red_flags": v.get("red_flags", "")})
    print(f"Stage 2: {len(diamonds)} diamond(s)")

    # Write city_signals.json — hunt=False always; the Apify hunt pipeline is dormant
    diamond_dests = {d["destination"] for d in diamonds}
    signals = [
        {
            "city": c.get("destination", ""),
            "window": c.get("window", ""),
            "reason": c.get("reason", ""),
            "type": "anomaly" if c.get("destination") in diamond_dests else "reminder",
            "confidence": c.get("confidence", "low"),
            "hunt": False,
        }
        for c in candidates
    ]
    X.save_json("city_signals.json", {"generated": today, "signals": signals})

    # Write markdown every run regardless of email outcome
    write_md(today, candidates, diamonds)

    # Anti-spam check + email
    seen_state = load_seen()
    seen_state = prune_seen(seen_state)

    new_diamonds = [
        d for d in diamonds
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
