"""Textual UI for Assistant. Run with: python app.py"""

import threading

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static
from textual.worker import get_current_worker

import assistant
import db
import tools

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Label shown before each kind of chat message (colors are in the CSS below).
PREFIX = {
    "user": "You",
    "assistant": "Assistant",
    "tool-call": "Tool call",
    "tool-result": "Tool result",
    "error": "Error",
}


EXIT_COMMANDS = {"/exit", "//exit", "/bye", "/quit"}


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No dialog (risky commands, deleting a session)."""

    BINDINGS = [("y", "answer(True)", "Yes"), ("n", "answer(False)", "No"),
                ("escape", "answer(False)", "No")]

    def __init__(self, command, title="Run this command?"):
        super().__init__()
        self.command = command
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text, id="dialog-title")
            yield Static(Text(self.command), id="dialog-command")
            with Horizontal(id="dialog-buttons"):
                yield Button("Yes (y)", variant="error", id="yes")
                yield Button("No (n)", variant="primary", id="no")

    def on_mount(self):
        self.query_one("#no", Button).focus()  # safe default

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool):
        self.dismiss(value)


class RenameScreen(ModalScreen):
    """Dialog asking for a new session title. Returns the title, or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current_title):
        super().__init__()
        self.current_title = current_title

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Label("Rename session", id="dialog-title")
            yield Input(value=self.current_title, id="rename-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Save", variant="success", id="save")
                yield Button("Cancel", variant="primary", id="cancel")

    def on_mount(self):
        self.query_one("#rename-input", Input).focus()

    def _save(self):
        title = self.query_one("#rename-input", Input).value.strip()
        self.dismiss(title or None)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self._save()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class SessionButton(Button):
    """Tiny icon button inside a session row (rename / delete)."""

    def __init__(self, icon, action, session_id, title):
        super().__init__(icon, classes=f"row-btn {action}", tooltip=action.capitalize())
        self.action_name = action
        self.session_id = session_id
        self.session_title = title


class AssistantApp(App):
    TITLE = "Assistant"

    CSS = """
    #sidebar { width: 32; border-right: solid $primary; padding: 0 1; }
    #new { width: 100%; margin: 1 0; }
    #sessions { height: 1fr; }
    #chat { width: 1fr; }
    #log { height: 1fr; padding: 0 1; }
    #status { height: 1; padding: 0 1; color: $warning; }
    #prompt { margin: 0 1; }

    .msg { margin: 1 0 0 0; }
    .user { color: #5fafff; }
    .assistant { color: #87d787; }
    .tool-call { color: #d7af5f; }
    .tool-result { color: #9e9e9e; }
    .error { color: #ff5f5f; }

    ConfirmScreen { align: center middle; }
    #dialog { width: 70; height: auto; padding: 1 2; border: thick $error; background: $surface; }
    #dialog-title { text-style: bold; }
    #dialog-command { margin: 1 0; color: #d7af5f; }
    #dialog-buttons { height: auto; align: center middle; }
    #dialog-buttons Button { margin: 0 2; }

    RenameScreen { align: center middle; }
    #rename-dialog { width: 60; height: auto; padding: 1 2; border: thick $primary; background: $surface; }
    #rename-input { margin: 1 0; }

    .session-row { height: auto; }
    .session-label { width: 1fr; }
    .row-btn { min-width: 3; width: 3; height: 1; border: none; padding: 0; margin: 0 0 0 1; }
    .row-btn.rename { background: $primary-darken-2; }
    .row-btn.delete { background: $error-darken-2; }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New session", priority=True),
        Binding("f2", "rename_session", "Rename session"),
        Binding("delete", "delete_session", "Delete session"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.session_id = None          # created in the DB on the first message
        self.messages = assistant.build_context([])  # model context
        self.busy = False
        self.status_text = ""
        self.frame = 0

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Button("+ New session", id="new", variant="success")
                yield ListView(id="sessions")
            with Vertical(id="chat"):
                yield VerticalScroll(id="log")
                yield Static("", id="status")
                yield Input(placeholder="Ask Assistant... (Enter to send)", id="prompt")
        yield Footer()

    async def on_mount(self):
        tools.set_confirm_callback(self.confirm_from_worker)
        self.set_interval(0.1, self.animate_status)
        await self.refresh_sessions()
        self.show_welcome()
        self.query_one("#prompt", Input).focus()

    # ---------- helpers ----------

    def add_message(self, kind, text):
        """Append one colored message to the chat view and scroll to it."""
        body = Text()
        body.append(f"{PREFIX[kind]}: ", style="bold")
        body.append(text)
        log = self.query_one("#log", VerticalScroll)
        log.mount(Static(body, classes=f"msg {kind}"))
        log.scroll_end(animate=False)

    def show_welcome(self):
        self.add_message("assistant", "New session. I can help with file management and device commands. "
                         "Type /search <question> to search the web.")

    def show_history(self, rows):
        """Re-display stored messages when resuming a session."""
        for row in rows:
            if row["role"] == "user":
                self.add_message("user", row["content"])
            elif row["role"] == "assistant":
                if row["content"]:
                    self.add_message("assistant", row["content"])
                for call in row["tool_calls"] or []:
                    fn = call["function"]
                    self.add_message("tool-call", f"{fn['name']}({fn['arguments']})")
            elif row["role"] == "tool":
                self.add_message("tool-result", row["content"])

    async def refresh_sessions(self):
        lv = self.query_one("#sessions", ListView)
        await lv.clear()
        for s in db.list_sessions():
            label = Text(s["title"])
            label.append("\n" + s["updated_at"].replace("T", " ")[:16], style="dim")
            row = Horizontal(
                Label(label, classes="session-label"),
                SessionButton("✎", "rename", s["id"], s["title"]),
                SessionButton("✕", "delete", s["id"], s["title"]),
                classes="session-row",
            )
            item = ListItem(row)
            item.session_id = s["id"]
            item.session_title = s["title"]
            await lv.append(item)

    def set_busy(self, busy, text=""):
        self.busy = busy
        self.status_text = text
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = busy
        if not busy:
            self.query_one("#status", Static).update("")
            prompt.focus()

    def animate_status(self):
        """Timer callback: spin the indicator while the model is working."""
        if self.busy:
            self.frame = (self.frame + 1) % len(SPINNER)
            self.query_one("#status", Static).update(
                Text(f"{SPINNER[self.frame]} {self.status_text}"))

    # ---------- sessions ----------

    async def action_new_session(self):
        if self.busy:
            return
        self.session_id = None
        self.messages = assistant.build_context([])
        await self.query_one("#log", VerticalScroll).remove_children()
        self.show_welcome()
        self.query_one("#prompt", Input).focus()

    @on(Button.Pressed, "#new")
    async def new_pressed(self):
        await self.action_new_session()

    def _highlighted_session(self):
        item = self.query_one("#sessions", ListView).highlighted_child
        if item is None:
            return None, None
        return item.session_id, item.session_title

    def action_rename_session(self):
        session_id, title = self._highlighted_session()
        if session_id is not None:
            self.rename_session(session_id, title)

    def action_delete_session(self):
        session_id, title = self._highlighted_session()
        if session_id is not None:
            self.delete_session(session_id, title)

    @on(Button.Pressed, ".row-btn")
    def row_button_pressed(self, event: Button.Pressed):
        event.stop()
        btn = event.button
        if btn.action_name == "rename":
            self.rename_session(btn.session_id, btn.session_title)
        else:
            self.delete_session(btn.session_id, btn.session_title)

    def rename_session(self, session_id, title):
        async def done(new_title):
            if new_title and new_title != title:
                db.rename_session(session_id, new_title)
                await self.refresh_sessions()

        self.push_screen(RenameScreen(title), done)

    def delete_session(self, session_id, title):
        if self.busy and session_id == self.session_id:
            self.notify("Wait for the current answer to finish.", severity="warning")
            return

        async def done(ok):
            if not ok:
                return
            db.delete_session(session_id)
            if session_id == self.session_id:
                await self.action_new_session()
            await self.refresh_sessions()
            self.notify(f"Deleted “{title}”.")

        self.push_screen(ConfirmScreen(title, title="Delete this session?"), done)

    @on(ListView.Selected, "#sessions")
    async def session_selected(self, event: ListView.Selected):
        if self.busy:
            self.notify("Wait for the current answer to finish.", severity="warning")
            return
        self.session_id = event.item.session_id
        rows = db.load_messages(self.session_id)
        self.messages = assistant.build_context(rows)
        await self.query_one("#log", VerticalScroll).remove_children()
        self.show_history(rows)
        self.query_one("#prompt", Input).focus()

    # ---------- chatting ----------

    @on(Input.Submitted, "#prompt")
    async def send(self, event: Input.Submitted):
        text = event.value.strip()
        if not text or self.busy:
            return
        if text.lower() in EXIT_COMMANDS:
            self.exit()
            return
        event.input.value = ""
        if self.session_id is None:
            self.session_id = db.create_session(db.make_title(text))
        db.add_message(self.session_id, "user", text)
        self.messages.append({"role": "user", "content": text})
        self.add_message("user", text)
        self.set_busy(True, "Assistant is thinking...")
        await self.refresh_sessions()
        self.run_agent(self.session_id)

    @work(thread=True, exclusive=True)
    def run_agent(self, session_id):
        """Runs in a background thread so the UI never freezes."""

        def on_event(kind, data):
            # Save first, then update the UI from the main thread.
            if kind == "assistant":
                db.add_message(session_id, "assistant", data["content"],
                               tool_calls=data["tool_calls"])
                if data["content"]:
                    self.call_from_thread(self.add_message, "assistant", data["content"])
            elif kind == "tool_call":
                args = data["arguments"]
                self.call_from_thread(self.add_message, "tool-call",
                                      f"{data['name']}({args})")
                self.status_text = f"Running: {args.get('command', args)}"
            elif kind == "tool_result":
                db.add_message(session_id, "tool", data["content"], tool_name=data["name"])
                self.call_from_thread(self.add_message, "tool-result", data["content"])
                self.status_text = "Assistant is thinking..."

        try:
            result = assistant.chat_turn(self.messages, on_event)
            if result == "Stopped: too many tool calls.":
                self.call_from_thread(self.add_message, "error", result)
        except Exception as e:  # e.g. Ollama not running
            self.call_from_thread(self.add_message, "error", f"{type(e).__name__}: {e}")
        self.call_from_thread(self.set_busy, False)
        self.call_from_thread(self.refresh_sessions)

    def confirm_from_worker(self, command):
        """Confirmation callback for tools.run_command (called on the worker thread).

        Shows the modal on the UI thread and blocks this worker until the user answers.
        """
        answered = threading.Event()
        result = {"ok": False}

        def done(ok):
            result["ok"] = bool(ok)
            answered.set()

        self.call_from_thread(self.push_screen, ConfirmScreen(command), done)
        worker = get_current_worker()
        while not answered.wait(0.2):
            if worker.is_cancelled:  # app is quitting
                return False
        return result["ok"]


if __name__ == "__main__":
    AssistantApp().run()
