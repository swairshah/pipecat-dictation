from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widgets import ListView, ListItem, Collapsible, Static

from tui.core.utils.json_render import compact_json, pretty_json


class RTVIListPanel(ListView):
    """ListView that displays RTVI messages as collapsible items.

    - Compact title: one-line JSON
    - Expanded content: pretty-printed JSON
    - Unified selection on mouse / arrow keys
    - `copy_current()` returns text to copy (pretty when expanded, compact otherwise)
    """

    def compose(self) -> ComposeResult:  # type: ignore[override]
        yield from ()

    async def append_json(self, payload: Any) -> None:
        title = compact_json(payload)
        pretty = pretty_json(payload)
        c = Collapsible(Static(pretty), title=title, collapsed=True)
        setattr(c, "_compact_text", title)
        setattr(c, "_pretty_text", pretty)
        await self.append(ListItem(c))

    def copy_current(self) -> str | None:
        item = self.highlighted_child
        if item is None:
            try:
                item = self.query("ListItem.-highlight").first()
            except Exception:
                item = None
        if not item:
            return None
        c = item.query_one(Collapsible)
        if c.collapsed:
            return getattr(c, "_compact_text", c.title)
        return getattr(c, "_pretty_text", str(c.query_one(Static).renderable))

