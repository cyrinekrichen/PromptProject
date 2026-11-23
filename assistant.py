import ollama
from tools import TOOLS, AVAILABLE

MODEL = "qwen3.5:4b"

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
Apart from greetings, you ONLY handle file management and device commands. For anything else (recipes, general knowledge, advice, coding help, math), do NOT answer. Reply exactly:
"Sorry, I can only help with file management and device commands."

HOW YOU WORK:
You have one tool, run_command, which runs a PowerShell command. Use it for every in-scope task. Write one correct command, run it, then report the result.
Never answer from memory: always run a command to check the current state.
Only report what the command output shows. Never invent names or paths.
If a command fails, read the error, fix the command, and try once more. If it still fails, tell the user.
If the user cancels a command, do not retry it.

POWERSHELL EXAMPLES:
- list a folder: Get-ChildItem ~/Desktop
- find a file: Get-ChildItem ~ -Recurse -Filter "*report*" -ErrorAction SilentlyContinue | Select-Object -First 20 FullName
- create a folder: New-Item -ItemType Directory -Path ~/Desktop/cyrine
- create a file: New-Item -ItemType File -Path ~/Desktop/cyrine/imen.txt
- open a file or folder: Start-Process "C:/Users/LENOVO/Desktop/file.pdf"
- launch an app: Start-Process chrome"""

def run_tool(call, confirm=None):
    name = call.function.name
    args = dict(call.function.arguments)
    args.pop("confirm_fn", None)  # never let the model choose the confirmation function
    fn = AVAILABLE.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'."
    if confirm is not None:
        # Per-conversation confirmation (WhatsApp). The terminal app leaves it None
        # and uses the global callback in tools.py.
        args["confirm_fn"] = confirm
    try:
        return fn(**args)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"


def print_event(kind, data):
    """Default event handler for the plain terminal version."""
    if kind == "tool_call":
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
    """
    for _ in range(5):  # safety cap on tool calls per request
        response = ollama.chat(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            think=False,
            options={"temperature": 0.2},
        )
        msg = response.message
        # Store plain dicts so the same shape can be saved to and reloaded from the DB.
        tool_calls = [
            {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
            for c in (msg.tool_calls or [])
        ] or None
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        on_event("assistant", {"content": msg.content or "", "tool_calls": tool_calls})
        if not msg.tool_calls:
            return msg.content
        for call in msg.tool_calls:
            on_event("tool_call", {"name": call.function.name,
                                   "arguments": dict(call.function.arguments)})
            result = run_tool(call, confirm)
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