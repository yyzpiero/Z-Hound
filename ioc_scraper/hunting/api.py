"""HTTP boundary for the isolated hunt workspace."""
from fastapi import Body
from fastapi.responses import JSONResponse
from .demo import snapshot as demo_snapshot
from .providers import configuration, scrub
from .service import manager_for, read_seeds
from .store import Conflict


def install_routes(app, data_dir):
    manager = manager_for(data_dir)
    app.state.hunting_manager = manager

    def error(status, message):
        return JSONResponse({'error': {400:'invalid_request',404:'not_found',409:'hunt_conflict'}[status], 'message':message},status_code=status)

    def invoke(call):
        try:
            return call()
        except KeyError:
            return error(404,'task, seed or candidate not found')
        except Conflict as exc:
            return error(409,str(exc))
        except ValueError as exc:
            return error(400,scrub(str(exc)) or 'invalid hunting request or replay snapshot')
        except (TypeError, OverflowError, RecursionError):
            return error(400,'invalid hunting request or replay snapshot')

    @app.get('/api/hunting/config')
    def config():
        return configuration()

    @app.get('/api/hunting/seeds')
    def seeds():
        return {'items':manager.store.seeds()}

    @app.get('/api/hunting/collector-seeds')
    def collector_seeds():
        return {'items':read_seeds(data_dir)}

    @app.post('/api/hunting/seeds/import')
    def import_seeds(payload: object = Body(None)):
        from .seeds import parse_import
        return invoke(lambda:manager.store.import_seeds(parse_import(payload)))

    @app.post('/api/hunting/seeds/from-collector')
    def transfer_seeds(payload: object = Body(None)):
        def transfer():
            from .seeds import normalize_record
            if not isinstance(payload,dict) or set(payload)!={'seed_ids'}:
                raise ValueError('invalid collector selection')
            ids=payload['seed_ids']
            if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
                raise ValueError('select 1–500 distinct collector seeds')
            known={s['id']:s for s in read_seeds(data_dir)}
            if any(i not in known for i in ids): raise ValueError('unknown collector seed')
            records=[]
            for ident in ids:
                record={k:v for k,v in known[ident].items() if k!='id'}
                record['source']=record.get('report_url') or record.get('report_title') or ''
                records.append(normalize_record(record))
            return manager.store.import_seeds(records)
        return invoke(transfer)

    @app.post('/api/hunting/seeds/{ident}/review')
    def review_seed(ident: str, payload: object = Body(None)):
        return invoke(lambda:manager.store.review_seed(ident,payload))

    @app.get('/api/hunts')
    def tasks():
        return {'items':manager.store.list()}

    @app.post('/api/hunts',status_code=202)
    def start(payload: object = Body(None)):
        return invoke(lambda:manager.start(payload))

    @app.post('/api/hunts/demo',status_code=202)
    def demo():
        snap = demo_snapshot()
        return invoke(lambda:manager.start({'name':'Synthetic demonstration','mode':'replay','seed_ids':['demo-seed']},trusted_snapshot=snap))

    @app.get('/api/hunts/{ident}')
    def detail(ident: str):
        return invoke(lambda:manager.store.get(ident))

    @app.get('/api/hunts/{ident}/candidates/{candidate_id}/assessment')
    def assessment(ident: str, candidate_id: str):
        return invoke(lambda:manager.store.assessment(ident,candidate_id))

    @app.post('/api/hunts/{ident}/stop',status_code=202)
    def stop(ident: str):
        return invoke(lambda:manager.stop(ident))

    @app.post('/api/hunts/{ident}/feedback')
    def feedback(ident: str, payload: object = Body(None)):
        def apply():
            if not isinstance(payload,dict) or set(payload) - {'candidate_id','decision','attribution','note'}:
                raise ValueError()
            if not isinstance(payload.get('candidate_id'),str) or payload.get('decision') not in {'related','unrelated','uncertain'}:
                raise ValueError()
            for key,limit in (('candidate_id',200),('attribution',500),('note',4000)):
                if not isinstance(payload.get(key,''),str) or len(payload.get(key,'')) > limit:
                    raise ValueError()
            return manager.store.feedback(ident,payload['candidate_id'],payload['decision'],scrub(payload.get('attribution','')),scrub(payload.get('note','')))
        return invoke(apply)

    @app.get('/api/hunts/{ident}/export')
    def export(ident: str):
        return invoke(lambda:manager.export(ident))

    @app.post('/api/hunts/{ident}/replay',status_code=202)
    def replay(ident: str):
        return invoke(lambda:manager.replay(ident))

    @app.post('/api/hunts/{ident}/evaluate',status_code=202)
    def evaluate(ident: str):
        return invoke(lambda:manager.evaluate(ident))

    return manager
