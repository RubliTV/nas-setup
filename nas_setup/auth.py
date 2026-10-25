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
from .manager_auth import ManagerAccount


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
        self.account=ManagerAccount(directory)

    def _attempt(self,peer):
        now=time.time()
        self.attempts={p:[t for t in ts if now-t<60] for p,ts in self.attempts.items() if any(now-t<60 for t in ts)}
        tries=self.attempts.setdefault(peer,[])
        if len(tries)>=5:raise SetupError('rate_limited')
        tries.append(now)
        return now

    def _session(self,now,revision,username=None):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        self.sessions={key:value for key,value in self.sessions.items() if value['expires']>=now}
        self.sessions[token]={'csrf':csrf,'expires':now+3600,'id':secrets.token_urlsafe(24),
                              'username':username,'account_revision':revision}
        return token,csrf

    def login(self,code,peer):
        with self.lock:
            now=self._attempt(peer)
            try:
                record=json.loads(self.path.read_text())
                digest=hashlib.sha256(code.encode()).hexdigest()
                if record['expires']<now or not hmac.compare_digest(record['hash'],digest):raise ValueError()
            except (OSError,ValueError,KeyError,TypeError,AttributeError):raise SetupError('login_invalid') from None
            self.path.unlink()
            self.attempts.pop(peer,None)
            self.sessions.clear()
            return self._session(now,self.account.revision())

    def login_password(self,username,password,peer):
        with self.lock:
            now=self._attempt(peer)
            revision=self.account.authenticate(username,password)
            if revision is None:raise SetupError('login_invalid')
            self.attempts.pop(peer,None)
            # A valid password also consumes an outstanding recovery code.
            if self.path.exists():self.sessions.clear()
            self.path.unlink(missing_ok=True)
            return self._session(now,revision,username)

    def logout(self,token):
        with self.lock:self.sessions.pop(token,None)

    def session(self,token):
        with self.lock:
            session=self.sessions.get(token)
            if (not session or session['expires']<time.time() or self.path.exists()
                    or session['account_revision']!=self.account.revision()):
                self.sessions.pop(token,None)
                raise SetupError('login_required')
            return session
