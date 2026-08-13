from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _WeiboHTMLParser(HTMLParser):
    BLOCK_TAGS = {"br", "p", "div", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.location = ""
        self.article = False
        self._capture_location = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag = tag.lower()

        if tag in ("script", "style"):
            self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

        if tag == "img":
            src = attrs_dict.get("src", "")
            alt = attrs_dict.get("alt", "")
            if "timeline_card_small_location_default" in src:
                self._capture_location = True
            if alt:
                self.parts.append(alt)

        if tag == "a":
            data_url = attrs_dict.get("data-url", "")
            if data_url.startswith("http://t.cn") or data_url.startswith("https://t.cn"):
                # Exact article semantics are confirmed after the visible text is parsed.
                pass

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if not data:
            return
        if self._capture_location and data.strip():
            self.location = data.strip()
            self._capture_location = False
            return
        self.parts.append(data)


def html_to_text(value: object) -> tuple[str, str, bool]:
    raw = "" if value is None else str(value)
    parser = _WeiboHTMLParser()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", "", raw)

    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    article = text.startswith("发布了头条文章")
    return text, parser.location, article


def strip_html(value: object) -> str:
    return html_to_text(value)[0]
