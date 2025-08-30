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

            # Overlays used by base handlers/actions
            with Horizontal(id="rtvi_panes"):
                self.rtvi_inbox = RTVIListPanel(id="inbox", classes="log")
                self.rtvi_outbox = RTVIListPanel(id="outbox", classes="log")
                yield self.rtvi_inbox
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
        try:
            self._heartbeat_timer = self.set_interval(2.0, self._heartbeat)
        except Exception:
            pass

    async def on_unmount(self) -> None:  # type: ignore[override]
        try:
            if self._heartbeat_timer is not None:
                self._heartbeat_timer.stop()
                self._heartbeat_timer = None
        except Exception:
            pass
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

    async def _on_inbound(self, payload: Any) -> None:
        await super()._on_inbound(payload)
        await self._maybe_append_message(payload)

    async def _on_outbound(self, payload: Any) -> None:
        await super()._on_outbound(payload)
        await self._maybe_append_message(payload)

    async def _maybe_append_message(self, payload: Any) -> None:
        try:
            t = payload.get("type")
            if t not in ("bot-transcription", "bot-llm-text"):
                return
            text = payload.get("data", {}).get("text")
            if not isinstance(text, str):
                return
            prefix = "ASR" if t == "bot-transcription" else "LLM"
            if self.messages:
                await self.messages.append_text(f"[{prefix}] {text}")
        except Exception:
            pass

    async def _on_input_submit(self, payload: Any) -> None:
        try:
            await self.transport_mgr.send_app_message(payload)
        except Exception as e:
            self.syslog and self.syslog.write_line(f"[error] Failed to send app message: {e}")

    def _heartbeat(self) -> None:
        try:
            self.syslog and self.syslog.write_line("[debug] heartbeat")
        except Exception:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    # Configure loguru sinks
    try:
        logger.remove()
    except Exception:
        pass
    logger.add(sys.stderr, level="INFO")
    logger.add("tui_demo.app.log", level="DEBUG", backtrace=True, diagnose=True, rotation="1 MB", retention=5)

    parser = argparse.ArgumentParser(description="Simple messages TUI app")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--bot", help="Python module path, e.g. my_pkg.my_bot")
    g.add_argument("--file", help="Path to a bot Python file, e.g. ./bot.py")
    parser.add_argument("--inline", action="store_true", help="Run Textual in inline mode for debugging")
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

