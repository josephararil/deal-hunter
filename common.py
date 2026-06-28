"""Shared helpers for the diamond-finder pipeline."""

import os, json, ssl, smtplib, datetime as dt, time
from email.message import EmailMessage
import requests
import config as C

# ---------------------------- LLM provider ----------------------------

PROVIDER          = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATE_DIR = "state"

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4]  # seconds between attempts 1→2 and 2→3


def _post_with_retry(url, headers, json_body, timeout=180):
    """POST with exponential backoff on transient errors (5xx, 429, network).
    Auth failures (401, 403) and client errors (400, 422) are returned immediately."""
    for attempt in range(_MAX_RETRIES):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if r.status_code not in _RETRY_STATUSES or attempt == _MAX_RETRIES - 1:
                return r
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [retry {attempt + 1}/{_MAX_RETRIES}] HTTP {r.status_code}, retrying in {delay}s")
            time.sleep(delay)
        except requests.exceptions.RequestException as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [retry {attempt + 1}/{_MAX_RETRIES}] {type(exc).__name__}: {exc}, retrying in {delay}s")
            time.sleep(delay)


def llm(messages, model, max_tokens=2000, want_search=False, response_schema=None):
    """Single entry point for all LLM calls. Returns plain text.
    messages is a list of {"role", "content"} dicts with string content.
    response_schema: Gemini only — JSON Schema dict added as response_format."""
    if PROVIDER == "gemini":
        return _gemini(messages, model, max_tokens, want_search, response_schema)
    return _anthropic(messages, model, max_tokens, want_search)


def _anthropic(messages, model, max_tokens, want_search):
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if want_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                          "max_uses": C.WEB_SEARCH_MAX_USES}]
    r = _post_with_retry("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json_body=body)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()


def _gemini(messages, model, max_tokens, want_search, response_schema=None):
    gmodel = C.GEMINI_MODEL_MAP.get(model, "gemini-3.5-flash")
    text = "\n\n".join(m["content"] for m in messages)
    body = {
        "model": gmodel,
        "input": text,
        "generation_config": {
            "thinking_level": "high",
            "maxOutputTokens": max_tokens,
        },
    }
    if want_search:
        body["tools"] = [{"type": "google_search"}]
    if response_schema is not None:
        body["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        }
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    r = _post_with_retry(url, headers=headers, json_body=body)
    if r.status_code in (400, 422) and want_search:
        # Gemini rejected google_search; retry without it so Stage 1 still returns
        # signals from reasoning alone rather than crashing.
        body.pop("tools", None)
        r = _post_with_retry(url, headers=headers, json_body=body)
    r.raise_for_status()
    parts = []
    for step in r.json().get("steps", []):
        if step.get("type") == "model_output":
            for block in step.get("content", []):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "".join(parts).strip()


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
        with open(os.path.join(STATE_DIR, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def today_iso():
    return dt.date.today().isoformat()
