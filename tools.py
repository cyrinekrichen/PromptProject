import re
import subprocess

BLOCKED = re.compile(
    r"\b(format-volume|format\s+[a-z]:|shutdown|restart-computer|stop-computer|"
    r"diskpart|bcdedit|reg\s+(add|delete)|set-itemproperty\s+hklm|"
    r"remove-item\s+.*-recurse.*[a-z]:\\\s*$|net\s+user|cipher\s+/w)\b",
    re.IGNORECASE,
)
RISKY = re.compile(
    r"\b(remove-item|del|erase|rd|rmdir|rm|move-item|mv|move|rename-item|ren|"
    r"set-content|out-file|clear-content|stop-process|taskkill|kill)\b|>",
    re.IGNORECASE,
)
MAX_OUTPUT = 3000


def _console_confirm(command: str) -> bool:
    """Default confirmation for the plain terminal version."""
    answer = input(f"  [confirm] Run this command? {command}\n  (y/n) > ").strip().lower()
    return answer == "y"


# Function used to confirm risky commands: takes the command, returns True/False.
# The Textual app replaces it with a modal dialog via set_confirm_callback().
confirm = _console_confirm


def set_confirm_callback(fn):
    global confirm
    confirm = fn


def run_command(command: str, confirm_fn=None) -> str:
    """Run a PowerShell command and return its output.

    confirm_fn: optional per-call confirmation function (used by the WhatsApp bot,
    where each conversation has its own). Defaults to the global `confirm`.
    """
    if BLOCKED.search(command):
        return "Error: this command is blocked for safety."
    if RISKY.search(command):
        if not (confirm_fn or confirm)(command):
            return "Command cancelled by the user."
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds."
    except OSError as e:
        return f"Error: could not run command: {e}"
    output = (result.stdout + result.stderr).strip()
    if not output:
        return "Command ran with no output." if result.returncode == 0 else f"Command failed (exit code {result.returncode})."
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n...(output truncated)"
    return output


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a Windows PowerShell command on the user's computer and "
                "return its output. Use it for ALL file management and device "
                "tasks: list, find, open, create, copy, move or delete files "
                "and folders, and launch applications."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "A single PowerShell command, e.g. 'Get-ChildItem ~/Desktop'.",
                    }
                },
                "required": ["command"],
            },
        },
    }
]

AVAILABLE = {"run_command": run_command}