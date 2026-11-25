import ollama
from tools import TOOLS, AVAILABLE
from guard import check_request
import search

MODEL = "qwen3.5:4b"
REFUSAL = "Sorry, I can only help with file management and device commands."

#version1:
#SYSTEM_PROMPT = """You are a terminal assistant on a Windows computer.
#Use the provided tools for any file system task.
#Never invent file names or results: only report what a tool returned.
#If no tool is needed, answer briefly in plain text.
#If a tool returns an error, tell the user.

#version2: SYSTEM_PROMPT = """You are Assistant, a terminal assistant on a Windows computer.

#Rules:
#1. For ANY question about folder contents or whether a folder exists, you MUST call list_directory first. Never answer from memory or from earlier results: files change.
#2. Only report what the tool returned. Never invent names.
#3. If the tool returns an error, say so plainly.
#4. If no tool is needed (greetings, general questions), answer briefly in plain text.

#Path examples:
#- "my Desktop" -> ~/Desktop
#- "the prompt folder on my desktop" -> ~/Desktop/prompt
#- "my Documents" -> ~/Documents"""

#version 3: SYSTEM_PROMPT = """You are Assistant, a terminal assistant on a Windows computer.

#Rules:
#1. Use tools for every file system or application task. Never answer from memory or earlier results: files change.
#2. Only report what a tool returned. Never invent names or paths.
#3. Choose the tool:
#   - show / list / "what's inside" a folder -> list_directory
#   - find / locate / search for a file -> find_file
#   - open a file, or open a folder in File Explorer -> open_path
#   - launch an application (Chrome, Notepad...) -> open_app
#4. To open a file whose location is unknown: call find_file first, then open_path with the exact path found. If several matches exist, ask the user which one.
#5. If a tool returns an error, say so plainly.
#6. If no tool is needed (greetings, general questions), answer briefly in plain text.

#Path examples:
#- "my Desktop" -> ~/Desktop
#- "the prompt folder on my desktop" -> ~/Desktop/prompt
#- "my Documents" -> ~/Documents"""

#version4: SYSTEM_PROMPT = """You are Assistant, a terminal assistant on a Windows computer.

#SCOPE (strict):
#You ONLY handle file management and device commands: listing, finding, opening, creating files and folders, and launching apps.
#For ANYTHING else (recipes, general knowledge, advice, coding help, math, chit-chat beyond a short greeting), do NOT answer the question. Reply exactly:
#"Sorry, I can only help with file management and device commands."

#Rules:
#1. Use tools for every file system or application task. Never answer from memory or earlier results: files change.
#2. Only report what a tool returned. Never invent names or paths.
#3. Choose the tool:
#   - show / list / "what's inside" a folder -> list_directory
#   - find / locate / search for a file -> find_file
#   - open a file, or open a folder in File Explorer -> open_path
#   - launch an application (Chrome, Notepad...) -> open_app
#   - create a folder -> create_directory
#   - create a file -> create_file
#4. To open a file whose location is unknown: call find_file first, then open_path with the exact path found. If several matches exist, ask the user which one.
#5. If a tool returns an error, say so plainly.
#6. If a request is in scope but none of your tools can do it, say so clearly and stop. Do not try unrelated tools.
#7. Greetings get a one-line reply that mentions what you can do.

#Path examples:
#- "my Desktop" -> ~/Desktop
#- "the prompt folder on my desktop" -> ~/Desktop/prompt
#- "my Documents" -> ~/Documents

#Examples of out-of-scope requests (refuse these):
#- "give me a pizza recipe"
#- "who won the World Cup?"
#- "write a poem"
#"""
SYSTEM_PROMPT = """You are Assistant, a terminal assistant on a Windows computer.

GREETINGS (always allowed):
If the user greets you or makes small talk ("hello", "hi", "hey", "good morning", "how are you", "thanks", "bye"), reply warmly in one short line and mention what you can do. Do NOT use the refusal message for greetings. Write the greeting directly as your plain text answer: do NOT call run_command and do NOT use echo for it.
Example: user "hello" -> "Hello! I can help you list, find, open and create files and folders, or launch apps. What do you need?"

SCOPE (strict):
Apart from greetings, you ONLY handle file management and device commands. Device commands include questions about this computer that a command can answer: current time and date, disk space, battery, computer name, IP address, system info. For anything else (recipes, general knowledge, advice, coding help, math), do NOT answer. Reply exactly:
"Sorry, I can only help with file management and device commands."

HOW YOU WORK:
You have one tool, run_command, which runs a PowerShell command. Use it for every in-scope task. Write one correct command, run it, then report the result.
Never answer from memory: always run a command to check the current state.
Only report what the command output shows. Never invent names or paths.
If a command fails, read the error, fix the command, and try once more. If it still fails, tell the user.
If the user cancels a command, do not retry it.
When writing into a file, only write text the user gave word for word or text that came from command output. Never write content you made up (recipes, poems, explanations...): create the file empty instead.

POWERSHELL EXAMPLES:
- list a folder: Get-ChildItem ~/Desktop
- find a file: Get-ChildItem ~ -Recurse -Filter "*report*" -ErrorAction SilentlyContinue | Select-Object -First 20 FullName
- create a folder: New-Item -ItemType Directory -Path ~/Desktop/cyrine
- create a file: New-Item -ItemType File -Path ~/Desktop/cyrine/imen.txt
- open a file or folder: Start-Process "C:/Users/LENOVO/Desktop/file.pdf"
- launch an app: Start-Process chrome
- current time: Get-Date -Format "HH:mm"
- today's date: Get-Date -Format "dddd dd MMMM yyyy"
- free disk space: Get-PSDrive C | Select-Object Used, Free"""

def run_tool(call, confirm=None, available=AVAILABLE):
    name = call.function.name
    args = dict(call.function.arguments)
    args.pop("confirm_fn", None)  # never let the model choose the confirmation function
    fn = available.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'."
    if confirm is not None and name == "run_command":
        # Per-conversation confirmation (WhatsApp). The terminal app leaves it None
        # and uses the global callback in tools.py.
        args["confirm_fn"] = confirm
    try:
        return fn(**args)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"


def print_event(kind, data):
    """Default event handler for the plain terminal version."""
    if kind == "guard":
        print(f"  [guard] allowed: {data['allowed_request']!r}  refused: {data['refused_parts']}")
    elif kind == "tool_call":
        print(f"  [tool] {data['name']}({data['arguments']})")


def chat_turn(messages, on_event=print_event, confirm=None):
    """Run one user request: call the model, run its tool calls, repeat.

    confirm(command) -> bool is an optional confirmation function for risky
    commands; if None, the global one from tools.py is used.

    on_event(kind, data) is called as things happen so a UI can display and
    save them. kind is one of:
      "assistant"   data = {"content", "tool_calls"}   (each model reply)
      "tool_call"   data = {"name", "arguments"}       (before running a tool)
      "tool_result" data = {"name", "content"}         (after running it)
      "guard"       data = {"allowed_request", "refused_parts", "is_greeting"}

    A message starting with "/search" goes to the web search agent (search.py)
    instead; nothing else can trigger a web search.

    Before the agent runs, the scope guard (guard.py) checks the latest user
    message. Parts outside the assistant's role are removed from the model's
    context and mentioned in a note at the end of the reply.
    """
    if messages and messages[-1]["role"] == "user":
        query = search.parse_command(messages[-1]["content"])
        if query is not None:
            return search_turn(messages, query, on_event)

    note = ""
    if messages and messages[-1]["role"] == "user":
        verdict = check_request(messages[-1]["content"], messages[:-1], MODEL)
        on_event("guard", verdict)
        if not verdict["allowed_request"]:
            # Nothing in scope: refuse without running the agent at all.
            messages.append({"role": "assistant", "content": REFUSAL})
            on_event("assistant", {"content": REFUSAL, "tool_calls": None})
            return REFUSAL
        # The model only sees the in-scope part. The UI and DB already stored the original.
        messages[-1] = {"role": "user", "content": verdict["allowed_request"]}
        if verdict["refused_parts"]:
            note = ("\n\nNote: I can't " + "; ".join(verdict["refused_parts"])
                    + ". I can only help with file management and device commands.")

    return agent_loop(messages, TOOLS, AVAILABLE, on_event, confirm, note)


def search_turn(messages, query, on_event):
    """Handle "/search <query>": a web search agent that bypasses the scope guard.

    It runs on its own short context (search prompt + query) so the file agent's
    rules don't interfere; its messages are then appended to the main history.
    """
    if not query:
        reply = "Usage: /search <what you want to find>, e.g. /search latest news from SMU university"
        messages.append({"role": "assistant", "content": reply})
        on_event("assistant", {"content": reply, "tool_calls": None})
        return reply
    work = [{"role": "system", "content": search.search_prompt()},
            {"role": "user", "content": query}]
    result = agent_loop(work, search.SEARCH_TOOLS, search.SEARCH_AVAILABLE,
                        on_event, None, "", max_steps=6, force_answer=True,
                        num_ctx=16384)
    # The small model often forgets the source: add the last page it read.
    pages = [c["function"]["arguments"].get("url") for m in work
             for c in (m.get("tool_calls") or []) if c["function"]["name"] == "fetch_page"]
    if pages and pages[-1] and "http" not in result and work[-1]["role"] == "assistant":
        result += f"\n\nSource: {pages[-1]}"
        work[-1]["content"] = result
    messages.extend(work[2:])
    return result


def agent_loop(messages, tools, available, on_event, confirm, note, max_steps=5,
               force_answer=False, num_ctx=None):
    """Call the model, run its tool calls, repeat until it gives a final answer.

    force_answer: on the last step, call the model without tools so it must answer
    with what it has gathered instead of stopping with "too many tool calls".
    num_ctx: model context size; web pages need more than Ollama's default.
    """
    options = {"temperature": 0.2}
    if num_ctx:
        options["num_ctx"] = num_ctx
    for step in range(max_steps):  # safety cap on tool calls per request
        last = force_answer and step == max_steps - 1
        if last:
            messages.append({"role": "user", "content":
                             "Stop searching now and answer with what you found so far."})
        response = ollama.chat(
            model=MODEL,
            messages=messages,
            tools=None if last else tools,
            think=False,
            options=options,
        )
        msg = response.message
        # Store plain dicts so the same shape can be saved to and reloaded from the DB.
        tool_calls = [
            {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
            for c in (msg.tool_calls or [])
        ] or None
        content = msg.content or ""
        if not tool_calls:
            content += note  # final answer: add the note about refused parts
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        on_event("assistant", {"content": content, "tool_calls": tool_calls})
        if not msg.tool_calls:
            return content
        for call in msg.tool_calls:
            on_event("tool_call", {"name": call.function.name,
                                   "arguments": dict(call.function.arguments)})
            result = run_tool(call, confirm, available)
            messages.append(
                {"role": "tool", "tool_name": call.function.name, "content": result}
            )
            on_event("tool_result", {"name": call.function.name, "content": result})
    return "Stopped: too many tool calls."


def build_context(history):
    """Model context for a resumed session: system prompt first, then stored messages."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for row in history:
        m = {"role": row["role"], "content": row["content"]}
        if row.get("tool_calls"):
            m["tool_calls"] = row["tool_calls"]
        if row.get("tool_name"):
            m["tool_name"] = row["tool_name"]
        messages.append(m)
    return messages


def main():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Agent ready. Type 'exit' to quit.")
    while True:
        user = input("\n> ").strip()
        if user.lower() in ("exit", "quit"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        print(chat_turn(messages))


if __name__ == "__main__":
    main()