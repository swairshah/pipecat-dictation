import argparse
import asyncio
import importlib
import importlib.util
import json
import signal
import os
import sys
from types import ModuleType
from typing import Any, Optional
import io
import contextlib
import subprocess

try:
    import pyperclip  # type: ignore
except Exception:  # pragma: no cover - optional
    pyperclip = None  # type: ignore

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from macos.local_mac_transport import LocalMacTransport, LocalMacTransportParams
from pipecat.runner.types import RunnerArguments

from loguru import logger as _loguru

try:
    from textual.app import App, ComposeResult
    from textual.widgets import (
        Header,
        Footer,
        Static,
        Input,
        Log,
        ListView,
        ListItem,
        Collapsible,
    )
    from textual.containers import Horizontal, Vertical
except Exception:
    print(
        "This tool requires the 'textual' package. Install with: pip install textual",
        file=sys.stderr,
    )
    raise


def import_bot_module(path_or_module: str) -> ModuleType:
    # If a file path is passed, import it as a module
    if os.path.exists(path_or_module) and path_or_module.endswith(".py"):
        spec = importlib.util.spec_from_file_location("bot_module", path_or_module)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to import bot file: {path_or_module}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bot_module"] = mod
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        return mod
    # Otherwise treat it as a module path
    return importlib.import_module(path_or_module)


class BotTUI(App):
    CSS_PATH = None
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "toggle_log", "Log view"),
        ("ctrl+y", "copy_outbound", "Copy selected"),
    ]

    CSS = """
    Screen { layout: vertical; }
    #status { height: 3; border: round $accent; padding: 0 1; }
    #panes { layout: horizontal; height: 1fr; }
    .log { border: round $primary; height: 1fr; }
    #input { border: round $secondary; }

    /* Make outbound ListView items as compact as Log lines */
    #outbox ListItem { padding: 0; }
    #outbox Collapsible { padding-left: 0; padding-bottom: 0; border: none; }
    #outbox CollapsibleTitle { padding: 0 1; height: 1; }
    /* Unify selection: make ListItem highlight style match bold title focus */
    #outbox ListItem.-highlight CollapsibleTitle {
        background: $block-cursor-background;
        color: $block-cursor-foreground;
        text-style: $block-cursor-text-style;
    }

    /* Inbound panel compact + unified selection */
    #inbox ListItem { padding: 0; }
    #inbox Collapsible { padding-left: 0; padding-bottom: 0; border: none; }
    #inbox CollapsibleTitle { padding: 0 1; height: 1; }
    #inbox ListItem.-highlight CollapsibleTitle {
        background: $block-cursor-background;
        color: $block-cursor-foreground;
        text-style: $block-cursor-text-style;
    }
    """

    def __init__(self, bot_module: ModuleType):
        super().__init__()
        self._bot_module = bot_module
        self._transport = None
        self._bot_task: Optional[asyncio.Task] = None
        self._connected = False
        self._rtvi_sent_message_id = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical() as root:
            self.status = Static("Status: initializing", id="status")
            yield self.status
            with Horizontal(id="panes"):
                # Inbound panel: switch to ListView of Collapsible items
                self.inbox_list = ListView(id="inbox", classes="log")
                # Outbound panel: switch to ListView of Collapsible items
                self.outbox_list = ListView(id="outbox", classes="log")
                yield self.inbox_list
                yield self.outbox_list
            self.input = Input(placeholder="Waiting for connection...", id="input")
            yield self.input
            # Hidden syslog view (full screen when toggled)
            self.syslog = Log(id="syslog", classes="log")
            self.syslog.display = False
            yield self.syslog
        yield Footer()

    async def on_mount(self) -> None:
        # Create LocalMacTransport and run the bot as a background task

        params = LocalMacTransportParams(
            audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=SileroVADAnalyzer()
        )
        self._transport = LocalMacTransport(params=params)

        # Initialize headers after the app is active
        # Add an Inbound header row as a non-interactive list item
        await self.inbox_list.append(ListItem(Static("Inbound RTVI:")))
        # Add an Outbound header row as a non-interactive list item
        await self.outbox_list.append(ListItem(Static("Outbound RTVI:")))
        self.input.disabled = True
        self.set_focus(self.input)

        @self._transport.event_handler("on_client_connected")
        async def _on_connected(_transport, _client):
            self._connected = True
            self.status.update("Status: connected")
            self.input.disabled = False
            self.input.placeholder = "Type RTVI JSON or text and press Enter"

        @self._transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_transport, _client):
            self._connected = False
            self.status.update("Status: disconnected")
            self.input.disabled = True
            self.input.placeholder = "Waiting for connection..."

        @self._transport.event_handler("on_app_message")
        async def _on_app_message(_transport, message: Any):
            await self._append_inbound(message)

        @self._transport.event_handler("on_transport_message")
        async def _on_transport_message(_transport, frame):
            try:
                payload = getattr(frame, "message", frame)
            except Exception:
                payload = frame
            await self._append_outbound(payload)

        class UILineBuffer(io.TextIOBase):
            def __init__(self, app: App, log: Log):
                self._app = app
                self._log = log
                self._buf = ""

            def writable(self) -> bool:
                return True

            def write(self, s: str) -> int:  # type: ignore[override]
                if not isinstance(s, str):
                    s = str(s)
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    # We're in the app's asyncio loop; update directly
                    self._log.write_line(line)
                return len(s)

            def flush(self) -> None:  # type: ignore[override]
                if self._buf:
                    self._log.write_line(self._buf)
                    self._buf = ""

        async def run_bot_task():
            runner_args = RunnerArguments()
            if not hasattr(self._bot_module, "run_bot"):
                raise RuntimeError("Bot module does not define run_bot(transport, runner_args)")
            # Capture stdout/stderr to syslog while bot runs
            sink = UILineBuffer(self, self.syslog)
            # Also route loguru to the syslog panel and suppress default stderr sink
            try:
                try:
                    _loguru.remove()  # remove default handlers to prevent console leak
                except Exception:
                    pass

                # Add sink that writes to the UI thread
                def _log_sink(m: str):
                    try:
                        self.syslog.write_line(m.rstrip("\n"))
                    except Exception:
                        # Swallow logging errors to avoid disrupting the UI
                        pass

                self._loguru_sink_id = _loguru.add(_log_sink, level="DEBUG")  # type: ignore[attr-defined]
            except Exception:
                self._loguru_sink_id = None  # type: ignore[attr-defined]

            try:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    await self._bot_module.run_bot(self._transport, runner_args)  # type: ignore[attr-defined]
            finally:
                # Best effort: remove UI sink
                try:
                    if getattr(self, "_loguru_sink_id", None) is not None:
                        _loguru.remove(self._loguru_sink_id)  # type: ignore[attr-defined]
                except Exception:
                    pass

        self._bot_task = asyncio.create_task(run_bot_task())

    async def _cleanup(self) -> None:
        # Cancel bot task and cleanup transport
        try:
            if self._bot_task and not self._bot_task.done():
                self._bot_task.cancel()
                try:
                    await self._bot_task
                except asyncio.CancelledError:
                    pass
        finally:
            if self._transport:
                try:
                    await self._transport.cleanup()  # type: ignore[attr-defined]
                except Exception:
                    logger.exception("Transport cleanup failed")
            # Remove loguru sink if still attached
            try:
                if getattr(self, "_loguru_sink_id", None) is not None:
                    _loguru.remove(self._loguru_sink_id)  # type: ignore[attr-defined]
            except Exception:
                pass

    async def action_quit(self) -> None:  # type: ignore[override]
        await self._cleanup()
        await super().action_quit()

    async def action_toggle_log(self) -> None:
        # Toggle full-screen syslog vs main view
        show_log = not self.syslog.display
        self.syslog.display = show_log
        self.status.display = not show_log
        # Hide/show panes and input
        panes = self.query_one("#panes")
        panes.display = not show_log
        self.input.display = not show_log
        if show_log:
            self.set_focus(self.syslog)
        else:
            self.set_focus(self.input)

    # Ensure clicking a collapsible also updates the ListView selection so arrow keys follow it
    def _select_list_item_for(self, collapsible: Collapsible) -> None:
        try:
            node = collapsible  # type: ignore[assignment]
            from textual.widgets import ListItem as _LI  # local import to avoid top-level cycles
            while node is not None and not isinstance(node, _LI):
                node = node.parent  # type: ignore[assignment]
            if node is None:
                return
            # Determine which ListView contains this item
            # by walking up to either inbox_list or outbox_list
            container = node.parent
            target_list = None
            while container is not None:
                if container is self.inbox_list:
                    target_list = self.inbox_list
                    break
                if container is self.outbox_list:
                    target_list = self.outbox_list
                    break
                container = container.parent
            if target_list is None:
                return
            items = list(target_list.query("ListItem"))
            if node in items:
                target_list.index = items.index(node)
                target_list.focus()
        except Exception:
            pass

    def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:  # type: ignore[override]
        self._select_list_item_for(event.collapsible)

    def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:  # type: ignore[override]
        self._select_list_item_for(event.collapsible)

    def _render_compact_json(self, payload: Any) -> str:
        """Render a compact one-line JSON string."""
        try:
            # Compact with no spaces; ensure ASCII off to keep unicode readable
            return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            # Fallback to str if not JSON-serializable
            return str(payload).replace("\n", " ")

    def _render_pretty_json(self, payload: Any) -> str:
        """Render a pretty-printed JSON string for expanded view."""
        try:
            return json.dumps(payload, indent=2, ensure_ascii=False)
        except Exception:
            return str(payload)

    async def _append_inbound(self, payload: Any) -> None:
        """Append an inbound message to the ListView as a collapsible item."""
        title_text = self._render_compact_json(payload)
        pretty_text = self._render_pretty_json(payload)
        collapsible = Collapsible(
            Static(pretty_text),
            title=title_text,
            collapsed=True,
        )
        setattr(collapsible, "_compact_text", title_text)
        setattr(collapsible, "_pretty_text", pretty_text)
        await self.inbox_list.append(ListItem(collapsible))

    async def _append_outbound(self, payload: Any) -> None:
        """Append an outbound message to the ListView as a collapsible item."""
        title_text = self._render_compact_json(payload)
        pretty_text = self._render_pretty_json(payload)
        collapsible = Collapsible(
            Static(pretty_text),
            title=title_text,
            collapsed=True,
        )
        # Attach raw strings for reliable copy (avoid pulling UI text that may contain arrows)
        setattr(collapsible, "_compact_text", title_text)
        setattr(collapsible, "_pretty_text", pretty_text)
        await self.outbox_list.append(ListItem(collapsible))

    def _copy_to_clipboard(self, text: str) -> bool:
        copied = False
        try:
            if pyperclip is not None:
                pyperclip.copy(text)  # type: ignore[attr-defined]
                copied = True
        except Exception:
            copied = False
        if not copied:
            try:
                subprocess.run(["pbcopy"], input=text, text=True, check=True)
                copied = True
            except Exception:
                copied = False
        return copied

    async def action_copy_outbound(self) -> None:
        """Copy current selection if present; otherwise copy Outbound item.

        For Outbound item: copies pretty JSON if expanded, else compact title.
        The expansion arrow is not included.
        """
        # 1) Try to copy any current textual selection from focused widget
        try:
            focused = self.focused  # type: ignore[attr-defined]
        except Exception:
            focused = None
        if focused is not None:
            selection = getattr(focused, "text_selection", None)
            get_sel = getattr(focused, "get_selection", None)
            if selection is not None and callable(get_sel):
                try:
                    res = get_sel(selection)  # type: ignore[misc]
                    if res:
                        text, _ending = res
                        if text:
                            if self._copy_to_clipboard(text):
                                self.syslog.write_line("[info] Copied selection to clipboard")
                            else:
                                self.syslog.write_line("[warn] Failed to copy selection")
                            return
                except Exception:
                    pass

        # 2) Fallback: copy from highlighted outbound item (or inbound if none)
        item = None
        try:
            item = self.outbox_list.highlighted_child  # type: ignore[attr-defined]
        except Exception:
            item = None
        # If ListView didn't track highlight, try querying the CSS highlight class
        if item is None:
            try:
                item = self.outbox_list.query("ListItem.-highlight").first()
            except Exception:
                item = None
        # Try inbound if still none
        if item is None:
            try:
                item = self.inbox_list.highlighted_child  # type: ignore[attr-defined]
            except Exception:
                item = None
        if item is None:
            try:
                item = self.inbox_list.query("ListItem.-highlight").first()
            except Exception:
                item = None
        if not item:
            self.syslog.write_line("[warn] Nothing to copy: no selection and no outbound item highlighted")
            return
        try:
            collapsible = item.query_one(Collapsible)
            compact_text = getattr(collapsible, "_compact_text", collapsible.title)
            pretty_text = getattr(
                collapsible,
                "_pretty_text",
                str(getattr(collapsible.query_one(Static), "renderable", "")),
            )
            text = pretty_text if not collapsible.collapsed else compact_text
        except Exception as e:
            self.syslog.write_line(f"[error] Couldn't extract outbound text: {e}")
            return

        if self._copy_to_clipboard(text):
            self.syslog.write_line("[info] Copied outbound JSON to clipboard")
        else:
            self.syslog.write_line("[warn] Failed to copy to clipboard")

    # As a final measure, clicking anywhere in an Outbound item selects it for arrow navigation
    def on_click(self, event) -> None:  # type: ignore[override]
        try:
            # Check if click occurred within the Outbound list subtree
            node = event.control
            within_outbox = False
            within_inbox = False
            probe = node
            while probe is not None:
                if probe is self.outbox_list:
                    within_outbox = True
                    break
                if probe is self.inbox_list:
                    within_inbox = True
                    break
                probe = probe.parent
            if not within_outbox and not within_inbox:
                return
            # Walk up to the containing ListItem and select it
            from textual.widgets import ListItem as _LI  # local import
            while node is not None and not isinstance(node, _LI):
                node = node.parent
            if node is None:
                return
            target_list = self.outbox_list if within_outbox else self.inbox_list
            items = list(target_list.query("ListItem"))
            if node in items:
                target_list.index = items.index(node)
        except Exception:
            pass

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._transport:
            return
        text = event.value.strip()
        if not text:
            return
        # Try JSON, else send raw string
        try:
            msg = json.loads(text)
        except Exception:
            msg = {
                "id": str(self._rtvi_sent_message_id),
                "label": "rtvi-ai",
                "type": "input",
                "data": {"text": text},
            }
        if not self._connected:
            self.syslog.write_line("[warn] Ignoring message: transport not connected")
        else:
            try:
                await self._transport.send_app_message(msg)
                self._rtvi_sent_message_id += 1
            except Exception as e:
                self.syslog.write_line(f"[error] Failed to send app message: {e}")
        self.input.value = ""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TUI to launch a Pipecat bot with LocalMacTransport"
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--bot", help="Python module path, e.g. my_pkg.my_bot")
    g.add_argument("--file", help="Path to a bot Python file, e.g. ./bot-realtime-api.py")
    args = parser.parse_args(argv)

    mod = import_bot_module(args.bot or args.file)
    app = BotTUI(mod)

    # Ensure Ctrl-C from terminal exits the app cleanly
    def _sigint_handler(signum, frame):  # noqa: ARG001
        try:
            # Schedule quit in the app's loop
            app.call_from_thread(lambda: asyncio.create_task(app.action_quit()))
        except Exception:
            pass

    signal.signal(signal.SIGINT, _sigint_handler)
    try:
        app.run()
        return 0
    except KeyboardInterrupt:
        # Ensure cleanup even on Ctrl-C from terminal
        try:
            asyncio.run(app._cleanup())
        except Exception:
            pass
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
