"""Durable operation intents. An uncertain mutation is never blindly retried."""
from contextlib import contextmanager
import json
import sqlite3
import threading
import time
from pathlib import Path
from .models import SetupError


class OperationJournal:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.directory/'operations.sqlite'
        self.lock=threading.RLock()
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT, destructive INTEGER, updated REAL)')
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def get(self,key):
        with self._connect() as db:
            row=db.execute('SELECT status,result,destructive FROM operations WHERE key=?',(key,)).fetchone()
        return None if row is None else {'status':row[0],'result':json.loads(row[1]) if row[1] else None,'destructive':bool(row[2])}

    def snapshot(self):
        with self._connect() as db:
            rows=db.execute('SELECT key,status,result,destructive FROM operations ORDER BY key').fetchall()
        return rows

    def perform(self,key,action,reconcile=None,destructive=False):
        with self.lock:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT status,result,destructive FROM operations WHERE key=?',(key,)).fetchone()
                if row is None:
                    db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(key,'dispatched',None,int(destructive),time.time()))
            if row is not None:
                record={'status':row[0],'result':json.loads(row[1]) if row[1] else None,'destructive':bool(row[2])}
                if reconcile is not None:
                    result=reconcile(record)
                    if result is None:raise SetupError('recovery_required')
                elif record['status']=='completed':return record['result']
                else:raise SetupError('recovery_required')
            else:
                # Intent is committed (FULL synchronous) before invoking the operation.
                result=action()
            encoded=json.dumps(result)
            with self._connect() as db:
                db.execute('UPDATE operations SET status=?,result=?,updated=? WHERE key=?',('completed',encoded,time.time(),key))
            return result
