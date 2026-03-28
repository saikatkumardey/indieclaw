from __future__ import annotations

import asyncio

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Input, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

import indieclaw.tools as _tools_mod
from indieclaw.agent import get_current_model, reset_session
from indieclaw.agent import run as agent_run
from indieclaw.scheduler import setup_scheduler

_BG = "#1a1b26"
_PURPLE = "#bb9af7"
_GREEN = "#9ece6a"
_BLUE = "#3d59a1"
_AMBER = "#e0af68"
_RED = "#f7768e"
_DIM = "#565f89"

CHAT_ID = "tui"

_MASCOT = {
    "idle": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_GREEN}]● ●[/] ▌\n"
        " [bold]▀▄▽▄▀[/]"
    ),
    "thinking": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_AMBER}]◑ ◑[/] ▌\n"
        " [bold]▀▄~▄▀[/]"
    ),
    "happy": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_GREEN}]★ ★[/] ▌\n"
        " [bold]▀▄▽▄▀[/] ♪"
    ),
    "error": (
        " [bold]▄▀▀▀▄[/]\n"
        "▐ [bold {_RED}]; ;[/] ▌\n"
        " [bold]▀▄△▄▀[/]"
    ),
}


class Mascot(Static):

    DEFAULT_CSS = f"""
    Mascot {{
        background: {_BG};
        color: {_PURPLE};
        width: auto;
        height: 3;
        padding: 0 1;
    }}
    """

    def on_mount(self) -> None:
        self.set_mood("idle")

    def set_mood(self, mood: str) -> None:
        self.update(_MASCOT.get(mood, _MASCOT["idle"]))


class StatusBar(Horizontal):

    DEFAULT_CSS = f"""
    StatusBar {{
        background: {_BG};
        height: 3;
        dock: top;
        align-vertical: middle;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(
            f"  IndieClaw · {get_current_model()}  [bold {_GREEN}]● connected[/]",
            classes="status-text",
        )
        yield Mascot()


class MessageBubble(Static):

    DEFAULT_CSS = f"""
    MessageBubble {{
        margin: 0 1;
        padding: 0 1;
    }}
    MessageBubble.user {{
        background: {_BLUE};
        color: white;
        text-align: right;
        width: auto;
        max-width: 80%;
    }}
    MessageBubble.bot {{
        border: round {_GREEN};
        color: {_GREEN};
        max-width: 80%;
    }}
    MessageBubble.cron {{
        border: round {_AMBER};
        color: {_AMBER};
        text-align: center;
        max-width: 90%;
        offset-x: 5%;
    }}
    MessageBubble.error {{
        border: round {_RED};
        color: {_RED};
        max-width: 80%;
    }}
    MessageBubble.system {{
        color: {_DIM};
        text-align: center;
        text-style: italic;
    }}
    """

    def __init__(self, text: str, variant: str = "bot") -> None:
        super().__init__(text)
        self.add_class(variant)


class UserRow(Horizontal):

    DEFAULT_CSS = """
    UserRow {
        align-horizontal: right;
        height: auto;
        width: 100%;
    }
    """


class ChatView(ScrollableContainer):
    _inherit_bindings = False

    DEFAULT_CSS = f"""
    ChatView {{
        background: {_BG};
        height: 1fr;
        padding: 1 0;
    }}
    """


class IndieClawApp(App):

    CSS = f"""
    Screen {{
        background: {_BG};
        layers: base overlay;
    }}
    .status-text {{
        color: {_PURPLE};
        width: 1fr;
        height: 3;
        content-align-vertical: middle;
    }}
    LoadingIndicator {{
        height: 1;
        background: {_BG};
        color: {_GREEN};
    }}
    Input {{
        background: #24283b;
        border: round #414868;
        color: #c0caf5;
        dock: bottom;
        height: 3;
    }}
    Input:focus {{
        border: round {_GREEN};
    }}
    """

    BINDINGS = [
        Binding("ctrl+r", "reset_session", "Reset session"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("ctrl+z", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield StatusBar()
        yield ChatView(can_focus=False)
        yield LoadingIndicator()
        yield Input(placeholder="Message IndieClaw...")

    async def on_mount(self) -> None:
        self._drain_task = None
        self._scheduler = None
        self._original_send = _tools_mod._send_telegram
        self._cron_queue: asyncio.Queue[str] = asyncio.Queue()
        _loop = asyncio.get_running_loop()
        def _intercept_send(cid, msg):
            _loop.call_soon_threadsafe(self._cron_queue.put_nowait, msg)
        _tools_mod._send_telegram = _intercept_send
        self._scheduler = setup_scheduler()
        self._scheduler.start()
        self._drain_task = asyncio.create_task(self._drain_cron_queue())
        self.query_one(LoadingIndicator).display = False
        self.query_one(Input).focus()

    async def on_unmount(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        if self._drain_task is not None:
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        if self._original_send is not None:
            _tools_mod._send_telegram = self._original_send

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.clear()
        event.input.disabled = True
        self.query_one(LoadingIndicator).display = True
        self.query_one(Mascot).set_mood("thinking")
        await self._append_bubble(msg, "user")
        self._run_agent(msg)

    @work(exclusive=True)
    async def _run_agent(self, msg: str) -> None:
        result = await agent_run(chat_id=CHAT_ID, user_message=msg)
        await self._append_bubble(result, "bot")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_run_agent":
            return
        state = event.worker.state
        if state not in (WorkerState.SUCCESS, WorkerState.ERROR, WorkerState.CANCELLED):
            return
        mascot = self.query_one(Mascot)
        self.query_one(LoadingIndicator).display = False
        self.query_one(Input).disabled = False
        self.query_one(Input).focus()
        if state == WorkerState.ERROR:
            mascot.set_mood("error")
            err = str(event.worker.error)
            self.call_after_refresh(self._sync_append_bubble, f"Error: {err}", "error")
        elif state == WorkerState.CANCELLED:
            mascot.set_mood("error")
            self.call_after_refresh(self._sync_append_bubble, "Request cancelled.", "error")
        else:
            mascot.set_mood("happy")

    async def _drain_cron_queue(self) -> None:
        try:
            while True:
                msg = await self._cron_queue.get()
                await self._append_bubble(msg, "cron")
        except asyncio.CancelledError:
            pass

    async def action_reset_session(self) -> None:
        await self.query_one(ChatView).remove_children()
        await reset_session(CHAT_ID)
        await self._append_bubble("Session reset.", "system")

    async def _append_bubble(self, text: str, variant: str) -> None:
        chat_view = self.query_one(ChatView)
        widget = UserRow(MessageBubble(text, variant=variant)) if variant == "user" else MessageBubble(text, variant=variant)
        await chat_view.mount(widget)
        _maybe_scroll(chat_view)

    def _sync_append_bubble(self, text: str, variant: str) -> None:
        chat_view = self.query_one(ChatView)
        chat_view.mount(MessageBubble(text, variant=variant))
        _maybe_scroll(chat_view)


_SCROLL_THRESHOLD = 2


def _maybe_scroll(chat_view: ChatView) -> None:
    if chat_view.scroll_y >= chat_view.max_scroll_y - _SCROLL_THRESHOLD:
        chat_view.scroll_end(animate=False)
