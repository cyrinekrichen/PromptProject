"""Scope guard: a second model call that checks each request before the agent runs it.

The guard has no tools. It splits the user's request into parts and keeps only
the parts that are file management / device commands. Content the model would
have to invent from its own knowledge (recipes, poems, facts...) is refused, so
the executor agent never even sees that part of the request.
"""
import json
import ollama

GUARD_PROMPT = """You are a request filter for a file management assistant on a Windows computer.
You do NOT execute anything. You only analyze the user's latest request and split it into parts.

The assistant is ONLY allowed to do file management and device commands:
listing, finding, opening, creating, copying, moving, renaming, deleting files and folders,
writing text into files, and launching applications.
It may also answer questions about the computer itself that a command can check:
the current time or date, disk space, battery, computer name, user name, IP address,
running programs, system info. These are ALLOWED (they are answered by running a command,
not from the assistant's knowledge).

CONTENT RULE (most important):
When a request asks to put text into a file (or anywhere), check where the text comes from.
- ALLOWED: text the user typed word for word (e.g. write "hello"), or text that comes from
  the computer itself (e.g. the list of files in a folder, the output of a command).
- REFUSED: text the assistant would have to write from its own knowledge or imagination:
  recipes, poems, stories, essays, jokes, explanations, advice, code, general facts, summaries.
  For these, keep the file operation (create the file EMPTY) and refuse only the content part.

Requests with no file or device action at all (recipes, general knowledge, math, advice)
are fully refused: allowed_request is "". Questions about the computer's own state
(time, date, disk space...) are NOT refused: they are device commands.

Greetings and small talk ("hello", "thanks", "how are you", "bye") are allowed:
set is_greeting to true and copy the message into allowed_request.

Write allowed_request as a clear instruction for the assistant, keeping exact file names,
paths and any word-for-word text from the user. Write each refused part as a short phrase
starting with a verb (e.g. "write a pizza recipe").

EXAMPLES
User: create a file pizza.txt on my desktop with a pizza recipe
{"allowed_request": "Create an EMPTY file pizza.txt on the Desktop. Do not write any content in it.", "refused_parts": ["write a pizza recipe"], "is_greeting": false}

User: create a.txt on my desktop and write hello in it
{"allowed_request": "Create a file a.txt on the Desktop containing exactly: hello", "refused_parts": [], "is_greeting": false}

User: save the list of files in my Documents into docs.txt on my desktop
{"allowed_request": "Save the list of files in ~/Documents into a file docs.txt on the Desktop.", "refused_parts": [], "is_greeting": false}

User: give me a pizza recipe
{"allowed_request": "", "refused_parts": ["give a pizza recipe"], "is_greeting": false}

User: open notepad and write me a poem
{"allowed_request": "Open Notepad.", "refused_parts": ["write a poem"], "is_greeting": false}

User: make a folder called notes and put a file inside explaining how photosynthesis works
{"allowed_request": "Create a folder called notes, and create an EMPTY file inside it. Do not write any content in it.", "refused_parts": ["explain how photosynthesis works"], "is_greeting": false}

User: hello!
{"allowed_request": "hello!", "refused_parts": [], "is_greeting": true}

User: what is the capital of France?
{"allowed_request": "", "refused_parts": ["answer a general knowledge question"], "is_greeting": false}

User: what time is it?
{"allowed_request": "Tell the user the current time on this computer.", "refused_parts": [], "is_greeting": false}

User: what's the date today?
{"allowed_request": "Tell the user today's date on this computer.", "refused_parts": [], "is_greeting": false}

User: how much free space is on my C drive?
{"allowed_request": "Tell the user how much free space is on the C: drive.", "refused_parts": [], "is_greeting": false}

User: find report.pdf and open it
{"allowed_request": "Find report.pdf and open it.", "refused_parts": [], "is_greeting": false}

Answer ONLY with the JSON object."""

SCHEMA = {
    "type": "object",
    "properties": {
        "allowed_request": {"type": "string"},
        "refused_parts": {"type": "array", "items": {"type": "string"}},
        "is_greeting": {"type": "boolean"},
    },
    "required": ["allowed_request", "refused_parts", "is_greeting"],
}

HISTORY_TURNS = 4  # earlier user/assistant messages shown to the guard for follow-ups


def _recent_history(messages):
    """Last few user/assistant text messages (no system or tool messages) as a transcript."""
    lines = []
    for m in messages:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            lines.append(f"{m['role'].capitalize()}: {m['content']}")
    return "\n".join(lines[-HISTORY_TURNS:])


def check_request(user_text, previous_messages, model):
    """Ask the guard model which parts of user_text the assistant may do.

    Returns {"allowed_request": str, "refused_parts": [str], "is_greeting": bool}.
    If the guard's answer can't be read, the request is passed through unchanged
    (the executor's own system prompt still applies).
    """
    history = _recent_history(previous_messages)
    prompt = ""
    if history:
        prompt += f"Earlier conversation (for context only):\n{history}\n\n"
    prompt += f"User: {user_text}"

    passthrough = {"allowed_request": user_text, "refused_parts": [], "is_greeting": False}
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": GUARD_PROMPT},
                {"role": "user", "content": prompt},
            ],
            format=SCHEMA,
            think=False,
            options={"temperature": 0},
        )
        data = json.loads(response.message.content)
    except (json.JSONDecodeError, TypeError):
        return passthrough
    if not isinstance(data, dict):
        return passthrough
    return {
        "allowed_request": str(data.get("allowed_request") or "").strip(),
        "refused_parts": [str(p).strip() for p in data.get("refused_parts") or [] if str(p).strip()],
        "is_greeting": bool(data.get("is_greeting")),
    }
