"""Shared helpers for the diamond-finder pipeline."""

import os, json, ssl, smtplib, datetime as dt
from email.message import EmailMessage

# ---------------------------- LLM provider ----------------------------
# Intentionally absent. Every LLM call goes through the `llm-chain` package:
# `import llm_chain as L; L.call_llm(...)`.
#
# Removed in that migration: llm(), _gemini(), _gemini_search(), _anthropic(),
# _post_with_retry(), resolved_provider(), and the wall-clock budget helpers
# RUN_DEADLINE / set_run_deadline() / _budget_left(). The budget is not lost -- it is
# llm_chain's LLM_TOTAL_BUDGET_SECONDS (set to the same 660 in daily.yml), whose clock
# starts at the FIRST call_llm rather than at import, so hotel grounding and state IO no
# longer eat into the LLM budget. The one-shot GEMINI_FALLBACK_MODEL_MAP hop became a real
# fallback chain. See config.py's "Run resilience" note for the full mapping.
#
# This module now owns email and state IO only.

STATE_DIR = "state"


# ------------------------------ Email ------------------------------

def send_email(subject, html, text):
    """Send a plain + HTML email. SMTP_HOST/USER/PASS must be set in env."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw   = os.environ["SMTP_PASS"]
    to   = os.environ.get("EMAIL_TO", user)
    frm  = os.environ.get("EMAIL_FROM", user)
    msg  = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = frm
    msg["To"]      = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


# ------------------------------ State ------------------------------

def parse_json_block(text):
    """Strip markdown fences and parse the outermost JSON value the model returned,
    choosing object vs array by whichever bracket appears first."""
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    starts = [(t.find(c), c) for c in ("[", "{") if t.find(c) != -1]
    if not starts:
        return None
    _, open_c = min(starts)
    close_c = "]" if open_c == "[" else "}"
    i, j = t.find(open_c), t.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def load_json(name, default):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def today_iso():
    return dt.date.today().isoformat()
