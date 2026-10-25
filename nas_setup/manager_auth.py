"""A separate local manager account; never a Linux or SMB password."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

from .models import SetupError


class ManagerAccount:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.path=self.directory/'manager-account.json'

    def exists(self):
        return self.path.exists()

    @staticmethod
    def validate(username,password):
        if (not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',username)
                or not isinstance(password,str) or not 12<=len(password)<=128):
            raise SetupError('manager_credentials_invalid')

    @staticmethod
    def _hash(password,salt):
        return hashlib.scrypt(password.encode('utf-8'),salt=salt,n=131072,r=8,p=1,
                              maxmem=256*1024*1024,dklen=32).hex()

    def create(self,username,password,replace=False):
        self.validate(username,password)
        if not replace and self.exists():raise SetupError('manager_account_exists')
        salt=secrets.token_bytes(32)
        record={'version':1,'username':username,'salt':salt.hex(),
                'hash':self._hash(password,salt),'algorithm':'scrypt-n131072-r8-p1'}
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,name=tempfile.mkstemp(prefix='.manager-account-',dir=self.directory)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
            if replace:os.replace(name,self.path)
            else:
                # Atomic no-clobber enrollment: concurrent requests cannot replace it.
                try:os.link(name,self.path)
                except FileExistsError:raise SetupError('manager_account_exists') from None
            # Resetting credentials revokes any previously issued console code.
            (self.directory/'access.json').unlink(missing_ok=True)
        finally:
            Path(name).unlink(missing_ok=True)

    def revision(self):
        try:return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:return None
        except OSError:return 'unreadable'

    def authenticate(self,username,password):
        """Return the verified file revision, preventing login/reset races."""
        if not isinstance(username,str) or not isinstance(password,str) or len(password)>128:return None
        try:
            raw=self.path.read_bytes();record=json.loads(raw)
            if record['version']!=1 or record['algorithm']!='scrypt-n131072-r8-p1':return None
            salt=bytes.fromhex(record['salt']);expected=record['hash']
            if len(salt)!=32 or not isinstance(expected,str) or len(expected)!=64:return None
            digest=self._hash(password,salt)
            if hmac.compare_digest(digest,expected) and hmac.compare_digest(username.encode(),record['username'].encode()):
                return hashlib.sha256(raw).hexdigest()
        except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
        return None

    def verify(self,username,password):
        return self.authenticate(username,password) is not None
