"""Bootstrap on an existing OMV host. Standard library only; no Docker required."""
import argparse
import hashlib
import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def run(*args):return subprocess.run(args,check=True,text=True,stdout=subprocess.PIPE).stdout

def ensure_manager_account(parser):
    # Do not use run(): interactive prompts must retain the controlling terminal.
    try:subprocess.run(['/opt/nas-setup/venv/bin/nas-setup','manager-account','--if-missing'],check=True)
    except subprocess.CalledProcessError:
        parser.error('Account setup incomplete. Run sudo nas-setup manager-account, then sudo nas-setup reopen. / Criação da conta incompleta.')


def main():
    p=argparse.ArgumentParser(description='Instalar NAS Setup / Install NAS Setup')
    p.add_argument('--address');p.add_argument('--port',type=int,default=8443);p.add_argument('--check',action='store_true')
    p.add_argument('--update',action='store_true',help='Update installed NAS Setup while preserving jobs, configuration and certificates')
    args=p.parse_args()
    if platform.system()!='Linux':p.error('Requires OMV 8 / Debian 13 / amd64. Requer OMV 8 / Debian 13 / amd64.')
    release=Path('/etc/os-release').read_text()
    if 'ID=debian' not in release or 'VERSION_ID="13"' not in release:p.error('Requires OMV 8 / Debian 13 / amd64.')
    if run('dpkg','--print-architecture').strip()!='amd64' or not run('dpkg-query','-W','-f=${Version}','openmediavault').startswith('8.'):
        p.error('Requires OMV 8 / Debian 13 / amd64.')
    if args.check:
        print('Compatible / Compatível: OMV 8 / Debian 13 / amd64');return
    if os.geteuid()!=0:p.error('Use sudo / Execute com sudo')
    manifest=ROOT/'SHA256SUMS'
    if not manifest.exists():p.error('Use a packaged release with SHA256SUMS. Run scripts/build_release.py first.')
    for line in manifest.read_text().splitlines():
        digest,name=line.split('  ',1);target=ROOT/name
        if not target.resolve().is_relative_to(ROOT) or target.is_symlink():p.error('Invalid release path')
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:p.error('Release checksum failed / Verificação falhou')
    if args.update:
        spec=importlib.util.spec_from_file_location('nas_setup_updater',Path(__file__).resolve().with_name('update.py'))
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        try:output=updater.update_release(ROOT,destination=Path('/opt/nas-setup'),state=Path('/var/lib/nas-setup'),conf=Path('/etc/nas-setup'),run=run)
        except updater.UpdateError as exc:p.error(str(exc))
        ensure_manager_account(p)
        output=run('/opt/nas-setup/venv/bin/nas-setup','reopen')
        print('Updated NAS Setup; existing jobs and NAS configuration were preserved. / NAS Setup atualizado.')
        print(output)
        return
    if Path('/opt/nas-setup').exists():p.error('Already installed. Use --update to upgrade, or sudo nas-setup reopen / Já instalado.')
    if not 1024<=args.port<=65535:p.error('Port must be between 1024 and 65535')
    addresses={a['local'] for i in json.loads(run('ip','-j','address','show')) for a in i.get('addr_info',[]) if a['family']=='inet'}
    address=args.address
    if not address:
        candidates=[a for a in addresses if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback]
        if len(candidates)!=1:p.error('Choose the NAS LAN address with --address / Escolha o IP local com --address')
        address=candidates[0]
    ip=ipaddress.ip_address(address)
    if address not in addresses or ip.version!=4 or not ip.is_private or ip.is_loopback:p.error('Use an assigned private IPv4 LAN address')
    with socket.socket() as probe:
        try:probe.bind((address,args.port))
        except OSError:p.error('Port occupied / Porta ocupada')
    # The web service must traverse the runtime even when root uses umask 0077.
    # Private state, TLS keys and service processes retain explicit restrictive modes.
    os.umask(0o022)
    print('Installing dependencies / Instalando dependências…',flush=True)
    run('apt-get','update')
    run('apt-get','-o','DPkg::Lock::Timeout=60','--no-remove','--yes','install','python3-venv','openssl','ca-certificates')
    try:pwd.getpwnam('nas-setup')
    except KeyError:run('useradd','--system','--user-group','--home-dir','/var/lib/nas-setup/web','--shell','/usr/sbin/nologin','nas-setup')
    user=pwd.getpwnam('nas-setup')
    destination=Path('/opt/nas-setup');destination.mkdir(mode=0o755)
    shutil.copytree(ROOT,destination/'source')
    for path in (destination/'source').rglob('*'):
        if path.is_file():path.chmod(0o644)
        elif path.is_dir():path.chmod(0o755)
    run('python3','-m','venv',str(destination/'venv'))
    pip=str(destination/'venv/bin/pip')
    run(pip,'install','-r',str(destination/'source/requirements.lock'))
    run(pip,'install','--no-deps',str(destination/'source'))
    root=Path('/var/lib/nas-setup');root.mkdir(mode=0o755)
    for name,uid,gid in [('web',user.pw_uid,user.pw_gid),('worker',0,0)]:
        directory=root/name;directory.mkdir(mode=0o700);os.chown(directory,uid,gid)
    conf=Path('/etc/nas-setup');conf.mkdir(mode=0o750);os.chown(conf,0,user.pw_gid)
    origin=f'https://{address}:{args.port}'
    (conf/'config.json').write_text(json.dumps({'address':address,'port':args.port,'origin':origin}))
    run('openssl','req','-x509','-newkey','rsa:3072','-sha256','-days','365','-nodes',
        '-keyout',str(conf/'server.key'),'-out',str(conf/'server.crt'),'-subj','/CN=NAS Setup',
        '-addext',f'subjectAltName=IP:{address}')
    os.chown(conf/'server.key',0,user.pw_gid);(conf/'server.key').chmod(0o640)
    (conf/'server.crt').chmod(0o644)
    web='''[Unit]
Description=NAS Setup browser interface
After=network-online.target nas-setup-worker.service
Wants=network-online.target
Requires=nas-setup-worker.service
[Service]
User=nas-setup
Group=nas-setup
ExecStart=/opt/nas-setup/venv/bin/nas-setup web
Restart=on-failure
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/nas-setup/web
RestrictAddressFamilies=AF_INET AF_UNIX
[Install]
WantedBy=multi-user.target
'''
    worker='''[Unit]
Description=NAS Setup installation worker
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
Group=nas-setup
ExecStart=/opt/nas-setup/venv/bin/nas-setup worker
Restart=on-failure
UMask=0077
RuntimeDirectory=nas-setup
RuntimeDirectoryMode=0750
[Install]
WantedBy=multi-user.target
'''
    for name,content in [('web',web),('worker',worker)]:Path('/etc/systemd/system/nas-setup-'+name+'.service').write_text(content)
    Path('/usr/local/bin/nas-setup').symlink_to('/opt/nas-setup/venv/bin/nas-setup')
    run('systemctl','daemon-reload')
    ensure_manager_account(p)
    output=run('/opt/nas-setup/venv/bin/nas-setup','reopen')
    print(output)
    print('HTTPS certificate fingerprint / Impressão digital do certificado HTTPS:')
    print(run('openssl','x509','-in',str(conf/'server.crt'),'-noout','-fingerprint','-sha256'))
    print('Open the address above. Compare the certificate fingerprint before trusting the certificate.\nAbra o endereço acima. Confira a impressão digital antes de aceitar o certificado.')

if __name__=='__main__':main()
