"""Local administration, demo, and systemd entry points."""
import argparse
import getpass
import warnings
import json
import os
from pathlib import Path
import subprocess
import sys
from .auth import issue_code
from .manager_auth import ManagerAccount
from .models import SetupError


def main():
    parser=argparse.ArgumentParser(description='NAS Setup · Instalador de NAS / NAS installer')
    sub=parser.add_subparsers(dest='command',required=True)
    demo=sub.add_parser('demo');demo.add_argument('--port',type=int,default=8765);demo.add_argument('--state',default='.demo')
    sub.add_parser('web');sub.add_parser('worker')
    reopen=sub.add_parser('reopen');reopen.add_argument('--pairing',action='store_true',help='Issue a one-use recovery/enrollment code')
    account=sub.add_parser('manager-account',help='Create or locally reset the separate manager login')
    mode=account.add_mutually_exclusive_group()
    mode.add_argument('--reset',action='store_true');mode.add_argument('--if-missing',action='store_true')
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
    elif args.command=='manager-account':
        if os.geteuid()!=0:parser.error('Use sudo / Execute com sudo')
        directory=Path('/var/lib/nas-setup/web')
        if ManagerAccount(directory).exists():
            if args.if_missing:return
            if not args.reset:parser.error('Manager account exists; use --reset / Conta existente; use --reset')
        print('Manager login is separate from SMB sharing accounts. / Login do gerenciador separado das contas SMB.',flush=True)
        try:
            with open('/dev/tty','r') as tty:
                if not tty.isatty():raise OSError()
                print('Manager username / Usuário (letters, digits, . _ -): ',end='',file=sys.stderr,flush=True)
                username=tty.readline(66).rstrip('\r\n')
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    password=getpass.getpass('Manager password / Senha (12–128): ')
                    confirmation=getpass.getpass('Repeat password / Repita a senha: ')
        except (OSError,EOFError,getpass.GetPassWarning):parser.error('A local terminal is required / É necessário um terminal local')
        if password!=confirmation:parser.error('Passwords do not match / Senhas diferentes')
        try:ManagerAccount.validate(username,password)
        except SetupError:parser.error('Use a 1–64 character username and 12–128 character password / Credenciais inválidas')
        import pwd
        u=pwd.getpwnam('nas-setup')
        # Only the unprivileged owner writes its state. Secrets travel via a pipe,
        # never process arguments, the environment, output, or exception text.
        script=('import json,sys; from nas_setup.manager_auth import ManagerAccount; '
                'p=json.load(sys.stdin); ManagerAccount("/var/lib/nas-setup/web").create('
                'p["username"],p["password"],replace=p["replace"])')
        result=subprocess.run([sys.executable,'-c',script],
            input=json.dumps({'username':username,'password':password,'replace':args.reset}),
            user=u.pw_uid,group=u.pw_gid,extra_groups=[],cwd='/',capture_output=True,text=True)
        password=confirmation=None
        if result.returncode:parser.error('Could not save manager account / Não foi possível salvar a conta')
        print('Manager account saved. / Conta do gerenciador salva.',flush=True)
    elif args.command=='reopen':
        if os.geteuid()!=0:parser.error('Use sudo / Execute com sudo')
        code=None
        if args.pairing or not ManagerAccount(Path('/var/lib/nas-setup/web')).exists():
            import pwd
            u=pwd.getpwnam('nas-setup')
            # All writes in the web-owned directory use that account's privileges.
            result=subprocess.run([sys.executable,'-c',
                'from nas_setup.auth import issue_code; print(issue_code("/var/lib/nas-setup/web"))'],
                user=u.pw_uid,group=u.pw_gid,extra_groups=[],cwd='/',
                capture_output=True,text=True,check=True)
            code=result.stdout.strip()
        subprocess.run(['systemctl','enable','--now','nas-setup-worker','nas-setup-web'],check=True)
        config=json.loads(Path('/etc/nas-setup/config.json').read_text())
        print(config['origin'])
        if code:print('Código / Code (15 min): '+code)
        else:print('Sign in with your manager username and password. / Entre com seu usuário e senha do gerenciador.')

if __name__=='__main__':main()
