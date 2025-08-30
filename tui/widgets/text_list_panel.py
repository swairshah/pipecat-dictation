from __future__ import annotations

from textual.widgets import ListView, ListItem, Static


class TextListPanel(ListView):
    """A simple list view that appends plain text items."""

    async def append_text(self, text: str) -> None:
        await self.append(ListItem(Static(text)))

    def copy_current(self) -> str | None:
        item = self.highlighted_child
        if item is None:
            try:
                item = self.query("ListItem.-highlight").first()
            except Exception:
                item = None
        if not item:
            return None
        try:
            static = item.query_one(Static)
            return str(static.renderable)
        except Exception:
            return None

