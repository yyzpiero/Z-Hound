"""Bounded observation comparisons, derived solely from recorded query evidence."""
from .certificates import COMMON_CN, CONTEXT_FIELDS, certificate_fields
from .policy import cdn_error
from .engine import FEATURE_FIELDS, GENERIC, asset_id, seed_query

HEADER_FIELDS=('server','x-powered-by','x-generator')
TASK_WIDE_ASSESSMENT_ROWS=2000


def observations(asset, *, structured_certificates=False):
    values={field:str(asset.get(field) or '').strip() or None for field in FEATURE_FIELDS}
    if structured_certificates:
        values.pop('cert')
        values.update(certificate_fields(asset))
    headers={}
    for line in str(asset.get('header') or '').splitlines()[:100]:
        key,separator,value=line.partition(':')
        key=key.strip().lower()
        if separator and key in HEADER_FIELDS:
            headers.setdefault(key,[]).append(value.strip())
    for key in HEADER_FIELDS:
        values['header:'+key]='\n'.join(headers.get(key,[])) or None
    return values


def prepare_evidence(seeds, queries, features=(), *, structured_certificates=False):
    """Index recorded retrievals once and retain only enough seed rows for the cap."""
    records={q['query']:q for q in queries}
    retrievals={}
    for record in queries:
        if record.get('purpose')=='expansion' and not record.get('error'):
            for asset in (record.get('result') or {}).get('assets',[]):
                ident=asset_id(asset)
                retrievals.setdefault(ident,record.get('retrieved_at'))
                retrievals.setdefault((record['query'],ident),record.get('retrieved_at'))
    rows=[]
    classified={}
    for f in features:
        field,value=f['field'],f['value']
        if field=='header':
            key,_,value=value.partition(':');field='header:'+key.strip().lower();value=value.strip()
        classified[field,value]=f['generic']
    for seed in seeds:
        record=records.get(seed_query(seed))
        if not record or record.get('error'):
            continue
        seen=set()
        for asset in (record.get('result') or {}).get('assets',[]):
            ident=asset_id(asset)
            if ident in seen: continue
            seen.add(ident)
            error_observation = structured_certificates and cdn_error(asset)
            for field,value in observations(asset,structured_certificates=structured_certificates).items():
                context_only = error_observation or structured_certificates and (field in CONTEXT_FIELDS or (field == 'cert.subject.cn' and value and COMMON_CN.fullmatch(value)))
                rows.append({'seed_id':seed['id'],'seed_content':seed['content'],'seed_asset_id':ident,
                             'field':field,'seed_value':value,'generic':bool(context_only) or classified.get((field,value),bool(value and value.lower() in GENERIC))})
                if len(rows)>200:
                    return rows,retrievals
    return rows,retrievals


def build_assessment(seeds, queries, candidate, *, prepared=None, features=(), promoting_time=False, structured_certificates=False):
    """Counts describe displayed rows; truncation explicitly marks partial output.

    V2 uses the response that supplied the promoted observation; legacy replay
    retains first-response timing. Neither is the source's observation time.
    """
    rows,retrievals=prepared if prepared is not None else prepare_evidence(seeds,queries,features,structured_certificates=structured_certificates)
    ident=asset_id(candidate['asset'])
    retrieved_at=retrievals.get(ident)
    if promoting_time:
        reasons=candidate.get('reasons',[])
        retrieved_at=retrievals.get((reasons[0]['query'],ident)) if reasons else None
    counts={'match':0,'different':0,'missing':0}
    result={'rows':[],'counts':counts,'truncated':len(rows)>200,'deferred':False,'source_observed_at':None}
    candidate_values=observations(candidate['asset'],structured_certificates=structured_certificates)
    for row in rows[:200]:
        value=row['seed_value']
        other=candidate_values[row['field']]
        relation='missing' if value is None or other is None else 'match' if value==other else 'different'
        counts[relation]+=1
        result['rows'].append({**row,'candidate_value':other,'relation':relation,'retrieved_at':retrieved_at})
    return result


def build_assessments(seeds, queries, candidates, features=(), *, promoting_time=False, structured_certificates=False):
    """Bound task-wide preview allocation; deferred candidates have a lazy endpoint."""
    prepared=prepare_evidence(seeds,queries,features,structured_certificates=structured_certificates)
    rows_per_candidate=min(len(prepared[0]),200)
    remaining=TASK_WIDE_ASSESSMENT_ROWS
    assessments=[]
    for candidate in candidates:
        if rows_per_candidate>remaining:
            assessments.append({'rows':[],'counts':{'match':0,'different':0,'missing':0},
                                'truncated':True,'deferred':True,'source_observed_at':None})
            continue
        result=build_assessment(seeds,queries,candidate,prepared=prepared,promoting_time=promoting_time,structured_certificates=structured_certificates)
        remaining-=len(result['rows'])
        assessments.append(result)
    return assessments
