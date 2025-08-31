from textual.widgets import ListView, ListItem, Static


class TextListPanel(ListView):
    """A simple list view that appends plain text items."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_item = None
        self._last_text = ""

    async def append_text_item(self, text: str) -> None:
        self._last_item = ListItem(Static(text))
        self._last_text = text
        await self.append(self._last_item)

    async def append_text_to_last_item(self, text: str) -> None:
        if self._last_item is not None:
            self._last_text = self._last_text + text
            self._last_item.children[0].update(self._last_text)

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
