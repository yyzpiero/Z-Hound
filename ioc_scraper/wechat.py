from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import urljoin


ALBUM_FIELDS = ("__biz", "action", "album_id")
PAGINATION_FIELDS = ("begin", "count")
ARTICLE_FIELDS = ("__biz", "mid", "idx", "sn", "chksm")


@dataclass(frozen=True)
class WeChatListingItem:
    url: str
    title: str = ""
    published_at: str = ""
    publisher_name: str = ""
    publisher_id: str = ""


@dataclass(frozen=True)
class WeChatListing:
    items: tuple[WeChatListingItem, ...] = ()
    follow_urls: tuple[str, ...] = ()
    structure_recognized: bool = False
    empty_confirmed: bool = False


@dataclass(frozen=True)
class WeChatArticle:
    title: str = ""
    published_at: str = ""
    publisher_name: str = ""
    publisher_id: str = ""
    album_url: str = ""
    content_html: str = ""


def _decoded(value: object) -> str:
    text = unescape("" if value is None else str(value))
    return (
        text.replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\U0026", "&")
        .replace("\\x26", "&")
    )


def _published_at(value: object) -> str:
    text = _decoded(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
        try:
            parsed = datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
        return parsed.isoformat().replace("+00:00", "Z")
    chinese = re.fullmatch(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{2}))?",
        text,
    )
    if chinese:
        year, month, day, hour, minute = chinese.groups()
        try:
            parsed = datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                tzinfo=timezone(timedelta(hours=8)),
            )
        except ValueError:
            return ""
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _attrs_html(attrs: list[tuple[str, str | None]]) -> str:
    values = []
    for key, value in attrs:
        if value is None:
            values.append(f" {key}")
        else:
            values.append(f' {key}="{escape(value, quote=True)}"')
    return "".join(values)


def _is_listing_script(script: str) -> bool:
    folded = script.casefold()
    return (
        "album" in folded
        or "appmsgalbum" in folded
        or ('"items"' in folded and "mp.weixin.qq.com/s" in folded)
    )


class _ListingParser(HTMLParser):
    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, dict[str, str], bool]] = []
        self.scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self.structure_recognized = False
        self._anchor_attrs: dict[str, str] | None = None
        self._anchor_in_album = False
        self._anchor_text: list[str] = []
        self._in_script = False
        self._script_text: list[str] = []
        self._album_depth = 0
        self._album_context_stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        values = {key.casefold(): value or "" for key, value in attrs}
        marker = " ".join((values.get("id", ""), values.get("class", "")))
        current_album_context = self._album_depth > 0 or "album" in marker.casefold()
        if "album" in marker.casefold():
            self.structure_recognized = True
        if tag not in self.VOID_ELEMENTS:
            self._album_context_stack.append(current_album_context)
            if current_album_context:
                self._album_depth += 1
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            if key and values.get("content"):
                self.meta[key] = values["content"]
        if tag == "a" and values.get("href"):
            self._anchor_attrs = values
            self._anchor_in_album = current_album_context
            self._anchor_text = []
        if tag == "script":
            self._in_script = True
            self._script_text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor_attrs is not None:
            title = " ".join(" ".join(self._anchor_text).split())
            self.anchors.append(
                (
                    self._anchor_attrs.get("href", ""),
                    title,
                    self._anchor_attrs,
                    self._anchor_in_album,
                )
            )
            self._anchor_attrs = None
            self._anchor_in_album = False
            self._anchor_text = []
        if tag == "script" and self._in_script:
            script = "".join(self._script_text)
            self.scripts.append(script)
            if _is_listing_script(script):
                self.structure_recognized = True
            self._in_script = False
            self._script_text = []
        if self._album_context_stack:
            was_album_context = self._album_context_stack.pop()
            if was_album_context:
                self._album_depth -= 1

    def handle_data(self, data):
        if self._anchor_attrs is not None and data.strip():
            self._anchor_text.append(data.strip())
        if self._in_script:
            self._script_text.append(data)


class _ArticleParser(HTMLParser):
    VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.scripts: list[str] = []
        self.album_urls: list[str] = []
        self.content: list[str] = []
        self.title_text: list[str] = []
        self.nickname_text: list[str] = []
        self._content_depth = 0
        self._content_excluded_depth = 0
        self._in_script = False
        self._script_text: list[str] = []
        self._in_title = False
        self._nickname_depth = 0

    def handle_starttag(self, tag, attrs):
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").casefold()
            if key and values.get("content"):
                self.meta[key] = values["content"]
        if tag == "script":
            self._in_script = True
            self._script_text = []
        if tag == "title":
            self._in_title = True
        if "profile_nickname" in values.get("class", "").casefold():
            self._nickname_depth = 1
        elif self._nickname_depth:
            self._nickname_depth += 1
        if tag == "a" and values.get("href"):
            absolute = urljoin("https://mp.weixin.qq.com/", _decoded(values["href"]))
            if wechat_url_kind(absolute) == "wechat_album":
                self.album_urls.append(canonicalize_wechat_url(absolute))
        if self._content_depth:
            if self._content_excluded_depth:
                if tag not in self.VOID_ELEMENTS:
                    self._content_excluded_depth += 1
                    self._content_depth += 1
            elif tag in {"script", "style"}:
                self._content_excluded_depth = 1
                self._content_depth += 1
            else:
                self.content.append(f"<{tag}{_attrs_html(attrs)}>")
                if tag not in self.VOID_ELEMENTS:
                    self._content_depth += 1
        elif values.get("id", "").casefold() == "js_content":
            self._content_depth = 1

    def handle_startendtag(self, tag, attrs):
        if (
            self._content_depth
            and not self._content_excluded_depth
            and tag not in {"script", "style"}
        ):
            self.content.append(f"<{tag}{_attrs_html(attrs)}>")

    def handle_endtag(self, tag):
        if self._content_depth:
            if self._content_excluded_depth:
                self._content_excluded_depth -= 1
                self._content_depth -= 1
            else:
                if self._content_depth > 1:
                    self.content.append(f"</{tag}>")
                self._content_depth -= 1
        if tag == "script" and self._in_script:
            self.scripts.append("".join(self._script_text))
            self._in_script = False
            self._script_text = []
        if tag == "title":
            self._in_title = False
        if self._nickname_depth:
            self._nickname_depth -= 1

    def handle_data(self, data):
        if self._content_depth and not self._content_excluded_depth:
            self.content.append(escape(data))
        if self._in_script:
            self._script_text.append(data)
        if self._in_title and data.strip():
            self.title_text.append(data.strip())
        if self._nickname_depth and data.strip():
            self.nickname_text.append(data.strip())


def _json_values(script: str):
    decoder = json.JSONDecoder()
    for index, character in enumerate(script):
        if character not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(script[index:])
        except json.JSONDecodeError:
            continue
        yield value


def _walk_json(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _first(mapping: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return ""


def wechat_url_kind(url: str) -> str:
    parsed = urlsplit(unescape((url or "").strip()).replace("\\/", "/"))
    if (parsed.hostname or "").casefold() != "mp.weixin.qq.com":
        return ""
    path = parsed.path.rstrip("/") or "/"
    if path == "/mp/appmsgalbum":
        return "wechat_album"
    if path == "/s" or path.startswith("/s/"):
        return "wechat_article"
    return ""


def canonicalize_wechat_url(url: str, keep_pagination: bool = False) -> str:
    cleaned = unescape((url or "").strip()).replace("\\/", "/")
    parsed = urlsplit(cleaned)
    kind = wechat_url_kind(cleaned)
    if not kind:
        return ""
    values = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if kind == "wechat_album":
        fields = ALBUM_FIELDS + (PAGINATION_FIELDS if keep_pagination else ())
    else:
        fields = ARTICLE_FIELDS
    query = urlencode([(key, values[key]) for key in fields if key in values])
    return urlunsplit(
        ("https", "mp.weixin.qq.com", parsed.path or "/", query, "")
    )


def wechat_album_identity(url: str) -> tuple[str, str] | None:
    canonical = canonicalize_wechat_url(url, keep_pagination=True)
    if wechat_url_kind(canonical) != "wechat_album":
        return None
    values = dict(parse_qsl(urlsplit(canonical).query, keep_blank_values=True))
    biz = values.get("__biz", "")
    album_id = values.get("album_id", "")
    if not biz or not album_id:
        return None
    return biz, album_id


def parse_wechat_listing(document: str, base_url: str) -> WeChatListing:
    parser = _ListingParser()
    parser.feed(document or "")
    items: dict[str, WeChatListingItem] = {}
    follow_urls: dict[str, None] = {}
    scripts = "\n".join(parser.scripts)
    global_publisher_name = (
        parser.meta.get("og:article:author", "")
        or parser.meta.get("article:author", "")
    ).strip()
    if not global_publisher_name:
        publisher_match = re.search(
            r"(?:nickname|nick_name)[\"']?\s*[:=]\s*[\"']([^\"']+)",
            scripts,
        )
        if publisher_match:
            global_publisher_name = publisher_match.group(1).strip()
    account_match = re.search(
        r"(?:user_name|account_id)[\"']?\s*[:=]\s*[\"']([^\"']+)",
        scripts,
    )
    global_publisher_id = account_match.group(1).strip() if account_match else ""

    def add_item(
        value: object,
        title: object = "",
        published: object = "",
        publisher_name: object = "",
        publisher_id: object = "",
    ) -> None:
        absolute = urljoin(base_url, _decoded(value))
        canonical = canonicalize_wechat_url(absolute)
        if wechat_url_kind(canonical) != "wechat_article":
            return
        incoming = WeChatListingItem(
            url=canonical,
            title=_decoded(title).strip(),
            published_at=_published_at(published),
            publisher_name=(
                _decoded(publisher_name).strip() or global_publisher_name
            ),
            publisher_id=_decoded(publisher_id).strip() or global_publisher_id,
        )
        previous = items.get(canonical)
        if previous is None:
            items[canonical] = incoming
            return
        items[canonical] = WeChatListingItem(
            url=canonical,
            title=previous.title or incoming.title,
            published_at=previous.published_at or incoming.published_at,
            publisher_name=previous.publisher_name or incoming.publisher_name,
            publisher_id=previous.publisher_id or incoming.publisher_id,
        )

    def add_follow(value: object) -> None:
        absolute = urljoin(base_url, _decoded(value))
        canonical = canonicalize_wechat_url(absolute, keep_pagination=True)
        if wechat_url_kind(canonical) == "wechat_album" and canonical:
            follow_urls[canonical] = None

    for href, title, attrs, in_album_context in parser.anchors:
        if not in_album_context:
            continue
        absolute = urljoin(base_url, _decoded(href))
        kind = wechat_url_kind(absolute)
        if kind == "wechat_article":
            published = (
                attrs.get("data-create-time")
                or attrs.get("data-publish-time")
                or attrs.get("data-time")
                or ""
            )
            add_item(absolute, title, published)
        elif kind == "wechat_album":
            add_follow(absolute)

    link_keys = ("url", "link", "href", "content_url")
    title_keys = ("title", "digest")
    time_keys = ("create_time", "publish_time", "published_at")
    publisher_keys = ("publisher_name", "nickname", "author")
    account_keys = ("publisher_id", "user_name", "account_id")
    next_keys = ("next_url", "nextUrl")
    for script in parser.scripts:
        if not _is_listing_script(script):
            continue
        for root in _json_values(script):
            for value in _walk_json(root):
                if not isinstance(value, dict):
                    continue
                link = _first(value, link_keys)
                if link:
                    absolute = urljoin(base_url, _decoded(link))
                    kind = wechat_url_kind(absolute)
                    if kind == "wechat_article":
                        add_item(
                            absolute,
                            _first(value, title_keys),
                            _first(value, time_keys),
                            _first(value, publisher_keys),
                            _first(value, account_keys),
                        )
                    elif kind == "wechat_album":
                        add_follow(absolute)
                for key in next_keys:
                    if value.get(key):
                        add_follow(value[key])
        decoded_script = _decoded(script)
        for match in re.finditer(
            r"https?://mp\.weixin\.qq\.com/(?:s(?:/[A-Za-z0-9_-]+|\?[^\"'<>\\\s;]+)|mp/appmsgalbum\?[^\"'<>\\\s;]+)",
            decoded_script,
            re.I,
        ):
            absolute = match.group(0)
            if wechat_url_kind(absolute) == "wechat_article":
                add_item(absolute)
            elif wechat_url_kind(absolute) == "wechat_album":
                add_follow(absolute)

    structure_recognized = parser.structure_recognized or bool(items) or bool(follow_urls)
    folded_document = unescape(document or "").casefold()
    empty_confirmed = any(
        marker in folded_document
        for marker in ("暂无文章", "暂无内容", "暂时没有文章", "no articles")
    )
    return WeChatListing(
        items=tuple(items.values()),
        follow_urls=tuple(follow_urls),
        structure_recognized=structure_recognized,
        empty_confirmed=empty_confirmed,
    )


def parse_wechat_article(document: str) -> WeChatArticle:
    parser = _ArticleParser()
    parser.feed(document or "")
    scripts = "\n".join(parser.scripts)
    json_title = json_published = json_publisher_name = json_publisher_id = ""
    metadata_signal_keys = {
        "create_time", "publish_time", "published_at", "user_name",
        "account_id", "publisher_id", "nickname", "publisher_name",
    }
    for script in parser.scripts:
        for root in _json_values(script):
            for value in _walk_json(root):
                if not isinstance(value, dict):
                    continue
                if not metadata_signal_keys.intersection(value):
                    continue
                json_title = json_title or _decoded(
                    _first(value, ("title", "msg_title"))
                ).strip()
                json_published = json_published or _decoded(
                    _first(
                        value,
                        ("create_time", "publish_time", "published_at"),
                    )
                ).strip()
                json_publisher_name = json_publisher_name or _decoded(
                    _first(value, ("publisher_name", "nickname", "author"))
                ).strip()
                json_publisher_id = json_publisher_id or _decoded(
                    _first(
                        value,
                        ("publisher_id", "user_name", "account_id"),
                    )
                ).strip()
    title = (
        parser.meta.get("og:title", "")
        or " ".join(parser.title_text)
        or json_title
    )
    publisher_name = (
        parser.meta.get("og:article:author", "")
        or parser.meta.get("article:author", "")
        or " ".join(parser.nickname_text)
        or json_publisher_name
    )
    published = (
        parser.meta.get("article:published_time", "")
        or parser.meta.get("og:article:published_time", "")
        or json_published
    )
    if not published:
        match = re.search(
            r"(?:ct|create_time|publish_time)\s*[:=]\s*[\"']?(\d{10})",
            scripts,
        )
        published = match.group(1) if match else ""
    account_match = re.search(
        r"(?:user_name|account_id)\s*[:=]\s*[\"']([^\"']+)",
        scripts,
    )
    publisher_id = json_publisher_id or (
        account_match.group(1).strip() if account_match else ""
    )
    album_url = next((value for value in parser.album_urls if value), "")
    return WeChatArticle(
        title=unescape(title).strip(),
        published_at=_published_at(published),
        publisher_name=unescape(publisher_name).strip(),
        publisher_id=unescape(publisher_id).strip(),
        album_url=album_url,
        content_html="".join(parser.content).strip(),
    )
