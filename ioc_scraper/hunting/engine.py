"""Deterministic evidence extraction and bounded query construction."""
import hashlib
import ipaddress
import json
from itertools import combinations
from urllib.parse import urlsplit

ENGINE_VERSION = '3'
SUPPORTED_ENGINES = ('1', '2', '3')
FEATURE_FIELDS = ('jarm', 'cert', 'title', 'server', 'protocol', 'port')
GENERIC = {'nginx', 'apache', 'http', 'https', '80', '443', 'welcome', 'default'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def asset_id(asset):
    """Service identity ignores mutable banners and preserves distinct ports."""
    host = str(asset.get('ip') or asset.get('host') or asset.get('domain') or '').lower().rstrip('.')
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if '://' in host:
            host = urlsplit(host).hostname or host
    virtual_host = str(asset.get('host') or asset.get('domain') or '').lower().strip()
    if virtual_host:
        virtual_host = (urlsplit(virtual_host if '://' in virtual_host else '//' + virtual_host).hostname or virtual_host).rstrip('.')
    port = str(asset.get('port', ''))
    if port.isdigit():
        port = str(int(port))
    return digest([host, port, str(asset.get('protocol', '')).lower(), virtual_host])


def compare_assets(seeds, results):
    baseline = {asset_id(a) for a in seeds}
    retrieved = {asset_id(a) for a in results}
    return {'coverage': len(baseline & retrieved) / len(baseline) if baseline else 0.0,
            'new_count': len(retrieved - baseline)}


def quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def seed_query(seed):
    kind, content = seed['type'].lower(), seed['content']
    port = normalize_port(seed.get('port',''))
    if kind == 'url':
        parsed = urlsplit(content)
        host = parsed.hostname
        if parsed.scheme not in {'http','https'} or not host:
            raise ValueError('invalid network seed URL')
        effective = normalize_port(str(parsed.port)) if parsed.port is not None else str(443 if parsed.scheme == 'https' else 80)
        if port and port != effective:
            raise ValueError('seed port conflicts with URL service port')
        port = effective
        authority = f'[{host}]' if ':' in host else host
        if parsed.port is not None:
            authority += ':' + str(parsed.port)
        content = f'{parsed.scheme}://{authority}'
        field = 'host'
    else:
        field = 'ip' if kind in {'ip','ipv4','ipv6'} else 'domain'
    query = f'{field}={quote(content)}'
    return query + (f' && port={quote(port)}' if port else '')


def normalize_port(value):
    if value == '':
        return ''
    if not isinstance(value,(str,int)) or isinstance(value,bool) or not str(value).isascii() or not str(value).isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError('seed port must be an integer from 1 to 65535')
    return str(int(value))


def extract_features(assets, *, with_policy=False, engine_version='2'):
    found = {}
    for asset in assets:
        observations = [(field,str(asset.get(field) or '').strip()) for field in FEATURE_FIELDS]
        if engine_version == '3':
            from .certificates import certificate_fields
            observations = [(f, v) for f, v in observations if f != 'cert']
            observations.append(('cert.subject.cn', certificate_fields(asset)['cert.subject.cn'] or ''))
        # Stable observed header lines only: dates/cookies/length are volatile.
        for line in str(asset.get('header') or '').splitlines()[:100]:
            if line.partition(':')[0].strip().lower() in {'server','x-powered-by','x-generator'}:
                observations.append(('header',line.strip()))
        for field, value in observations:
            if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
                continue
            key = (field, value)
            feature = found.setdefault(key, {'id': 'f-' + digest(key), 'field': field, 'value': value, 'evidence': [], 'generic': value.lower() in GENERIC})
            ident = asset_id(asset)
            if ident not in feature['evidence']:
                feature['evidence'].append(ident)
    features = list(found.values())
    if with_policy:
        from .policy import classify_features
        features = classify_features(features, assets, version=engine_version)
    return sorted(features, key=lambda f: (f['generic'], -len(f['evidence']), f['field'], f['value']))[:24]


def generate_rules(features, limit=64):
    """Singles plus pairs, bounded before enumeration (no power set)."""
    features = features[:24]
    singles = [(f,) for f in features]
    # At most 276 pairs, and only jointly observed features form a query.
    pairs = [(a,b) for a,b in combinations(features,2)
             if a['field'] != b['field'] and set(a['evidence']) & set(b['evidence'])]
    pairs.sort(key=lambda pair: (-len(set(pair[0]['evidence']) & set(pair[1]['evidence'])),
                                 sum(f['generic'] for f in pair)))
    groups = []
    for index in range(max(len(singles),len(pairs))):
        if index < len(singles):
            groups.append(singles[index])
        if index < len(pairs):
            groups.append(pairs[index])
    result = []
    for group in groups[:limit]:
        if len({f['field'] for f in group}) != len(group):
            continue
        result.append({'query': ' && '.join(f'{f["field"]}={quote(f["value"])}' for f in group),
                       'feature_ids': [f['id'] for f in group], 'generic_penalty': sum(f['generic'] for f in group)})
    return result
