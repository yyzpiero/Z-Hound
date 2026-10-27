"""Versioned query gates. Weak evidence remains visible, never a verdict."""
import re
from .engine import asset_id

BROAD_TOTAL = 10_000
BROAD_STREAK = 2
# Observed on 2,697,840 services in the 2026-10-27 recorded trial.
# This is a common-feature label, not an allowlist or maliciousness label.
COMMON_JARMS = {'15d3fd16d29d29d00042d43d00000071784fa9f8305ba9220d0a7894b6ff2c'}
PARKING = re.compile(r'\b(?:parked domain|domain (?:is )?parked|domain (?:is )?for sale|buy this domain|sedo domain parking)\b', re.I)
DEFAULT_PAGE = re.compile(r'^(?:301 Moved Permanently|302 (?:Found|Moved Temporarily)|400 Bad Request|401 Unauthorized|403 Forbidden|404 Not Found|500 Internal Server Error|502 Bad Gateway|503 Service Unavailable|welcome to (?:nginx|apache)|default (?:web |web site |website )?page|index of /)(?:\b|$)', re.I)
COMMON_SERVER = re.compile(r'^(?:nginx|apache|openresty|litespeed|hcdn|cloudflare|cloudfront|microsoft-iis|caddy|envoy)(?:\b|/)', re.I)
SHARED_SERVER = re.compile(r'^(?:hcdn|cloudflare|cloudfront)(?:\b|/)', re.I)


def cdn_error(asset):
    """Exact vendor templates, or generic error titles with vendor context."""
    title = str(asset.get('title') or '').strip()
    server = str(asset.get('server') or '').strip()
    headers = str(asset.get('header') or '')[:16384]
    if re.fullmatch(r'Attention Required!?\s*\|\s*Cloudflare', title, re.I):
        return True
    cloudflare = bool(re.match(r'^cloudflare(?:\b|/)', server, re.I) or re.search(r'^server:\s*cloudflare\b', headers, re.I | re.M))
    akamai = bool(re.match(r'^akamaighost(?:\b|/)', server, re.I) or re.search(r'^server:\s*akamaighost\b', headers, re.I | re.M))
    return bool((cloudflare and re.fullmatch(r'Just a moment(?:\.{3}|…)?|Attention Required!?|Access denied|Error (?:10\d{2}|52[0-7])', title, re.I))
                or (akamai and re.fullmatch(r'Invalid URL|Access Denied|Reference #[0-9a-z.]+', title, re.I)))


def classify_features(features, assets, *, version='2'):
    by_id = {asset_id(a): a for a in assets}
    result = []
    for feature in features:
        f = dict(feature)
        field, value = f['field'], f['value'].strip()
        evidence = [by_id[i] for i in f['evidence'] if i in by_id]
        reasons = []
        if f['generic'] or field in {'port', 'protocol'}:
            reasons.append('generic_service')
        if field == 'title' and (PARKING.search(value) or DEFAULT_PAGE.search(value)):
            reasons.append('parking_or_default_page')
        server = value if field == 'server' else value.partition(':')[2].strip() if field == 'header' and value.partition(':')[0].lower() == 'server' else ''
        if server and COMMON_SERVER.search(server):
            reasons.append('common_hosting_server')
        if field == 'header' and value.lower().startswith('x-powered-by:') and re.match(r'^(?:php|asp\.net|express)(?:\b|/)', value.partition(':')[2].strip(), re.I):
            reasons.append('common_framework')
        if evidence and all(PARKING.search(str(a.get('title', ''))) for a in evidence):
            reasons.append('only_parking_observations')
        if field == 'jarm':
            if value in COMMON_JARMS or set(value) == {'0'}:
                reasons.append('common_jarm')
            if evidence and all(PARKING.search(str(a.get('title', ''))) or SHARED_SERVER.search(str(a.get('server', ''))) for a in evidence):
                reasons.append('shared_hosting_jarm')
        if version == '3':
            from .certificates import COMMON_CN
            if server and re.match(r'^akamaighost(?:\b|/)', server, re.I):
                reasons.append('common_hosting_server')
            if evidence and all(cdn_error(a) for a in evidence):
                reasons.append('only_cdn_error_observations')
            if field == 'jarm' and evidence and all(SHARED_SERVER.search(str(a.get('server', ''))) or re.match(r'^akamaighost(?:\b|/)', str(a.get('server', '')), re.I) or cdn_error(a) for a in evidence):
                reasons.append('cdn_or_shared_hosting_jarm')
            if field == 'cert.subject.cn' and COMMON_CN.fullmatch(value):
                reasons.append('common_certificate_name')
        f.update(generic=bool(reasons), filter_reasons=reasons)
        result.append(f)
    return result


def filter_rules(rules, features):
    known = {f['id']: f for f in features}
    accepted, skipped = [], []
    for rule in rules:
        group = [known[i] for i in rule['feature_ids']]
        reason = None
        if all(f['generic'] for f in group):
            reason = 'weak_features_only'
        # Server and its raw header encode the same observation, not corroboration.
        if len(group) == 2 and {f['field'] for f in group} == {'server', 'header'}:
            server = next(f['value'] for f in group if f['field'] == 'server')
            header = next(f['value'] for f in group if f['field'] == 'header')
            if header.partition(':')[0].strip().lower() == 'server' and header.partition(':')[2].strip().lower() == server.strip().lower():
                reason = 'duplicate_server_evidence'
        if not reason and len(accepted) >= 64:
            reason = 'rule_limit'
        if reason:
            skipped.append({**rule, 'reason': reason})
        else:
            accepted.append(rule)
    return accepted, skipped


def audit():
    return {'broad_total': BROAD_TOTAL, 'broad_streak_limit': BROAD_STREAK,
            'skipped': [], 'rejected': [], 'offline_missing': [], 'stop_reason': None}
