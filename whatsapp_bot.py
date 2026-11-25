"""WhatsApp interface for Assistant (Meta WhatsApp Cloud API).

Run with:  python whatsapp_bot.py   (listens on http://127.0.0.1:8000, expose it with ngrok)

Flow: Meta -> POST /webhook -> signature check -> 200 right away -> the message is
handled in a background thread (FastAPI's thread pool), which runs the same agent
loop as the terminal app (assistant.chat_turn) and replies through the Graph API.
"""

import hashlib
import hmac
import logging
import os
import secrets
import sys
import threading
from collections import OrderedDict

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response

import assistant
import db

# ---------- configuration (values are never printed or logged) ----------

load_dotenv()

TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
ALLOWED_NUMBERS = {
    n.strip().lstrip("+") for n in os.getenv("ALLOWED_NUMBERS", "").split(",") if n.strip()
}
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v23.0")

REQUIRED = ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN",
            "WHATSAPP_APP_SECRET", "ALLOWED_NUMBERS"]

MAX_CHUNK = 4000            # WhatsApp text limit is 4096; keep a margin
CONFIRM_TIMEOUT = 60        # seconds before an unanswered Run/Cancel counts as Cancel

HELP_TEXT = (
    "I'm Assistant. I can help with file management and device commands on your laptop.\n\n"
    "Commands:\n"
    "• new: start a new session\n"
    "• sessions: pick a recent session to resume\n"
    "• current: show the active session\n"
    "• help: show this message\n"
    "• /search <question>: search the web (e.g. /search latest news from SMU university)\n\n"
    "Anything else is sent to the assistant."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whatsapp_bot")
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log every Graph API URL

app = FastAPI()
http = httpx.Client(
    base_url=f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}",
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=20,
)


def mask(phone):
    """Phone number safe for logs: only the last 4 digits."""
    return "***" + phone[-4:]


# ---------- per-number state (in memory) ----------

class Conversation:
    def __init__(self):
        self.session_id = None                      # created in the DB on the first message
        self.messages = assistant.build_context([])  # model context
        self.busy = threading.Lock()                # one agent turn at a time per number
        self.pending = None                          # waiting Run/Cancel: {"token", "event", "ok"}


conversations = {}
conversations_lock = threading.Lock()


def get_conversation(phone):
    with conversations_lock:
        if phone not in conversations:
            conversations[phone] = Conversation()
        return conversations[phone]


# Message ids already handled (Meta can deliver the same webhook more than once).
seen_ids = OrderedDict()
seen_lock = threading.Lock()


def already_seen(message_id):
    with seen_lock:
        if message_id in seen_ids:
            return True
        seen_ids[message_id] = True
        if len(seen_ids) > 1000:
            seen_ids.popitem(last=False)
        return False


# ---------- Graph API helpers ----------

def _post(payload):
    try:
        r = http.post("/messages", json=payload)
        if r.status_code >= 400:
            # Body contains Meta's error description, never our token.
            log.warning("Graph API error %s: %s", r.status_code, r.text[:300])
    except httpx.HTTPError as e:
        log.warning("Graph API request failed: %s", type(e).__name__)


def send_text(to, text):
    """Send a text message, split into chunks if it is too long."""
    for chunk in split_message(text):
        _post({"messaging_product": "whatsapp", "to": to, "type": "text",
               "text": {"body": chunk, "preview_url": False}})


def send_typing(message_id):
    """Mark the incoming message as read and show "typing..." (lasts up to ~25 s)."""
    _post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id,
           "typing_indicator": {"type": "text"}})


def send_buttons(to, body, buttons):
    """Interactive reply-button message. buttons = [(id, title), ...] (max 3, title <= 20 chars)."""
    _post({
        "messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}}
                                   for i, t in buttons]},
        },
    })


def send_list(to, body, button, rows):
    """Interactive list message. rows = [(id, title, description), ...] (max 10)."""
    _post({
        "messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {
                "button": button[:20],
                "sections": [{
                    "title": "Recent sessions",
                    "rows": [{"id": i, "title": t[:24], "description": d[:72]}
                             for i, t, d in rows[:10]],
                }],
            },
        },
    })


def split_message(text, limit=MAX_CHUNK):
    """Split long text into chunks <= limit, preferring to cut at newlines."""
    text = text or ""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return [c for c in chunks if c.strip()] or ["(empty reply)"]


# ---------- webhook ----------

@app.get("/webhook")
def verify(request: Request):
    """Meta's verification handshake when you save the callback URL in the dashboard."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and hmac.compare_digest(
            p.get("hub.verify_token", ""), VERIFY_TOKEN):
        log.info("Webhook verified by Meta.")
        return Response(content=p.get("hub.challenge", ""), media_type="text/plain")
    log.warning("Webhook verification failed.")
    return Response(status_code=403)


def valid_signature(body: bytes, header: str) -> bool:
    """Check X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, raw body)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    log.info("Webhook POST received (%d bytes)", len(body))
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        log.warning("Rejected webhook with invalid signature.")
        return Response(status_code=403)

    try:
        data = await request.json()
    except ValueError:
        return Response(status_code=400)

    # Payload: entry[] -> changes[] -> value -> messages[] (statuses[] are delivery receipts: ignored)
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                background.add_task(handle_message, msg)  # runs after the 200 is sent
    return Response(status_code=200)


# ---------- message handling (runs in a worker thread) ----------

def handle_message(msg):
    phone = msg.get("from", "")
    message_id = msg.get("id", "")
    if not message_id or already_seen(message_id):
        return
    if phone not in ALLOWED_NUMBERS:
        log.warning("Ignored message from unauthorized number %s", mask(phone))
        return

    conv = get_conversation(phone)
    kind = msg.get("type")

    if kind == "interactive":
        reply = msg.get("interactive", {})
        reply_id = (reply.get("button_reply") or reply.get("list_reply") or {}).get("id", "")
        if reply_id.startswith("confirm:"):
            handle_confirm_reply(phone, conv, reply_id)
        elif reply_id.startswith("session:"):
            resume_session(phone, conv, reply_id.split(":", 1)[1])
        return

    if kind != "text":
        send_text(phone, "Sorry, only text messages are supported.")
        return

    text = msg.get("text", {}).get("body", "").strip()
    if not text:
        return
    command = text.lower()

    # Read-only commands work even while the agent is busy.
    if command == "help":
        send_text(phone, HELP_TEXT)
        return
    if command == "current":
        show_current(phone, conv)
        return

    if not conv.busy.acquire(blocking=False):
        send_text(phone, "Still working on your previous message, please wait…")
        return
    try:
        if command == "new":
            conv.session_id = None
            conv.messages = assistant.build_context([])
            send_text(phone, "New session started. What can I do for you?")
        elif command == "sessions":
            show_sessions(phone, conv)
        else:
            run_agent(phone, conv, text, message_id)
    finally:
        conv.busy.release()


def show_current(phone, conv):
    if conv.session_id is None:
        send_text(phone, "No active session yet. Send a message to start one.")
        return
    s = db.get_session(conv.session_id)
    send_text(phone, f"Current session: {s['title']}" if s else "No active session.")


def show_sessions(phone, conv):
    sessions = db.list_whatsapp_sessions(phone, limit=10)
    if not sessions:
        send_text(phone, "No sessions yet. Send a message to start one.")
        return
    rows = []
    for s in sessions:
        date = s["updated_at"].replace("T", " ")[:16]
        marker = " (current)" if s["id"] == conv.session_id else ""
        rows.append((f"session:{s['id']}", s["title"], f"{date}{marker}  {s['title']}"))
    send_list(phone, "Choose a session to resume:", "Sessions", rows)


def resume_session(phone, conv, raw_id):
    try:
        session_id = int(raw_id)
    except ValueError:
        return
    if db.whatsapp_session_owner(session_id) != phone:  # only your own sessions
        send_text(phone, "That session is not available.")
        return
    if not conv.busy.acquire(blocking=False):
        send_text(phone, "Still working on your previous message, please wait…")
        return
    try:
        rows = db.load_messages(session_id)
        conv.session_id = session_id
        conv.messages = assistant.build_context(rows)
        title = db.get_session(session_id)["title"]
        send_text(phone, f"Resumed session: {title} ({len(rows)} messages). Go ahead.")
    finally:
        conv.busy.release()


def run_agent(phone, conv, text, message_id):
    """One normal message through the shared agent loop (same as the terminal app)."""
    if conv.session_id is None:
        conv.session_id = db.create_session(db.make_title(text))
        db.link_whatsapp_session(conv.session_id, phone)
    session_id = conv.session_id
    db.add_message(session_id, "user", text)
    conv.messages.append({"role": "user", "content": text})

    send_text(phone, "Thinking...")
    send_typing(message_id)  # after the status text: sending a message hides the indicator

    def on_event(kind, data):
        # Save everything exactly like app.py does.
        if kind == "assistant":
            db.add_message(session_id, "assistant", data["content"], tool_calls=data["tool_calls"])
        elif kind == "tool_call":
            args = data["arguments"]
            send_text(phone, f"Running: {args.get('command', args)}")
            send_typing(message_id)
        elif kind == "tool_result":
            db.add_message(session_id, "tool", data["content"], tool_name=data["name"])
            send_typing(message_id)  # model is thinking again

    def confirm(command):
        return ask_confirmation(phone, conv, command)

    try:
        reply = assistant.chat_turn(conv.messages, on_event, confirm=confirm)
    except Exception as e:  # e.g. Ollama not running
        log.error("Agent error: %s", type(e).__name__)
        send_text(phone, f"Error: {type(e).__name__}: {e}")
        return
    send_text(phone, reply or "Done.")


# ---------- Run / Cancel confirmation ----------

def ask_confirmation(phone, conv, command):
    """Send Run/Cancel buttons and block this worker thread until answered (or timeout).

    The button tap arrives as a separate webhook call, handled on another thread,
    which sets the event (see handle_confirm_reply).
    """
    token = secrets.token_hex(4)
    pending = {"token": token, "event": threading.Event(), "ok": False}
    conv.pending = pending
    send_buttons(phone, f"Run this command?\n\n{command}",
                 [(f"confirm:{token}:run", "Run"), (f"confirm:{token}:cancel", "Cancel")])
    answered = pending["event"].wait(CONFIRM_TIMEOUT)
    conv.pending = None
    if not answered:
        send_text(phone, "No answer after 60 seconds: command cancelled.")
    return answered and pending["ok"]


def handle_confirm_reply(phone, conv, reply_id):
    _, token, choice = (reply_id.split(":") + ["", ""])[:3]
    pending = conv.pending
    if pending is None or pending["token"] != token:
        send_text(phone, "That confirmation expired.")
        return
    pending["ok"] = choice == "run"
    pending["event"].set()


# ---------- entry point ----------

if __name__ == "__main__":
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print("Missing settings in .env: " + ", ".join(missing))
        sys.exit(1)
    log.info("Starting WhatsApp bot on http://127.0.0.1:8000 (%d allowed number(s))",
             len(ALLOWED_NUMBERS))
    # access_log=False: uvicorn's access log would print query strings (incl. hub.verify_token).
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
