import argparse
import asyncio
import importlib
import importlib.util
import os
import signal
import sys
from types import ModuleType
from typing import Optional, Any

from loguru import logger

# Route Textual internal logs to a separate file to avoid duplicates
os.environ.setdefault("TEXTUAL_LOG", "tui_textual.log")
os.environ.setdefault("TEXTUAL", "debug")

from textual.app import ComposeResult
from textual.timer import Timer
from textual.widgets import Header, Footer, Static
from textual.containers import Vertical, Horizontal

from tui.core.base_app import BotTUIBase
from tui.widgets.text_list_panel import TextListPanel
from tui.widgets.input_bar import InputBar
from tui.widgets.rtvi_list_panel import RTVIListPanel
from tui.widgets.syslog_panel import SyslogPanel


def import_bot_module(path_or_module: str) -> ModuleType:
    if os.path.exists(path_or_module) and path_or_module.endswith(".py"):
        spec = importlib.util.spec_from_file_location("bot_module", path_or_module)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to import bot file: {path_or_module}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bot_module"] = mod
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        return mod
    return importlib.import_module(path_or_module)


class SimpleMessagesApp(BotTUIBase):
    """Minimal demo: status + messages + input; overlays provided here.

    - Ctrl+L toggles syslog overlay; Ctrl+N toggles RTVI two-panel overlay.
    - Ctrl+Q quits; Ctrl+Y copies selection or current item.
    """

    def __init__(self, bot_module: ModuleType):
        super().__init__(bot_module)
        self.messages: Optional[TextListPanel] = None
        self.input_bar: Optional[InputBar] = None
        self._heartbeat_timer: Optional[Timer] = None
        # Signal to base that we provide our own titles for RTVI panes
        self._rtvi_titles = True
        # One-time placeholders for empty RTVI lists
        self._inbox_placeholder = None
        self._outbox_placeholder = None
        # One-time placeholder for main messages list
        self._messages_placeholder = None
        self._last_messages_append_type: Optional[str] = None

    def compose(self) -> ComposeResult:  # type: ignore[override]
        # Build main layout and include overlays expected by base.
        yield Header(show_clock=True)
        with Vertical():
            self.status = Static("Status: initializing", id="status")
            yield self.status

            # Main view
            self.messages = TextListPanel(id="messages", classes="log")
            yield self.messages
            self.input_bar = InputBar(self._on_input_submit, id="input")
            yield self.input_bar

            # Overlays used by base handlers/actions (with titles like tui.py)
            with Horizontal(id="rtvi_panes"):
                with Vertical():
                    yield Static("Inbound RTVI:")
                    self.rtvi_inbox = RTVIListPanel(id="inbox", classes="log")
                    yield self.rtvi_inbox
                with Vertical():
                    yield Static("Outbound RTVI:")
                    self.rtvi_outbox = RTVIListPanel(id="outbox", classes="log")
                    yield self.rtvi_outbox
            self.syslog = SyslogPanel(id="syslog", classes="log")
            self.syslog.display = False
            yield self.syslog
        yield Footer()

    async def on_mount(self) -> None:  # type: ignore[override]
        await super().on_mount()
        try:
            if self.input_bar:
                self.set_focus(self.input_bar)
        except Exception:
            pass
        try:
            self.syslog and self.syslog.write_line("[info] SimpleMessagesApp mounted")
        except Exception:
            pass
        # Add a one-time placeholder row to each RTVI list to force initial layout
        try:
            from textual.widgets import ListItem as _LI

            if self.rtvi_inbox is not None and self._inbox_placeholder is None:
                self._inbox_placeholder = _LI(Static("..."), classes="-placeholder")
                await self.rtvi_inbox.append(self._inbox_placeholder)
            if self.rtvi_outbox is not None and self._outbox_placeholder is None:
                self._outbox_placeholder = _LI(Static("..."), classes="-placeholder")
                await self.rtvi_outbox.append(self._outbox_placeholder)
            if self.messages is not None and self._messages_placeholder is None:
                self._messages_placeholder = _LI(Static("..."), classes="-placeholder")
                await self.messages.append(self._messages_placeholder)
        except Exception:
            pass
        # No diagnostics heartbeat in normal runs

    async def on_unmount(self) -> None:  # type: ignore[override]
        logger.info("on_unmount(): UI is unmounting")

    async def on_ready(self) -> None:  # type: ignore[override]
        logger.info("on_ready(): UI is ready")
        try:
            self.syslog and self.syslog.write_line("[info] UI ready")
        except Exception:
            pass

    async def _on_status(self, connected: bool) -> None:
        await super()._on_status(connected)
        if self.input_bar:
            self.input_bar.disabled = not connected
            self.input_bar.placeholder = (
                "Type RTVI JSON or text and press Enter"
                if connected
                else "Waiting for connection..."
            )

    async def _on_inbound(self, payload: Any) -> None:
        # Hide placeholder on first inbound
        try:
            if self._inbox_placeholder is not None:
                self._inbox_placeholder.display = False
                self._inbox_placeholder = None
        except Exception:
            pass
        await super()._on_inbound(payload)
        await self._maybe_append_message(payload)

    async def _on_outbound(self, payload: Any) -> None:
        # Hide placeholder on first outbound
        try:
            if self._outbox_placeholder is not None:
                self._outbox_placeholder.display = False
                self._outbox_placeholder = None
        except Exception:
            pass
        await super()._on_outbound(payload)
        await self._maybe_append_message(payload)

    async def _maybe_append_message(self, payload: Any) -> None:
        """Append readable text for common RTVI payloads.

        - Prefer bot-transcription / bot-llm-text types with data.text
        - Fallback: any dict with a top-level 'text' or data.text
        - Ignore non-text payloads
        """
        try:
            if not isinstance(payload, dict):
                return
            # Hide messages placeholder on first real item
            try:
                if self._messages_placeholder is not None:
                    self._messages_placeholder.display = False
                    self._messages_placeholder = None
            except Exception:
                pass

            t = payload.get("type")
            text = payload.get("data", {}).get("text")
            final = payload.get("data", {}).get("final")
            if t == "user-transcription" and final:
                if self._last_messages_append_type == "user-transcription":
                    await self.messages.append_text_to_last_item(" " + text)
                else:
                    await self.messages.append_text_item("User: " + text)
                self._last_messages_append_type = "user-transcription"

            elif t == "bot-transcription":
                if self._last_messages_append_type == "bot-transcription":
                    await self.messages.append_text_to_last_item(text)
                else:
                    await self.messages.append_text_item("Bot: " + text)
                self._last_messages_append_type = "bot-transcription"
            return
        except Exception as e:
            logger.exception("Failed to append message: ", e)
            pass

    async def _on_input_submit(self, payload: Any) -> None:
        try:
            await self.transport_mgr.send_app_message(payload)
        except Exception as e:
            self.syslog and self.syslog.write_line(f"[error] Failed to send app message: {e}")

    # No heartbeat: cleaned up


def main(argv: Optional[list[str]] = None) -> int:
    # Configure loguru sinks
    try:
        logger.remove()
    except Exception:
        pass
    logger.add(sys.stderr, level="INFO")
    logger.add(
        "tui_demo.app.log",
        level="DEBUG",
        backtrace=True,
        diagnose=True,
        rotation="1 MB",
        retention=5,
    )

    parser = argparse.ArgumentParser(description="Simple messages TUI app")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--bot", help="Python module path, e.g. my_pkg.my_bot")
    g.add_argument("--file", help="Path to a bot Python file, e.g. ./bot.py")
    parser.add_argument(
        "--inline", action="store_true", help="Run Textual in inline mode for debugging"
    )
    args = parser.parse_args(argv)

    mod = import_bot_module(args.bot or args.file)
    app = SimpleMessagesApp(mod)

    # Ensure Ctrl-C exits the app cleanly
    def _sigint_handler(signum, frame):  # noqa: ARG001
        try:
            app.call_from_thread(lambda: asyncio.create_task(app.action_quit()))
        except Exception:
            pass

    signal.signal(signal.SIGINT, _sigint_handler)
    try:
        logger.info("Starting Textual app.run()")
        if args.inline:
            logger.warning("Running in inline mode (debug)")
            app.run(inline=True, inline_no_clear=True)
        else:
            app.run()
        logger.info("Textual app.run() returned")
        return 0
    except KeyboardInterrupt:
        try:
            asyncio.run(app.action_quit())
        except Exception:
            pass
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
