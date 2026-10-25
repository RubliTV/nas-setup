"""SQLite transactions enforce one active installation across processes."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from .models import SetupError

ACTIVE={'running','pending','waiting-for-user','interrupted','paused','failed','waiting-for-sync'}

class JobStore:
    def __init__(self, directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=self.directory/'jobs.sqlite'
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)')
        self.db.chmod(0o600)

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=10)
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback(); raise
        finally: c.close()

    def create(self, selection, steps, **metadata):
        job={'id':uuid.uuid4().hex,'selection':selection,'status':'running','pause_requested':False,
             'steps':[{'id':s,'status':'pending'} for s in steps], 'error':None,'links':{}}
        job.update(metadata)
        with self.connect() as c:
            existing=c.execute('SELECT body FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
            if existing and json.loads(existing[0])['status'] in ACTIVE: raise SetupError('job_active')
            c.execute('INSERT INTO jobs VALUES(?,?,?)',(job['id'],json.dumps(job),time.time()))
        return job

    def get(self, identity=None):
        with self.connect() as c:
            row=c.execute('SELECT body FROM jobs WHERE id=?',(identity,)).fetchone() if identity else c.execute('SELECT body FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
            return json.loads(row[0]) if row else None

    def edit(self, identity, change):
        with self.connect() as c:
            row=c.execute('SELECT body FROM jobs WHERE id=?',(identity,)).fetchone()
            if not row: raise SetupError('job_missing')
            job=json.loads(row[0]); change(job)
            c.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),identity))
            return job

    def update(self,identity,**values): return self.edit(identity,lambda j:j.update(values))

    def step(self,identity,step,status):
        def mutate(job):
            for s in job['steps']:
                if s['id']==step:
                    s['status']=status
                    s.pop('activity',None)
                    if status=='running':s['started_at']=time.time()
        return self.edit(identity,mutate)

    def activity(self,identity,step,code):
        from .progress import ACTIVITIES
        if code not in ACTIVITIES:raise ValueError('Unknown activity identifier')
        def mutate(job):
            if job['status']!='running':return
            for record in job['steps']:
                if record['id']==step and record['status']=='running':record['activity']=code
        return self.edit(identity,mutate)

    def interrupt_running(self):
        job=self.get()
        if job and job['status']=='running':
            self.update(job['id'],status='interrupted')
