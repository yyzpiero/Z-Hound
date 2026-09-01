from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

from ..models import IndicatorCandidate, ReportCandidate


@dataclass(frozen=True)
class EnrichmentResult:
    threat_actor: str = ""
    threat_actor_source_name: str = ""
    malware_family: str = ""
    indicator_role: str = "unknown"
    campaign: str = ""
    confidence: str = "low"
    evidence: str = ""
    contextual_threat_entity: bool = False


class AliasCatalog:
    def __init__(self, aliases: dict[str, tuple[str, str]]):
        self.aliases = aliases

    @classmethod
    def from_csv(cls, path: Path) -> "AliasCatalog":
        aliases: dict[str, tuple[str, str]] = {}
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                canonical = (row.get("canonical_name") or "").strip()
                source = (row.get("source_name") or "").strip()
                if not canonical or not source:
                    continue
                key = source.casefold()
                previous = aliases.get(key)
                if previous and previous[0] != canonical:
                    raise ValueError(f"actor alias maps to multiple names: {source}")
                aliases[key] = (canonical, source)
        return cls(aliases)

    def find(self, text: str) -> tuple[str, str]:
        folded = text.casefold()
        for alias in sorted(self.aliases, key=len, reverse=True):
            if alias in folded:
                return self.aliases[alias]
        apt = re.search(r"(?i)\bAPT[- ]?\d{1,4}\b", text)
        if apt:
            name = apt.group(0).upper().replace(" ", "-")
            return name, apt.group(0)
        return "", ""


ROLE_PATTERNS = (
    ("c2", re.compile(r"(?i)(?<![A-Za-z0-9])C2(?![A-Za-z0-9])|C&C|command[- ]and[- ]control|命令与控制|控制服务器|回连地址")),
    ("phishing", re.compile(r"(?i)phish(?:ing)?|钓鱼")),
    ("malware_distribution", re.compile(r"(?i)malware distribution|download server|分发|下载服务器")),
    ("payload", re.compile(r"(?i)payload|恶意载荷|恶意样本|样本下载")),
    ("exfiltration", re.compile(r"(?i)exfiltrat|data theft|数据外传|数据窃取")),
)


def _unmapped_actor(text: str) -> str:
    quoted = re.search(
        r"(?:归因于|关联到|来自|由)\s*[“\"《]\s*"
        r"([\u4e00-\u9fffA-Za-z0-9_-]{2,24})\s*[”\"》]\s*(组织|集团|小组)",
        text,
    )
    if quoted:
        return quoted.group(1) + quoted.group(2)
    chinese = re.search(
        r"(?:归因于|关联到|来自|由)\s*"
        r"([\u4e00-\u9fffA-Za-z0-9_-]{2,24}(?:组织|集团|小组))",
        text,
    )
    if chinese:
        value = chinese.group(1)
        if value not in {
            "该组织",
            "某组织",
            "威胁组织",
            "该威胁组织",
            "攻击组织",
            "黑客组织",
        }:
            return value
    english = re.search(
        r"(?i)(?:attributed to|associated with)\s+([A-Z][A-Za-z0-9 _-]{1,30}(?:Group|Team|Actor))\b",
        text,
    )
    if not english:
        return ""
    value = english.group(1).strip()
    if value.casefold() in {"the threat actor", "a threat actor", "an threat actor"}:
        return ""
    return value


def _named_value(text: str, patterns: tuple[re.Pattern, ...]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip(" \t\"'“”《》:：,.。")
    return ""


MALWARE_PATTERNS = (
    re.compile(r"(?i)malware family\s+(?:named\s+|called\s+)?([A-Za-z][A-Za-z0-9._-]{1,39})"),
    re.compile(r"(?i)(?:ransomware|backdoor|trojan)\s+(?:named|called)\s+([A-Za-z][A-Za-z0-9._-]{1,39})"),
    re.compile(r"\b([A-Z][A-Za-z0-9._-]{1,39})\s+(?i:ransomware|backdoor|trojan)\b"),
    re.compile(r"(?:恶意软件|木马|勒索软件|后门)(?:家族)?[：:\s]*(?:名为|称为)?[“\"《]([^”\"》]{2,39})[”\"》]"),
    re.compile(r"(?:恶意软件|木马|勒索软件|后门)(?:家族)?[：:\s]+([A-Za-z][A-Za-z0-9._-]{1,39})"),
)
CAMPAIGN_PATTERNS = (
    re.compile(r"(?i)(?:campaign|operation)\s+(?:named\s+|called\s+)?([A-Za-z][A-Za-z0-9 _-]{1,39})"),
    re.compile(r"(?:攻击活动|行动)[：:\s“\"《]*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9 _-]{1,39})"),
)


def enrich_indicator(
    candidate: IndicatorCandidate,
    report: ReportCandidate,
    aliases: AliasCatalog,
) -> EnrichmentResult:
    context = candidate.context or candidate.evidence
    actor, source_actor = aliases.find(context)
    actor_from_title = False
    if not actor:
        actor = source_actor = _unmapped_actor(context)
    contextual_actor = bool(actor)
    if not actor:
        actor, source_actor = aliases.find(report.title)
        actor_from_title = bool(actor)
    if not actor:
        actor = source_actor = _unmapped_actor(report.title)
        actor_from_title = bool(actor)
    context_malware = _named_value(context, MALWARE_PATTERNS)
    context_campaign = _named_value(context, CAMPAIGN_PATTERNS)
    malware = context_malware or _named_value(report.title, MALWARE_PATTERNS)
    campaign = context_campaign or _named_value(report.title, CAMPAIGN_PATTERNS)
    role = "unknown"
    for label, pattern in ROLE_PATTERNS:
        if pattern.search(context):
            role = label
            break
    if candidate.extraction_method == "table":
        confidence = "high"
    elif candidate.extraction_method == "ioc_section":
        confidence = "medium"
    elif candidate.extraction_method == "document_scan":
        confidence = "low"
    elif actor_from_title or (not context and report.title):
        confidence = "low"
    else:
        confidence = "medium"
    evidence = (report.title if actor_from_title else context)[:500]
    return EnrichmentResult(
        threat_actor=actor,
        threat_actor_source_name=source_actor,
        malware_family=malware,
        indicator_role=role,
        campaign=campaign,
        confidence=confidence,
        evidence=evidence,
        contextual_threat_entity=bool(
            contextual_actor or context_malware or context_campaign
        ),
    )
