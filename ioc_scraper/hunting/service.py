"""Hunt orchestration with bounded calls and isolated background lifecycle."""
import csv
import ipaddress
import json
import re
import threading
import uuid
import weakref
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from .engine import ENGINE_VERSION, SUPPORTED_ENGINES, asset_id, compare_assets, extract_features, generate_rules, seed_query, normalize_port
from .providers import FofaProvider, ReplayProvider, ProviderError, configuration, select_features, validate_selection, validate_result, scrub
from .store import Store, Conflict, ACTIVE, now
from . import policy

_PROCESS_LOCK = threading.Lock()
_MANAGERS = weakref.WeakValueDictionary()


class ReplayBoundary(Exception):
    """All available queries of an interrupted execution were replayed."""


def read_seeds(root):
    path = Path(root) / 'indicators.csv'
    if not path.is_file():
        return []
    keys = ('id','content','type','port','report_url','report_title','threat_actor','indicator_role','published_at','evidence')
    with path.open(encoding='utf-8-sig', newline='') as handle:
        seeds = []
        for row in csv.DictReader(handle):
            if row.get('validation_status') != 'accepted':
                continue
            seed = {key:scrub(row.get(key) or '') for key in keys}
            try:
                validate_seed(seed)
                seed['port'] = normalize_port(seed['port'])
            except ValueError:
                continue
            seeds.append(seed)
        return seeds


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError('request must be an object')
    allowed = {'name','mode','seed_ids','use_llm','query_budget','max_results','snapshot','context'}
    if set(payload) - allowed:
        raise ValueError('unknown request fields')
    if not isinstance(payload.get('name'), str) or not 1 <= len(payload['name'].strip()) <= 200:
        raise ValueError('name must contain 1–200 characters')
    if payload.get('mode') not in {'fofa','replay'}:
        raise ValueError('mode must be fofa or replay')
    ids = payload.get('seed_ids')
    minimum = 0 if payload.get('mode') == 'replay' and payload.get('snapshot') is not None else 1
    if not isinstance(ids, list) or not minimum <= len(ids) <= 20 or any(not isinstance(i,str) or not 1 <= len(i) <= 200 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('select 1–20 distinct qualified network seeds')
    for key, default, maximum in (('query_budget',12,64),('max_results',100,1000)):
        value = payload.get(key,default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{key} must be an integer from 1 to {maximum}')
    if type(payload.get('use_llm',False)) is not bool:
        raise ValueError('use_llm must be boolean')
    if payload['mode'] == 'replay' and payload.get('use_llm',False):
        raise ValueError('replay cannot request live LLM assistance')
    if not isinstance(payload.get('context',''),str) or len(payload.get('context','')) > 4000:
        raise ValueError('context must contain at most 4000 characters')


def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict) or type(snapshot.get('version')) is not int or snapshot.get('version') != 1 or snapshot.get('engine_version') not in SUPPORTED_ENGINES:
        raise ValueError('unsupported replay snapshot version')
    reject_credentials(snapshot)
    allowed = {'version','engine_version','id','name','state','mode','created_at','synthetic','seeds','context','features','rules','candidates','queries','warnings','metrics','error','query_budget','max_results','use_llm','llm_selections','seed_assets','feedback','replay_source_state','policy','evaluation'}
    if set(snapshot) - allowed:
        raise ValueError('unknown replay snapshot fields')
    if 'policy' in snapshot:
        audit = snapshot['policy']
        if not isinstance(audit,dict) or not isinstance(audit.get('offline_missing',[]),list):
            raise ValueError('invalid replay policy audit')
        missing = audit.get('offline_missing',[])
        if len(missing)>320 or any(not isinstance(r,dict) or not isinstance(r.get('query'),str) or len(r['query'])>4096 for r in missing):
            raise ValueError('invalid offline query boundary')
    if 'evaluation' in snapshot:
        evaluation = snapshot['evaluation']
        if (snapshot['engine_version'] not in ('2', '3') or not isinstance(evaluation,dict)
                or set(evaluation) != {'source_id','source_engine_version'}
                or not isinstance(evaluation['source_id'],str) or len(evaluation['source_id']) > 200
                or evaluation['source_engine_version'] not in SUPPORTED_ENGINES):
            raise ValueError('invalid offline evaluation provenance')
    for key, choices in (('state',{'completed','cancelled','interrupted','failed'}),
                         ('replay_source_state',{'cancelled','interrupted'}),('mode',{'fofa','replay'})):
        if key in snapshot and (not isinstance(snapshot[key],str) or snapshot[key] not in choices):
            raise ValueError('invalid replay state or mode')
    for key in ('synthetic','use_llm'):
        if key in snapshot and type(snapshot[key]) is not bool:
            raise ValueError('invalid replay flag')
    metrics = snapshot.get('metrics',{})
    if not isinstance(metrics,dict) or set(metrics) - {'api_calls','cache_hits','queries','returned','candidates','budget_exhausted','replay_boundary'}:
        raise ValueError('invalid replay metrics')
    for key,value in metrics.items():
        if key in {'budget_exhausted','replay_boundary'}:
            valid = type(value) is bool
        else:
            valid = type(value) is int and value >= 0
        if not valid:
            raise ValueError('invalid replay metric value')
    if not isinstance(snapshot.get('context',''),str) or len(snapshot.get('context','')) > 4000:
        raise ValueError('invalid replay context')
    for key, default, maximum in (('query_budget',12,64),('max_results',100,1000)):
        value = snapshot.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('invalid replay limits')
    seeds, queries = snapshot.get('seeds'), snapshot.get('queries')
    if not isinstance(seeds,list) or not 1 <= len(seeds) <= 20 or any(not isinstance(s,dict) or not isinstance(s.get('id'),str) or not isinstance(s.get('content'),str) or not isinstance(s.get('type'),str) for s in seeds):
        raise ValueError('invalid replay seeds')
    if len({s['id'] for s in seeds}) != len(seeds):
        raise ValueError('duplicate replay seeds')
    for seed in seeds:
        validate_seed(seed)
    if not isinstance(queries,list) or len(queries) > 64 or any(not isinstance(q,dict) or not isinstance(q.get('query'),str) or len(q['query']) > 4096 for q in queries):
        raise ValueError('invalid replay queries')
    if len({q['query'] for q in queries}) != len(queries):
        raise ValueError('duplicate replay queries')
    for record in queries:
        if record.get('retrieved_at') is not None:
            stamp=record['retrieved_at']
            if not isinstance(stamp,str) or len(stamp)>100 or datetime.fromisoformat(stamp.replace('Z','+00:00')).tzinfo is None:
                raise ValueError('invalid query retrieval timestamp')
        if record.get('error') is not None and (not isinstance(record['error'],str) or len(record['error']) > 4000):
            raise ValueError('invalid replay query error')
        if not record.get('error'):
            validate_result(record.get('result'),1000)
    selections = snapshot.get('llm_selections',[])
    if not isinstance(selections,list):
        raise ValueError('invalid replay LLM decisions')
    if selections:
        seed_queries = {seed_query(s) for s in seeds}
        assets = [a for q in queries if q['query'] in seed_queries and not q.get('error') for a in q['result']['assets']]
        validate_selection({'selections':selections},extract_features(assets,with_policy=snapshot['engine_version'] in ('2', '3'),engine_version=snapshot['engine_version']))
    reviews = snapshot.get('feedback',[])
    if not isinstance(reviews,list):
        raise ValueError('invalid replay feedback')
    for review in reviews:
        if not isinstance(review,dict) or review.get('decision') not in {'related','unrelated','uncertain'}:
            raise ValueError('invalid replay decision')
        if not isinstance(review.get('candidate_id'),str) or not review['candidate_id'].strip():
            raise ValueError('replay feedback requires candidate_id')
        for key,limit in (('candidate_id',200),('attribution',500),('note',4000),('created_at',100)):
            if not isinstance(review.get(key,''),str) or len(review.get(key,'')) > limit:
                raise ValueError('invalid replay review text')
        stamp = review.get('created_at')
        if stamp:
            try:
                parsed = datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise ValueError('invalid replay feedback timestamp') from None
    return snapshot


def reject_credentials(value):
    sensitive = {'credentials','api_key','fofa_api_key','deepseek_api_key','fofa_email','authorization','password','access_token'}
    if isinstance(value,dict):
        for key, item in value.items():
            if key.lower() in sensitive:
                raise ValueError('credentials are not allowed in replay snapshots')
            reject_credentials(item)
    elif isinstance(value,list):
        for item in value:
            reject_credentials(item)


def validate_seed(seed):
    allowed = {'id','content','type','port','report_url','report_title','threat_actor','indicator_role','published_at','evidence','source','qualification'}
    if set(seed) - allowed or any(not isinstance(v,str) or len(v) > 16384 for k,v in seed.items() if k!='qualification'):
        raise ValueError('invalid seed evidence')
    if 'source' in seed or 'qualification' in seed:
        # New independent-seed provenance follows import/review text safety.
        # Legacy snapshots retain their historical free-text evidence semantics.
        from .seeds import safe_text
        for key,value in seed.items():
            if key!='qualification':
                safe_text(value)
    if 'qualification' in seed:
        from .seeds import validate_qualified_snapshot
        validate_qualified_snapshot(seed)
    content, kind = seed['content'], seed['type'].lower()
    if not seed['id'] or len(seed['id']) > 200 or not content or len(content) > 2048:
        raise ValueError('invalid seed identity')
    normalize_port(seed.get('port',''))
    if kind in {'ip','ipv4','ipv6'}:
        ipaddress.ip_address(content)
    elif kind in {'url','domain'}:
        host = urlsplit(content).hostname if kind == 'url' else content
        if kind == 'url' and urlsplit(content).scheme not in {'http','https'}:
            raise ValueError('unsupported seed URL scheme')
        if not host or len(host) > 253:
            raise ValueError('invalid network seed host')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[a-zA-Z0-9.-]+',host):
                raise ValueError('invalid network seed host') from None
        if kind == 'url':
            seed_query(seed)  # Validate URL port and any separate port agreement.
    else:
        raise ValueError('network seeds only')


class HuntManager:
    def __init__(self, root):
        self.root = Path(root)
        self.store = Store(root)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.active = None
        self.store.interrupt()

    def start(self, payload, *, trusted_snapshot=None):
        validate_payload(payload)
        mode = payload['mode']
        snap = trusted_snapshot if trusted_snapshot is not None else payload.get('snapshot')
        if mode == 'replay':
            snap = validate_snapshot(snap)
        if mode == 'fofa':
            from .seeds import eligible
            available = {s['id']:s for s in self.store.seeds() if eligible(s)}
            if any(i not in available for i in payload['seed_ids']):
                raise ValueError('seed selection must use confirmed high-value independent seeds')
            seeds = [{k:v for k,v in available[i].items() if k not in {'history','eligible'}} for i in payload['seed_ids']]
        else:
            seeds = snap['seeds']
            payload = {**payload,'query_budget':snap.get('query_budget',12),'max_results':snap.get('max_results',100),'context':snap.get('context','')}
        for seed in seeds:
            validate_seed(seed)
        from .seeds import seed_snapshot
        seeds = [seed_snapshot(seed) for seed in seeds]
        if mode == 'fofa' and not configuration()['fofa_configured']:
            raise ValueError('FOFA credentials are not configured')
        if mode == 'fofa' and snap is not None:
            raise ValueError('snapshot is only supported in replay mode')
        if mode == 'fofa' and payload.get('query_budget',12) <= len({seed_query(s) for s in seeds}):
            raise ValueError('query_budget must exceed unique seed lookups to reserve expansion capacity')
        task = {'id':uuid.uuid4().hex,'name':scrub(payload['name'].strip()),'state':'queued','mode':mode,'created_at':now(),
                'engine_version':snap['engine_version'] if mode=='replay' else ENGINE_VERSION,'synthetic':bool(snap and snap.get('synthetic')),'seeds':seeds,
                'context':scrub(payload.get('context','')),'features':[],'rules':[],'candidates':[],'queries':[],
                'seed_assets':[],'warnings':[],'metrics':{'api_calls':0,'cache_hits':0,'queries':0,'returned':0,'candidates':0,'budget_exhausted':False},
                'error':None,'query_budget':payload.get('query_budget',12),'max_results':payload.get('max_results',100),
                'use_llm':payload.get('use_llm',False),'llm_selections':[]}
        if task['engine_version'] in ('2', '3'):
            task['policy'] = policy.audit()
        if snap and 'evaluation' in snap:
            task['evaluation'] = dict(snap['evaluation'])
        if not _PROCESS_LOCK.acquire(blocking=False):
            raise Conflict('a hunting task is already active in this process')
        try:
            with self.lock:
                self.store.create(task,qualified_ids=payload['seed_ids'] if mode=='fofa' else None)
                self.cancel.clear()
                self.active = task['id']
                self.thread = threading.Thread(target=self._run, args=(task,snap), daemon=True, name='infrastructure-hunt')
                self.thread.start()
        except Exception:
            _PROCESS_LOCK.release()
            raise
        return {'id':task['id'],'state':'queued'}

    def stop(self, ident):
        with self.lock:
            task = self.store.get(ident)
            if task['state'] not in ACTIVE or ident != self.active:
                raise Conflict('hunt has already finished')
            self.cancel.set()
            task['state'] = 'stopping'
            self.store.save(task)
        return {'id':ident,'state':'stopping'}

    def _save(self, task):
        with self.lock:
            if self.cancel.is_set() and task['state'] in ACTIVE:
                task['state'] = 'stopping'
            self.store.save(task)

    def _run(self, task, snapshot):
        try:
            cache = {}
            attempts = 0
            current_policy = task['engine_version'] in ('2', '3')
            evaluation = 'evaluation' in task
            partial_state = snapshot.get('replay_source_state') or snapshot.get('state') if snapshot else None
            partial_replay = partial_state in {'cancelled','interrupted'}
            if partial_replay:
                task['replay_source_state'] = partial_state
            def query(text, purpose):
                nonlocal attempts
                if self.cancel.is_set():
                    raise InterruptedError()
                if text in cache:
                    task['metrics']['cache_hits'] += 1
                    return cache[text]
                if partial_replay and attempts >= len(snapshot['queries']):
                    raise ReplayBoundary()
                if attempts >= task['query_budget']:
                    task['metrics']['budget_exhausted'] = True
                    raise StopIteration()
                attempts += 1
                task['metrics']['queries'] = attempts
                if snapshot is None:
                    task['metrics']['api_calls'] += 1
                trace = {'query':text,'purpose':purpose,'error':None,'result':None,'cost':0 if snapshot is not None else 1}
                if snapshot is not None:
                    recorded=next((r for r in snapshot['queries'] if r['query']==text),{})
                    if 'retrieved_at' in recorded:
                        trace['retrieved_at']=recorded['retrieved_at']
                try:
                    result = validate_result(provider.search(text,task['max_results']),task['max_results'])
                    trace.update(result=result,total=result['total'],returned=result['returned'],partial=result['partial'])
                    if snapshot is None:
                        trace['retrieved_at']=now()
                    cache[text] = result
                    if snapshot is not None:
                        task['metrics']['cache_hits'] += 1
                except Exception:
                    trace['error'] = 'Replay cache miss or recorded failure' if snapshot is not None else 'FOFA request failed or returned malformed data'
                    task['queries'].append(trace)
                    self._save(task)
                    raise ProviderError(trace['error']) from None
                task['queries'].append(trace)
                task['metrics']['returned'] += result['returned']
                self._save(task)
                return result
            provider = ReplayProvider(snapshot['queries']) if snapshot is not None else FofaProvider()
            task['state'] = 'running'
            self._save(task)
            seed_assets = {}
            available_queries = {q['query'] for q in snapshot['queries']} if snapshot else set()
            recorded_gaps = {r['query'] for r in snapshot.get('policy',{}).get('offline_missing',[])} if snapshot else set()
            def check_boundary(text):
                if not partial_replay or text in cache:
                    return
                if evaluation and text not in available_queries:
                    if text not in recorded_gaps:
                        raise ReplayBoundary()
                elif attempts >= len(snapshot['queries']):
                    raise ReplayBoundary()
            for seed in task['seeds']:
                if self.cancel.is_set():
                    raise InterruptedError()
                check_boundary(seed_query(seed))
                if evaluation and seed_query(seed) not in available_queries:
                    task['policy']['offline_missing'].append({'query':seed_query(seed),'purpose':'seed','feature_ids':[],'reason':'not_recorded'})
                    continue
                result = query(seed_query(seed),'seed')
                for asset in result['assets']:
                    seed_assets[asset_id(asset)] = asset
                task['seed_assets'] = list(seed_assets.values())
                self._save(task)
            task['seed_assets'] = list(seed_assets.values())
            task['features'] = extract_features(task['seed_assets'],with_policy=current_policy,engine_version=task['engine_version'])
            features = task['features']
            if snapshot is not None:
                selections = snapshot.get('llm_selections',[])
                if selections:
                    selections = validate_selection({'selections':selections}, features)
                    task['llm_selections'] = selections
                    order = {s['feature_id']:i for i,s in enumerate(selections)}
                    features = sorted(features,key=lambda f:order.get(f['id'],len(order)))
            elif task['use_llm'] and features and (not current_policy or any(not f['generic'] for f in features)):
                if self.cancel.is_set():
                    raise InterruptedError()
                try:
                    selections = validate_selection({'selections':select_features(features,seeds=task['seeds'],context=task['context'],assets=task['seed_assets'])},features)
                    task['llm_selections'] = selections
                    order = {s['feature_id']:i for i,s in enumerate(selections)}
                    features = sorted(features,key=lambda f:order.get(f['id'],len(order)))
                except ProviderError:
                    task['warnings'].append('DeepSeek unavailable or invalid evidence; deterministic fallback used')
            candidates = {}
            rules = generate_rules(features,300 if current_policy else 64)
            if current_policy:
                rules,task['policy']['skipped'] = policy.filter_rules(rules,features)
                if not rules:
                    task['policy']['stop_reason'] = 'no_discriminating_features'
            broad_streak = 0
            for index,rule in enumerate(rules):
                if self.cancel.is_set():
                    raise InterruptedError()
                check_boundary(rule['query'])
                if evaluation and rule['query'] not in available_queries:
                    task['policy']['offline_missing'].append({**rule,'reason':'not_recorded'})
                    broad_streak = 0
                    continue
                result = query(rule['query'],'expansion')
                comparison = compare_assets(task['seed_assets'],result['assets'])
                new_ids = {asset_id(a) for a in result['assets']} - set(seed_assets)
                redundant = len(new_ids & set(candidates))
                score = round((10 if comparison['coverage'] > 0 else 0) + 10 * comparison['coverage'] + min(comparison['new_count'],5) / (5 * (1 + redundant)) - rule['generic_penalty'] - .25,4)
                task['rules'].append(dict(rule,**comparison,total=result['total'],returned=result['returned'],partial=result['partial'],coverage_unverified=result['partial'],score=score))
                task['rules'].sort(key=lambda r:-r['score'])
                if current_policy and result['total'] >= policy.BROAD_TOTAL:
                    task['policy']['rejected'].append({**rule,'reason':'broad_result','total':result['total'],'returned':result['returned']})
                    broad_streak += 1
                    if broad_streak >= policy.BROAD_STREAK:
                        task['policy']['stop_reason'] = 'consecutive_broad_rules'
                        task['policy']['skipped'].extend({**r,'reason':'early_stop'} for r in rules[index+1:])
                        self._save(task)
                        break
                    self._save(task)
                    continue
                broad_streak = 0
                for asset in result['assets']:
                    ident = asset_id(asset)
                    if ident not in seed_assets:
                        candidate = candidates.setdefault(ident,{'id':ident,'asset':asset,'reasons':[],'decision':'pending','attribution':'','note':''})
                        candidate['reasons'].append({'query':rule['query'],'feature_ids':rule['feature_ids'],'score':score})
                task['candidates'] = list(candidates.values())
                task['metrics']['candidates'] = len(candidates)
                self._save(task)
            task['state'] = 'completed'
        except ReplayBoundary:
            task['metrics']['replay_boundary'] = True
            task['warnings'].append('Replayed all recorded queries at the original partial execution boundary')
            task['state'] = 'completed'
        except StopIteration:
            task['warnings'].append('Query budget exhausted; evidence and coverage are partial')
            task['state'] = 'completed'
        except InterruptedError:
            task['state'] = 'cancelled'
        except ProviderError:
            task['state'] = 'failed'
            task['error'] = 'Replay cache miss or recorded provider failure' if snapshot is not None else 'Provider request failed; inspect query traces'
        except Exception:
            task['state'] = 'failed'
            task['error'] = 'Hunt failed while processing evidence'
        finally:
            try:
                with self.lock:
                    if self.cancel.is_set():
                        task['state'] = 'cancelled'
                    try:
                        self.store.save(task, imported_reviews=snapshot.get('feedback',[]) if snapshot is not None else None)
                    except Exception:
                        task['state'] = 'failed'
                        task['error'] = 'Failed to persist final hunt snapshot or imported feedback'
                        self.store.fail_finalization(task)
            finally:
                self.active = None
                _PROCESS_LOCK.release()

    def export(self, ident):
        task = self.store.get(ident)
        return {'version':1, **task}

    def replay(self, ident):
        snapshot = self.export(ident)
        if snapshot['state'] in ACTIVE:
            raise Conflict('wait for the task to finish before replay')
        return self.start({'name':snapshot['name'][:191] + ' (replay)','mode':'replay','seed_ids':[s['id'] for s in snapshot['seeds']],
                           'query_budget':snapshot['query_budget'],'max_results':snapshot['max_results']}, trusted_snapshot=snapshot)

    def evaluate(self, ident):
        """Apply current gates to recorded responses only; never spend API credit."""
        snapshot = self.export(ident)
        if snapshot['state'] in ACTIVE:
            raise Conflict('wait for the task to finish before evaluating')
        snapshot['evaluation'] = {'source_id':ident,'source_engine_version':snapshot['engine_version']}
        snapshot['engine_version'] = ENGINE_VERSION
        snapshot['feedback'] = []
        snapshot['llm_selections'] = []
        snapshot['state'] = 'completed'
        snapshot.pop('replay_source_state',None)
        # Inspect every available recorded response; budget does not imply new calls.
        snapshot['query_budget'] = 64
        return self.start({'name':snapshot['name'][:170]+' (current policy / offline)',
                           'mode':'replay','seed_ids':[s['id'] for s in snapshot['seeds']],'use_llm':False},trusted_snapshot=snapshot)


def manager_for(root):
    key = str(Path(root).resolve())
    with _REGISTRY_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = HuntManager(root)
            _MANAGERS[key] = manager
        return manager


_REGISTRY_LOCK = threading.Lock()
