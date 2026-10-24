"""Length-bounded JSON RPC on a permissions-restricted local Unix socket."""
import json
import socket
from .models import SetupError

MAX_MESSAGE=262144
OPERATIONS={'inventory','plan','start','job','resume','pause','tailscale','finish'}

class WorkerClient:
    def __init__(self,path):self.path=str(path)
    def call(self,operation,payload=None):
        if operation not in OPERATIONS:raise SetupError('operation_invalid')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(30)
            try:
                sock.connect(self.path)
                sock.sendall(json.dumps({'operation':operation,'payload':payload or {}}).encode()+b'\n')
                with sock.makefile('rb') as stream:raw=stream.readline(MAX_MESSAGE+1)
                if not raw.endswith(b'\n') or len(raw)>MAX_MESSAGE:raise SetupError('worker_unavailable')
                result=json.loads(raw)
            except (OSError,ValueError):raise SetupError('worker_unavailable')
        if 'error' in result:raise SetupError(result['error'])
        return result['result']
