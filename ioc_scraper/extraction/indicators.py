from __future__ import annotations

from dataclasses import dataclass, replace
from html.parser import HTMLParser
import ipaddress
import re
from urllib.parse import urlsplit

from ..models import IndicatorCandidate


METHOD_STRENGTH = {
    "document_scan": 0,
    "context": 1,
    "ioc_section": 2,
    "table": 3,
}
STRONG_LABEL = re.compile(
    r"(?i)^\s*(?:"
    r"iocs?|indicators(?:\s+of\s+compromise)?|indicator\s+of\s+compromise|"
    r"network\s+iocs?|host\s+iocs?|atomic(?:\s+indicators?)?|"
    r"c2|c\s*&\s*c|"
    r"失陷指标|威胁指标|指示器"
    r")\s*[:：]?\s*$"
)
INDICATORS_FAMILY = re.compile(
    r"(?i)^\s*(?:iocs?|indicators(?:\s+of\s+compromise)?|"
    r"indicator\s+of\s+compromise|"
    r"network\s+iocs?|host\s+iocs?|atomic(?:\s+indicators?)?|"
    r"失陷指标|威胁指标|指示器)\s*[:：]?\s*$"
)
ATOMIC_LABEL = re.compile(r"(?i)^\s*atomic\s*[:：]?\s*$")
WEAK_LABEL = re.compile(
    r"(?i)^\s*(?:domains?|urls?|hashes?|file\s*hashes?)\s*[:：]?\s*$"
)
REFERENCE_STOP = re.compile(
    r"(?i)^\s*(?:"
    r"参考链接|参考文献|相关链接|references?|see also|related articles?|"
    r"阅读原文|团队介绍|team introduction|"
    r"关于.{0,40}(?:实验室|研究院|研究中心)"
    r")\s*[:：]?\s*$"
)
POINTER_HINT = re.compile(
    r"(?i)\b(?:github(?:\s+repository)?|"
    r"iocs?\s+(?:for this threat\s+)?(?:are|is)\s+also\s+available|"
    r"available\s+(?:at|on|from)|download\s+(?:the\s+)?iocs?|ioc\s+repository)\b"
)
TYPE_COLUMN_LABEL = re.compile(
    r"(?i)^\s*(?:indicator|type|context|description|sha-?256|sha-?1|md5|sha-?512|"
    r"email address|domain|hostname|url|hash|ipv4|ipv6)\s*[:：]?\s*$"
)
FIELD_LABEL_PREFIX = re.compile(
    r"(?i)^\s*(?:sha-?256(?:\s+hash)?|sha-?1(?:\s+hash)?|md5(?:\s+hash)?|"
    r"sha-?512(?:\s+hash)?|file\s+(?:size|type|description|name)|"
    r"package\s+name|vhash|domain|url|hash|description|ipv4|ipv6|"
    r"email(?:\s+address)?|hostname|indicator)\s*:"
)
IOC_LINE_HINT = re.compile(
    r"(?i)https?://|hxxp|\[\.]|:\d{2,5}\b|(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])|"
    r"(?:[a-z0-9-]+\.)+[a-z]{2,24}"
)
FILE_EXTENSIONS = (
    "exe|dll|sys|bin|dat|js|vbs|ps1|bat|cmd|jar|apk|docm|xlsm|xls|xlsx|doc|docx|"
    "pdf|application|manifest|lnk|hta|scr|msi|iso|img|zip|rar|7z|gz|txt|log|db|"
    "php|asp|aspx|jsp|cgi"
)
FILE_LIKE_TLDS = frozenset(
    extension.casefold() for extension in FILE_EXTENSIONS.split("|")
)
HIT_TYPES = frozenset(
    {"md5", "sha1", "sha256", "sha512", "ipv4", "ipv6", "domain", "url", "email"}
)
END_FRACTION = 0.6
SHORT_ARTICLE_BLOCKS = 12


@dataclass(frozen=True)
class _Block:
    text: str
    method: str
    in_table: bool = False


class _VisibleBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._skip = 0
        self._table = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag == "table":
            self._table += 1

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        if tag == "table" and self._table:
            self._table -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.blocks.append(
            _Block(text, "document_scan", in_table=bool(self._table))
        )


def _looks_like_narrative(text: str) -> bool:
    """True for sentence-like footer/bio prose, not single-token blobs or field lines."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) < 80 or IOC_LINE_HINT.search(cleaned):
        return False
    if FIELD_LABEL_PREFIX.match(cleaned):
        return False
    words = cleaned.split()
    alpha_words = 0
    for word in words:
        letters = sum(1 for char in word if char.isalpha())
        if letters >= 3:
            alpha_words += 1
    return alpha_words >= 6


def _label_kind(text: str, blocks: list[_Block], index: int) -> str | None:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None
    if STRONG_LABEL.match(cleaned):
        if ATOMIC_LABEL.match(cleaned):
            prior = blocks[max(0, index - 3) : index]
            if not any(
                INDICATORS_FAMILY.match(" ".join(block.text.split()))
                for block in prior
            ):
                return None
        return "strong"
    if WEAK_LABEL.match(cleaned):
        return "weak"
    return None


def _section_window(blocks: list[_Block], start: int) -> tuple[int, int]:
    end = len(blocks)
    for index in range(start + 1, len(blocks)):
        text = blocks[index].text
        cleaned = " ".join(text.split())
        if REFERENCE_STOP.match(cleaned):
            end = index
            break
        if _label_kind(text, blocks, index) == "strong":
            end = index
            break
        if _looks_like_narrative(cleaned):
            end = index
            break
    return start, end


def _window_metrics(
    blocks: list[_Block], start: int, end: int
) -> tuple[float, int, bool, bool]:
    hit_blocks = 0
    content_blocks = 0
    has_table_hits = False
    texts: list[str] = []
    for index in range(start, end):
        block = blocks[index]
        cleaned = " ".join(block.text.split())
        texts.append(cleaned)
        if index == start:
            continue
        if TYPE_COLUMN_LABEL.match(cleaned) or FIELD_LABEL_PREFIX.match(cleaned):
            continue
        if len(cleaned) <= 2:
            continue
        hits = [
            item for item in _extract_block(block) if item.type in HIT_TYPES
        ]
        if hits:
            content_blocks += 1
            hit_blocks += 1
            if block.in_table:
                has_table_hits = True
            continue
        # Skip short subsection titles that are not IoC values.
        if len(cleaned) < 40 and not IOC_LINE_HINT.search(cleaned):
            continue
        content_blocks += 1
    density = (hit_blocks / content_blocks) if content_blocks else 0.0
    joined = " ".join(texts)
    pointer = bool(POINTER_HINT.search(joined) and hit_blocks < 2)
    return density, hit_blocks, has_table_hits, pointer


def _accept_window(
    kind: str,
    start: int,
    end: int,
    total: int,
    density: float,
    hit_blocks: int,
    has_table_hits: bool,
    pointer: bool,
) -> bool:
    if pointer:
        return False
    near_end = total < SHORT_ARTICLE_BLOCKS or start >= int(total * END_FRACTION)
    if kind == "weak":
        return density >= 0.50 and hit_blocks >= 5
    if near_end:
        if has_table_hits:
            return True
        return density >= 0.25 and hit_blocks >= 2
    # Mid-article: require density, or enough absolute hits for long IoC lists.
    if hit_blocks >= 8 and density >= 0.20:
        return True
    return density >= 0.35 and hit_blocks >= 3


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _accepted_ranges(blocks: list[_Block]) -> list[tuple[int, int]]:
    accepted: list[tuple[int, int]] = []
    for index, block in enumerate(blocks):
        kind = _label_kind(block.text, blocks, index)
        if kind is None:
            continue
        start, end = _section_window(blocks, index)
        density, hit_blocks, has_table_hits, pointer = _window_metrics(
            blocks, start, end
        )
        if _accept_window(
            kind,
            start,
            end,
            len(blocks),
            density,
            hit_blocks,
            has_table_hits,
            pointer,
        ):
            accepted.append((start, end))
    return _merge_ranges(accepted)


def _restrict_to_structured(blocks: list[_Block]) -> list[_Block]:
    """Assign structured methods only inside density-accepted IoC windows."""
    ranges = _accepted_ranges(blocks)
    restricted: list[_Block] = []
    for index, block in enumerate(blocks):
        inside = any(start <= index < end for start, end in ranges)
        if not inside:
            restricted.append(_Block(block.text, "document_scan", block.in_table))
        elif block.in_table:
            restricted.append(_Block(block.text, "table", True))
        else:
            restricted.append(_Block(block.text, "ioc_section", False))
    return restricted


def normalize_defanged(value: str) -> str:
    value = re.sub(r"(?i)hxxps", "https", value)
    value = re.sub(r"(?i)hxxp", "http", value)
    value = re.sub(r"\[(?:://)\]", "://", value)
    value = re.sub(r"\[\.\]|\(\.\)|\{\.\}|<\.>", ".", value)
    value = re.sub(r"\[:\]|\(:\)|\{:\}|：", ":", value)
    value = value.replace("\\/", "/")
    return value


def _valid_port(value: str | int) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _valid_domain(value: str) -> bool:
    value = value.rstrip(".").casefold()
    if len(value) > 253 or "." not in value:
        return False
    labels = value.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or label.startswith("0x")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        return False
    if labels[-1] in FILE_LIKE_TLDS:
        return False
    return bool(re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})", labels[-1]))


def _actionable_ip(value: str) -> str | None:
    """Return a normalized address unless it is a local placeholder."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if address.is_unspecified or address.is_loopback:
        return None
    if str(address) == "255.255.255.255":
        return None
    return str(address)


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _candidate(
    content: str,
    type_: str,
    port: int | None,
    block: _Block,
) -> IndicatorCandidate:
    evidence = block.text[:500]
    return IndicatorCandidate(
        content=content,
        type=type_,
        port=port,
        evidence=evidence,
        extraction_method=block.method,
        context=evidence,
    )


URL_PATTERN = re.compile(r"(?i)\b(?:https?|file|tcp|udp|wss)://[^\s<>\"']+")
EMAIL_PATTERN = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}\b")
IPV4_PORT_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])((?:\d{1,3}\.){3}\d{1,3}):(\d{1,6})(?!\d)")
DOMAIN_PORT_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_@.\\/-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}):(\d{1,6})(?!\d)"
)
IPV4_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])(?:\d{1,3}\.){3}\d{1,3}(?![A-Za-z0-9_.])")
IPV6_PORT_PATTERN = re.compile(r"\[([0-9A-Fa-f:]{2,})\]:(\d{1,6})(?!\d)")
IPV6_PATTERN = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
DOMAIN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_@.\\/-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![A-Za-z0-9_.\\/-])"
)
HASH_PATTERNS = (
    ("sha512", re.compile(r"(?i)(?<![a-f0-9])[a-f0-9]{128}(?![a-f0-9])")),
    ("sha256", re.compile(r"(?i)(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])")),
    ("sha1", re.compile(r"(?i)(?<![a-f0-9])[a-f0-9]{40}(?![a-f0-9])")),
    ("md5", re.compile(r"(?i)(?<![a-f0-9])[a-f0-9]{32}(?![a-f0-9])")),
)
CVE_PATTERN = re.compile(r"(?i)\bCVE-\d{4}-\d{4,7}\b")
FILENAME_PATTERN = re.compile(
    rf"(?i)(?<![\w.-])[a-z0-9_()\[\]~-]+\.(?:{FILE_EXTENSIONS})(?![\w.-])"
)


def _extract_block(block: _Block) -> list[IndicatorCandidate]:
    text = normalize_defanged(block.text)
    found: list[IndicatorCandidate] = []
    consumed: list[tuple[int, int]] = []

    for match in URL_PATTERN.finditer(text):
        raw = match.group(0).rstrip(".,;!?)。；，")
        try:
            parsed = urlsplit(raw)
            if not parsed.hostname:
                continue
            try:
                parsed_ip = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                parsed_ip = None
            if parsed_ip is not None and _actionable_ip(str(parsed_ip)) is None:
                continue
            port = parsed.port
        except ValueError:
            continue
        found.append(_candidate(raw, "url", port, block))
        consumed.append((match.start(), match.start() + len(raw)))

    remaining = _mask(text, consumed)
    for match in EMAIL_PATTERN.finditer(remaining):
        found.append(_candidate(match.group(0).casefold(), "email", None, block))
        consumed.append((match.start(), match.end()))
    remaining = _mask(text, consumed)

    for type_, pattern in HASH_PATTERNS:
        for match in pattern.finditer(remaining):
            found.append(_candidate(match.group(0).casefold(), type_, None, block))
            consumed.append((match.start(), match.end()))
    for match in CVE_PATTERN.finditer(remaining):
        found.append(_candidate(match.group(0).upper(), "cve", None, block))
        consumed.append((match.start(), match.end()))
    remaining = _mask(text, consumed)

    for match in IPV6_PORT_PATTERN.finditer(remaining):
        host = _actionable_ip(match.group(1))
        if host is None:
            continue
        port = _valid_port(match.group(2))
        found.append(_candidate(host, "ipv6", port, block))
        if port is not None:
            consumed.append((match.start(), match.end()))

    for pattern, type_ in (
        (IPV4_PORT_PATTERN, "ipv4"),
        (DOMAIN_PORT_PATTERN, "domain"),
    ):
        for match in pattern.finditer(remaining):
            host = match.group(1).casefold().rstrip(".")
            valid = _valid_domain(host)
            if type_ == "ipv4":
                host = _actionable_ip(host)
                valid = host is not None and "." in host
            if not valid:
                continue
            port = _valid_port(match.group(2))
            found.append(_candidate(host, type_, port, block))
            if port is not None:
                consumed.append((match.start(), match.end()))

    remaining = _mask(text, consumed)
    for match in IPV4_PATTERN.finditer(remaining):
        host = _actionable_ip(match.group(0))
        if host is None or "." not in host:
            continue
        found.append(_candidate(host, "ipv4", None, block))
        consumed.append((match.start(), match.end()))
    remaining = _mask(text, consumed)

    for match in IPV6_PATTERN.finditer(remaining):
        host = _actionable_ip(match.group(0))
        if host is None:
            continue
        if ":" not in host:
            continue
        found.append(_candidate(host, "ipv6", None, block))
        consumed.append((match.start(), match.end()))
    remaining = _mask(text, consumed)

    for match in FILENAME_PATTERN.finditer(remaining):
        found.append(_candidate(match.group(0), "filename", None, block))
        consumed.append((match.start(), match.end()))
    remaining = _mask(text, consumed)
    for match in DOMAIN_PATTERN.finditer(remaining):
        domain = match.group(0).casefold().rstrip(".")
        if _valid_domain(domain):
            found.append(_candidate(domain, "domain", None, block))
    return found


def extract_indicators(
    document: str,
    content_type: str = "text/html",
) -> list[IndicatorCandidate]:
    if not document:
        return []
    if "html" in content_type.casefold():
        parser = _VisibleBlocks()
        parser.feed(document)
        blocks = _restrict_to_structured(parser.blocks)
    else:
        blocks = [
            _Block(line.strip(), "document_scan")
            for line in document.splitlines()
            if line.strip()
        ]
    selected: dict[tuple[str, str, int | None], IndicatorCandidate] = {}
    for index, block in enumerate(blocks):
        nearby = " ".join(
            value.text for value in blocks[max(0, index - 2) : index + 2]
        )[:1500]
        for item in _extract_block(block):
            item = replace(item, context=nearby)
            key = (item.type, item.content.casefold(), item.port)
            previous = selected.get(key)
            if previous is None or METHOD_STRENGTH[item.extraction_method] > METHOD_STRENGTH[previous.extraction_method]:
                selected[key] = item
    return list(selected.values())
