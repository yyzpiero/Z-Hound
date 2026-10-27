"""Independent local seed validation; qualification is an analyst decision."""
import csv
import io
import ipaddress
import json
import re
from urllib.parse import urlsplit, urlunsplit
from .engine import digest, normalize_port
from .providers import scrub

FIELDS = {'content','type','port','source','evidence','report_url','report_title','threat_actor','indicator_role','published_at'}
QUALIFICATION = {'status','value','reviewer','rationale','reviewed_at','revision'}
STATUSES = {'pending','confirmed','rejected'}
VALUES = {'unrated','low','medium','high'}


def safe_text(value, limit=16384):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
        raise ValueError('invalid seed text')
    if re.search(r'(?i)https?://[^\s/?#]*@',value):
        raise ValueError('URL credentials are not allowed')
    if re.search(r'(?i)(?:api[_-]?key|password|access[_-]?token|authorization|(?:[?&]|\b)token)\s*[=:]|\bbearer\s+\S+|\bsk-[a-zA-Z0-9_-]{12,}',value):
        raise ValueError('credential-shaped input is not allowed')
    return scrub(value).strip()


def network_host(host):
    if not host:
        raise ValueError('invalid network host')
    host=host.lower().rstrip('.')
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if re.fullmatch(r'[\d.]+',host) or len(host)>253 or '.' not in host or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p) for p in host.split('.')):
            raise ValueError('invalid DNS or IP host') from None
        return host


def normalize_record(record):
    if not isinstance(record,dict) or set(record)-FIELDS or 'content' not in record:
        raise ValueError('unknown or missing seed import fields')
    clean={key:safe_text(record.get(key,'')) for key in FIELDS-{'port'}}
    content=clean['content']
    if not content or len(content)>2048 or any(c.isspace() for c in content):
        raise ValueError('invalid seed content')
    port=normalize_port(record.get('port',''))
    kind=clean['type'].lower()
    if not kind:
        try:
            ipaddress.ip_address(content); kind='ip'
        except ValueError:
            kind='url' if '://' in content else 'domain'
    if kind in {'ip','ipv4','ipv6'}:
        address=ipaddress.ip_address(content)
        if kind=='ipv4' and address.version!=4 or kind=='ipv6' and address.version!=6:
            raise ValueError('seed IP version differs from type')
        content=str(address); kind='ipv4' if address.version==4 else 'ipv6'
    elif kind=='domain':
        content=network_host(content)
        try:
            ipaddress.ip_address(content)
        except ValueError:
            pass
        else:
            raise ValueError('domain requires a DNS name')
    elif kind=='url':
        parsed=urlsplit(content)
        if parsed.scheme.lower() not in {'http','https'} or parsed.username is not None or parsed.password is not None or '\\' in content:
            raise ValueError('invalid seed URL')
        host=network_host(parsed.hostname)
        effective=normalize_port(parsed.port) if parsed.port is not None else str(443 if parsed.scheme.lower()=='https' else 80)
        if port and port!=effective:
            raise ValueError('seed port conflicts with URL service port')
        authority=f'[{host}]' if ':' in host else host
        if effective!=str(443 if parsed.scheme.lower()=='https' else 80):
            authority+=':'+effective
        content=urlunsplit((parsed.scheme.lower(),authority,parsed.path or '/',parsed.query,''))
        port=effective
    else:
        raise ValueError('network seeds only')
    clean.update(content=content,type=kind,port=port)
    clean['id']='seed-'+digest([kind,content,port])
    return clean


def parse_import(payload):
    if not isinstance(payload,dict):
        raise ValueError('import must be an object')
    if 'items' in payload:
        if set(payload)!={'items'} or not isinstance(payload['items'],list):
            raise ValueError('invalid seed items')
        records=payload['items']
        if len(json.dumps(records,ensure_ascii=False).encode('utf-8'))>2*1024*1024:
            raise ValueError('seed text exceeds 2 MiB')
    else:
        if set(payload)-{'format','text','source','evidence'} or payload.get('format') not in {'csv','text'}:
            raise ValueError('invalid import format')
        text=payload.get('text')
        if not isinstance(text,str) or len(text.encode('utf-8'))>2*1024*1024:
            raise ValueError('seed text exceeds 2 MiB')
        defaults={key:safe_text(payload.get(key,'')) for key in ('source','evidence')}
        if payload['format']=='csv':
            try:
                reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')),strict=True)
                if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or set(reader.fieldnames)-FIELDS or 'content' not in reader.fieldnames:
                    raise ValueError('invalid CSV columns')
                records=[]
                for row in reader:
                    records.append({**defaults,**row})
                    if len(records)>500: raise ValueError('at most 500 seeds per import')
            except csv.Error:
                raise ValueError('invalid seed CSV') from None
        else:
            records=[{'content':line.strip(),**defaults} for line in text.splitlines() if line.strip()]
    if not 1<=len(records)<=500:
        raise ValueError('import requires 1–500 seeds')
    return [normalize_record(row) for row in records]


def validate_review(payload):
    if not isinstance(payload,dict) or set(payload)!={'revision','status','value','reviewer','rationale','source','evidence'}:
        raise ValueError('invalid seed review fields')
    if type(payload['revision']) is not int or payload['revision']<0 or not isinstance(payload['status'],str) or payload['status'] not in STATUSES or not isinstance(payload['value'],str) or payload['value'] not in VALUES:
        raise ValueError('invalid seed review status, value or revision')
    clean={**payload,**{key:safe_text(payload[key]) for key in ('reviewer','rationale','source','evidence')}}
    if not clean['reviewer'] or not clean['rationale']:
        raise ValueError('reviewer and rationale are required')
    if clean['status']=='confirmed' and (not clean['source'] or not clean['evidence']):
        raise ValueError('confirmation requires source and evidence')
    return clean


def eligible(seed):
    q=seed.get('qualification',{})
    return q.get('status')=='confirmed' and q.get('value')=='high' and all(isinstance(v,str) and v.strip() for v in (seed.get('source'),seed.get('evidence'),q.get('reviewer'),q.get('rationale')))


def validate_qualified_snapshot(seed):
    q=seed.get('qualification')
    if not isinstance(q,dict) or set(q)!=QUALIFICATION or not isinstance(q['status'],str) or q['status'] not in STATUSES or not isinstance(q['value'],str) or q['value'] not in VALUES or type(q['revision']) is not int or q['revision']<0:
        raise ValueError('invalid snapshot qualification')
    for key in ('reviewer','rationale'):
        safe_text(q[key])
    if q['reviewed_at'] is not None:
        from datetime import datetime
        if not isinstance(q['reviewed_at'],str) or len(q['reviewed_at'])>100 or datetime.fromisoformat(q['reviewed_at'].replace('Z','+00:00')).tzinfo is None:
            raise ValueError('invalid qualification time')
    if q['status']=='confirmed' and (not eligible({**seed,'qualification':{**q,'value':'high'}}) or q['reviewed_at'] is None or q['revision']<1):
        raise ValueError('invalid confirmed seed provenance')


def seed_snapshot(seed):
    """Copy only task evidence, scrubbing nested analyst labels as well."""
    result={k:scrub(v) for k,v in seed.items() if k not in {'qualification','eligible','history'}}
    if 'qualification' in seed:
        result['qualification']={k:scrub(v) if isinstance(v,str) else v for k,v in seed['qualification'].items()}
    return result
