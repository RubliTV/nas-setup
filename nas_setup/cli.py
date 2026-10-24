"""Local administration, demo, and systemd entry points."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from .auth import issue_code


def main():
    parser=argparse.ArgumentParser(description='NAS Setup · Instalador de NAS / NAS installer')
    sub=parser.add_subparsers(dest='command',required=True)
    demo=sub.add_parser('demo');demo.add_argument('--port',type=int,default=8765);demo.add_argument('--state',default='.demo')
    sub.add_parser('web');sub.add_parser('worker');sub.add_parser('reopen')
    stack=sub.add_parser('stack-run');stack.add_argument('app',choices=['immich','jellyfin'])
    args=parser.parse_args()
    if args.command=='demo':
        import uvicorn
        from .engine import Engine
        from .system import DemoSystem
        from .web import create_app
        state=Path(args.state).resolve();code=issue_code(state/'web')
        engine=Engine(state/'worker',DemoSystem(state/'system',delay=1.2))
        origin=f'http://127.0.0.1:{args.port}'
        print(f'DEMO ONLY — no system changes\n{origin}\nAccess code / Código: {code}',flush=True)
        uvicorn.run(create_app(engine,state/'web',origin,demo=True),host='127.0.0.1',port=args.port,access_log=False)
        return
    if args.command=='web':
        import uvicorn
        from .web import create_app
        from .rpc import WorkerClient
        config=json.loads(Path('/etc/nas-setup/config.json').read_text())
        uvicorn.run(create_app(WorkerClient('/run/nas-setup/worker.sock'),'/var/lib/nas-setup/web',config['origin']),
            host=config['address'],port=config['port'],ssl_certfile='/etc/nas-setup/server.crt',
            ssl_keyfile='/etc/nas-setup/server.key',access_log=False)
    elif args.command=='worker':
        from .worker import serve
        serve()
    elif args.command=='stack-run':
        from .system import System
        System().stack_run(args.app)
    elif args.command=='reopen':
        if os.geteuid()!=0:parser.error('Use sudo / Execute com sudo')
        import pwd
        code=issue_code('/var/lib/nas-setup/web');u=pwd.getpwnam('nas-setup')
        os.chown('/var/lib/nas-setup/web/access.json',u.pw_uid,u.pw_gid)
        subprocess.run(['systemctl','enable','--now','nas-setup-worker','nas-setup-web'],check=True)
        config=json.loads(Path('/etc/nas-setup/config.json').read_text())
        print(config['origin']);print('Código / Code (15 min): '+code)

if __name__=='__main__':main()
