"""Reserved synthetic assets, generated without catalog or credentials."""
from .engine import extract_features, generate_rules, seed_query
from .providers import validate_result


def snapshot():
    seeds = [{'id':'demo-seed','content':'192.0.2.10','type':'ipv4','report_url':'https://example.test/synthetic-report','report_title':'Synthetic demonstration','threat_actor':'','indicator_role':'c2','published_at':'','evidence':'Synthetic example; no attribution claim'}]
    baseline = {'ip':'192.0.2.10','port':'443','protocol':'https','host':'https://seed.example.test','domain':'seed.example.test','title':'Synthetic control panel','server':'demo-server','jarm':'synthetic-jarm'}
    neighbor = dict(baseline, ip='198.51.100.20', domain='candidate.example.test', host='https://candidate.example.test')
    records = [{'query':seed_query(seeds[0]),'result':validate_result({'total':1,'assets':[baseline]},100),'error':None}]
    features = extract_features(records[0]['result']['assets'])
    for rule in generate_rules(features):
        records.append({'query':rule['query'],'result':validate_result({'total':2,'assets':[baseline,neighbor]},100),'error':None})
    return {'version':1,'engine_version':'1','synthetic':True,'seeds':seeds,'queries':records,'llm_selections':[], 'query_budget':12,'max_results':100}
