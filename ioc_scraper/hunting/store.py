"""SQLite snapshots and append-only analyst audit."""
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

ACTIVE = ('queued', 'running', 'stopping')
_INSTANCE_TOKEN = uuid.uuid4().hex


def windows_process_info(pid):
    """Return (alive/unknown, creation identity) without sending any signal."""
    try:
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [('low',ctypes.c_uint32),('high',ctypes.c_uint32)]

        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        kernel.GetExitCodeProcess.restype = ctypes.c_int
        kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)] * 4
        kernel.GetProcessTimes.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        handle = kernel.OpenProcess(0x1000,False,pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return (False,None) if ctypes.get_last_error() == 87 else (None,None)
        try:
            code = ctypes.c_uint32()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):
                return None,None
            if code.value != 259:  # STILL_ACTIVE
                return False,None
            created,exited,kernel_time,user_time = (FileTime() for _ in range(4))
            if kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel_time),ctypes.byref(user_time)):
                return True,f'windows:{(created.high << 32) | created.low}'
            return True,None
        finally:
            kernel.CloseHandle(handle)
    except (OSError,AttributeError):
        return None,None


def process_birth(pid):
    """Identify a process instance, including Linux container PID reuse.

    Linux exposes boot ID + process start ticks. Darwin's libproc exposes
    microsecond process birth time (proc_bsdinfo from sys/proc_info.h).
    If inspection is unavailable, only this interpreter gets a stable token;
    another live process with unknown identity is conservatively preserved.
    """
    try:
        if sys.platform == 'win32':
            _,birth = windows_process_info(pid)
            if birth:
                return birth
        if sys.platform.startswith('linux'):
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            # comm may contain spaces and parentheses; fields after its last ')'
            # start at stat field 3; starttime is field 22.
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
            return f'linux:{boot}:{int(fields[19])}'
        if sys.platform == 'darwin':
            import ctypes

            class BsdInfo(ctypes.Structure):
                _fields_ = [('ids',ctypes.c_uint32 * 12),('comm',ctypes.c_char * 16),
                            ('name',ctypes.c_char * 32),('process_fields',ctypes.c_uint32 * 5),
                            ('nice',ctypes.c_int32),('start_seconds',ctypes.c_uint64),
                            ('start_microseconds',ctypes.c_uint64)]

            proc = ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidinfo
            proc.argtypes = [ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
            proc.restype = ctypes.c_int
            info = BsdInfo()
            if proc(pid,3,0,ctypes.byref(info),ctypes.sizeof(info)) == ctypes.sizeof(info):
                return f'darwin:{info.start_seconds}:{info.start_microseconds}'
    except (OSError,ValueError,IndexError,AttributeError):
        pass
    return f'local:{_INSTANCE_TOKEN}' if pid == os.getpid() else None


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self.path = Path(root) / 'hunting.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL, payload TEXT NOT NULL,owner_pid INTEGER,owner_birth TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_hunt ON tasks((1)) WHERE state IN ('queued','running','stopping');
                CREATE TABLE IF NOT EXISTS seeds(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seed_reviews(seq INTEGER PRIMARY KEY AUTOINCREMENT,seed_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS seed_reviews_no_update BEFORE UPDATE ON seed_reviews BEGIN SELECT RAISE(ABORT,'append-only seed reviews'); END;
                CREATE TRIGGER IF NOT EXISTS seed_reviews_no_delete BEFORE DELETE ON seed_reviews BEGIN SELECT RAISE(ABORT,'append-only seed reviews'); END;
                CREATE TABLE IF NOT EXISTS feedback(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,candidate_id TEXT NOT NULL,decision TEXT NOT NULL,attribution TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS feedback_no_update BEFORE UPDATE ON feedback BEGIN SELECT RAISE(ABORT,'append-only feedback'); END;
                CREATE TRIGGER IF NOT EXISTS feedback_no_delete BEFORE DELETE ON feedback BEGIN SELECT RAISE(ABORT,'append-only feedback'); END;
            ''')
            if 'owner_pid' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN owner_pid INTEGER')
            if 'owner_birth' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN owner_birth TEXT')

    def seeds(self):
        from .seeds import eligible
        with self.connect() as db:
            items=[]
            for row in db.execute('SELECT payload FROM seeds ORDER BY rowid'):
                seed=json.loads(row['payload'])
                seed['history']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM seed_reviews WHERE seed_id=? ORDER BY seq',(seed['id'],))]
                seed['eligible']=bool(eligible(seed))
                items.append(seed)
        return items

    def import_seeds(self, records):
        duplicates=0
        ids=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for record in records:
                ident=record['id']
                if db.execute('SELECT 1 FROM seeds WHERE id=?',(ident,)).fetchone():
                    duplicates+=1
                else:
                    seed={**record,'qualification':{'status':'pending','value':'unrated','reviewer':'','rationale':'','reviewed_at':None,'revision':0}}
                    db.execute('INSERT INTO seeds(id,payload) VALUES(?,?)',(ident,json.dumps(seed)))
                if ident not in ids: ids.append(ident)
        known={s['id']:s for s in self.seeds()}
        return {'items':[known[ident] for ident in ids],'duplicates':duplicates}

    def review_seed(self, ident, payload):
        from .seeds import validate_review
        review=validate_review(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM seeds WHERE id=?',(ident,)).fetchone()
            if row is None: raise KeyError(ident)
            seed=json.loads(row['payload'])
            if review['revision']!=seed['qualification']['revision']:
                raise Conflict('seed review revision is stale; refresh and review again')
            qualification={key:review[key] for key in ('status','value','reviewer','rationale')}
            qualification.update(revision=review['revision']+1,reviewed_at=now())
            audit={**qualification,'source':review['source'],'evidence':review['evidence'],
                   'previous_source':seed['source'],'previous_evidence':seed['evidence']}
            seed.update(source=review['source'],evidence=review['evidence'],qualification=qualification)
            db.execute('INSERT INTO seed_reviews(seed_id,payload) VALUES(?,?)',(ident,json.dumps(audit)))
            db.execute('UPDATE seeds SET payload=? WHERE id=?',(json.dumps(seed),ident))
        return next(s for s in self.seeds() if s['id']==ident)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def interrupt(self):
        with self.connect() as db:
            for row in db.execute("SELECT payload,owner_pid,owner_birth FROM tasks WHERE state IN ('queued','running','stopping')").fetchall():
                if row['owner_pid']:
                    if sys.platform == 'win32':
                        alive,birth = windows_process_info(row['owner_pid'])
                        if alive is None:
                            continue  # Access denied/inspection error: preserve owner.
                    else:
                        alive = True
                        try:
                            os.kill(row['owner_pid'], 0)
                        except ProcessLookupError:
                            alive = False
                        except PermissionError:
                            pass
                        birth = process_birth(row['owner_pid']) if alive else None
                    if alive:
                        if birth is None:
                            continue
                        if row['owner_birth'] and birth:
                            if row['owner_birth'] == birth:
                                continue
                        elif row['owner_pid'] != os.getpid():
                            # Do not interrupt another process we cannot inspect.
                            continue
                task = json.loads(row['payload'])
                task.update(state='interrupted', error='Process restarted before hunt finished')
                db.execute('UPDATE tasks SET state=?,payload=? WHERE id=?', ('interrupted', json.dumps(task), task['id']))

    def create(self, task, qualified_ids=None):
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if qualified_ids is not None:
                    from .seeds import eligible, seed_snapshot
                    snapshots = []
                    for ident in qualified_ids:
                        row = db.execute('SELECT payload FROM seeds WHERE id=?',(ident,)).fetchone()
                        seed = json.loads(row['payload']) if row else None
                        if seed is None or not eligible(seed):
                            raise ValueError('live hunts require confirmed high-value seeds with provenance')
                        snapshots.append(seed_snapshot(seed))
                    task['seeds'] = snapshots
                db.execute('INSERT INTO tasks(id,state,payload,owner_pid,owner_birth) VALUES(?,?,?,?,?)',
                           (task['id'], task['state'], json.dumps(task),os.getpid(),process_birth(os.getpid())))
        except sqlite3.IntegrityError:
            raise Conflict('a hunting task is already active') from None

    def save(self, task, imported_reviews=None):
        with self.connect() as db:
            db.execute('UPDATE tasks SET state=?,payload=? WHERE id=?', (task['state'], json.dumps(task), task['id']))
            if imported_reviews:
                from .providers import scrub
                known = {c['id'] for c in task['candidates']}
                for review in imported_reviews:
                    if review['candidate_id'] in known:
                        db.execute('INSERT INTO feedback(task_id,candidate_id,decision,attribution,note,created_at) VALUES(?,?,?,?,?,?)',
                                   (task['id'],scrub(review['candidate_id']),review['decision'],scrub(review.get('attribution','')),scrub(review.get('note','')),scrub(review.get('created_at') or now())))

    def fail_finalization(self, task):
        """Rollback-free recovery path; never reattempt imported audit writes."""
        with self.connect() as db:
            db.execute('UPDATE tasks SET state=?,payload=? WHERE id=?',('failed',json.dumps(task),task['id']))

    def get(self, ident):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            task = json.loads(row['payload'])
            feedback = [dict(row) for row in db.execute('SELECT * FROM feedback WHERE task_id=? ORDER BY seq', (ident,))]
        from .assessment import build_assessments
        task['feedback'] = feedback
        assessments=build_assessments(task.get('seeds',[]),task.get('queries',[]),task['candidates'],
                                      task.get('features',[]) if task.get('engine_version') in ('2', '3') else (),
                                      promoting_time=task.get('engine_version') in ('2', '3'),
                                      structured_certificates=task.get('engine_version')=='3')
        for candidate,assessment in zip(task['candidates'],assessments):
            candidate['assessment'] = assessment
        for review in feedback:
            for candidate in task['candidates']:
                if candidate['id'] == review['candidate_id']:
                    candidate.update({k: review[k] for k in ('decision','attribution','note')})
        return task

    def assessment(self, ident, candidate_id):
        """Read one comparison directly, without allocating task-wide previews."""
        from .assessment import build_assessment
        with self.connect() as db:
            row=db.execute('SELECT payload FROM tasks WHERE id=?',(ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            task=json.loads(row['payload'])
        candidate=next((c for c in task['candidates'] if c['id']==candidate_id),None)
        if candidate is None:
            raise KeyError(candidate_id)
        return build_assessment(task.get('seeds',[]),task.get('queries',[]),candidate,
                                features=task.get('features',[]) if task.get('engine_version') in ('2', '3') else (),
                                promoting_time=task.get('engine_version') in ('2', '3'),
                                      structured_certificates=task.get('engine_version')=='3')

    def list(self):
        with self.connect() as db:
            tasks = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM tasks ORDER BY rowid DESC')]
        return [{k: task[k] for k in ('id','name','state','mode','created_at','synthetic','metrics','error')} for task in tasks]

    def feedback(self, ident, candidate_id, decision, attribution, note):
        task = self.get(ident)
        if not any(c['id'] == candidate_id for c in task['candidates']):
            raise KeyError(candidate_id)
        with self.connect() as db:
            db.execute('INSERT INTO feedback(task_id,candidate_id,decision,attribution,note,created_at) VALUES(?,?,?,?,?,?)', (ident,candidate_id,decision,attribution,note,now()))
        return self.get(ident)
