"""One-use console pairing codes and expiring in-memory browser sessions."""
import hashlib
import hmac
import json
import os
import tempfile
import secrets
import threading
import time
from pathlib import Path
from .models import SetupError


def issue_code(directory):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    code=secrets.token_urlsafe(24)
    path=directory/'access.json'
    data={'hash':hashlib.sha256(code.encode()).hexdigest(),'expires':time.time()+900}
    fd,name=tempfile.mkstemp(prefix='.access-',dir=directory)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps(data));stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
    return code

class Auth:
    def __init__(self,directory):
        self.path=Path(directory)/'access.json'; self.sessions={}; self.attempts={}; self.lock=threading.Lock()

    def login(self,code,peer):
        with self.lock:
            now=time.time()
            self.attempts={p:[t for t in ts if now-t<60] for p,ts in self.attempts.items() if any(now-t<60 for t in ts)}
            tries=self.attempts.setdefault(peer,[])
            if len(tries)>=5: raise SetupError('rate_limited')
            tries.append(now)
            try: record=json.loads(self.path.read_text())
            except (OSError,ValueError): raise SetupError('login_invalid')
            digest=hashlib.sha256(code.encode()).hexdigest()
            if record['expires']<now or not hmac.compare_digest(record['hash'],digest): raise SetupError('login_invalid')
            self.path.unlink()
            self.sessions.clear()
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
            self.sessions[token]={'csrf':csrf,'expires':now+3600,'id':secrets.token_urlsafe(24)}
            return token,csrf

    def session(self,token):
        session=self.sessions.get(token)
        if not session or session['expires']<time.time(): raise SetupError('login_required')
        # Rotating a pairing code invalidates existing sessions immediately.
        if self.path.exists(): raise SetupError('login_required')
        return session
