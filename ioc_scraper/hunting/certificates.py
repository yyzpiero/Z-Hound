"""Conservative extraction from FOFA's rendered certificate text, not DER parsing.

Unrecognized formats stay unknown. Conflicting values never become
query material. Issuers, serials and validity are context, not pivot features.
"""
import re

CERTIFICATE_FIELDS = ('cert.subject.cn', 'cert.subject.org', 'cert.issuer.cn',
                      'cert.issuer.org', 'cert.serial', 'cert.not_before', 'cert.not_after')
CONTEXT_FIELDS = set(CERTIFICATE_FIELDS) - {'cert.subject.cn'}
COMMON_CN = re.compile(r'^(?:localhost(?:\.localdomain)?|127\.0\.0\.1|::1|\*|'
                       r'cloudflare origin certificate|caddy local authority(?:.*)?|'
                       r'kubernetes ingress controller fake certificate|'
                       r'traefik default cert|default certificate|snakeoil)$', re.I)


def scalar(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    value = value.strip()
    return value if value and len(value) <= 256 else None


def certificate_fields(asset):
    """Return a fixed field map; None means absent, unsupported or conflicting."""
    collected = {}
    text = asset.get('cert') or ''
    if isinstance(text, str) and len(text) <= 16384:
        section = None
        for line in text.splitlines():
            if not line.strip():
                continue
            key, separator, value = line.strip().partition(':')
            key = key.strip()
            if not line[0].isspace():
                section = key if separator and not value.strip() and key in {'Subject', 'Issuer', 'Validity'} else None
                field = 'cert.serial' if key == 'Serial Number' and separator else None
            else:
                field = None
                if section in {'Subject', 'Issuer'} and key in {'CommonName', 'Organization'}:
                    field = 'cert.' + section.lower() + ('.cn' if key == 'CommonName' else '.org')
                elif section == 'Validity' and key in {'Not Before', 'Not After'}:
                    field = 'cert.not_before' if key == 'Not Before' else 'cert.not_after'
                if not field and separator and not value.strip():
                    # An unsupported nested section must not inherit Subject or
                    # Issuer context and masquerade as one of their fields.
                    section = None
            if field and separator:
                collected.setdefault(field, set()).add(scalar(value))
    for field, source in [('cert.subject.cn', 'certs_subject_cn'), ('cert.subject.org', 'certs_subject_org')]:
        value = asset.get(source)
        if value:
            collected.setdefault(field, set()).add(scalar(value))
    return {field: next(iter(values)) if len(values := collected.get(field, set())) == 1 else None
            for field in CERTIFICATE_FIELDS}
