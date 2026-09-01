from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class PageMetadata:
    title: str = ""
    published_at: str = ""


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og_title = ""
        self.document_title: list[str] = []
        self.h1_title: list[str] = []
        self.published_at = ""
        self.time_value = ""
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.h1_title:
            self._in_h1 = True
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name", "")).casefold()
            content = " ".join(attributes.get("content", "").split())
            if key == "og:title" and content and not self.og_title:
                self.og_title = content
            if key in {
                "article:published_time",
                "datepublished",
                "date",
                "pubdate",
            } and content and not self.published_at:
                self.published_at = content
        elif tag == "time" and not self.time_value:
            self.time_value = " ".join(attributes.get("datetime", "").split())

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self._in_h1:
            self.h1_title.append(value)
        elif self._in_title:
            self.document_title.append(value)


def extract_page_metadata(document: str, content_type: str) -> PageMetadata:
    if not document or "html" not in (content_type or "").casefold():
        return PageMetadata()
    parser = _MetadataParser()
    parser.feed(document)
    h1 = " ".join(parser.h1_title).strip()
    document_title = " ".join(parser.document_title).strip()
    return PageMetadata(
        title=h1 or parser.og_title or document_title,
        published_at=parser.published_at or parser.time_value,
    )
