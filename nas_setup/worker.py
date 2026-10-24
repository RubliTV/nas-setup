"""Root worker; the web service can invoke only the Engine's typed operations."""
import grp
import json
import os
import pwd
import socket
import socketserver
import struct
from pathlib import Path
from pydantic import ValidationError
from .engine import Engine
from .system import System
from .rpc import MAX_MESSAGE
from .models import SetupError


def serve(socket_path='/run/nas-setup/worker.sock',state_dir='/var/lib/nas-setup/worker'):
    if os.geteuid()!=0:raise SystemExit('Worker requires root')
    allowed=pwd.getpwnam('nas-setup').pw_uid;group=grp.getgrnam('nas-setup').gr_gid
    engine=Engine(state_dir,System(state_dir))
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(30)
            _,uid,_=struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid not in (0,allowed):return
            try:
                raw=self.rfile.readline(MAX_MESSAGE+1)
                if len(raw)>MAX_MESSAGE or not raw.endswith(b'\n'):raise SetupError('request_invalid')
                request=json.loads(raw)
                if set(request)!={'operation','payload'} or not isinstance(request['payload'],dict):raise SetupError('request_invalid')
                result={'result':engine.call(request['operation'],request['payload'])}
            except SetupError as e:result={'error':e.code}
            except (ValidationError,ValueError,TypeError):result={'error':'request_invalid'}
            except Exception:result={'error':'worker_failed'}
            self.wfile.write(json.dumps(result).encode()+b'\n')
    class Server(socketserver.ThreadingUnixStreamServer):daemon_threads=True
    path=Path(socket_path);path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    os.chown(path.parent,0,group);path.parent.chmod(0o750)
    if path.exists():path.unlink()
    with Server(str(path),Handler) as server:
        os.chown(path,0,group);path.chmod(0o660)
        server.serve_forever()
