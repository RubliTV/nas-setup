"""OMV 8 storage adapter. Mutations remain disabled pending disposable-VM validation.

RPC contracts audited at OMV e0c7b63bf55064eaa6a5e476800465d27e9b86a5.
Source/schema inspection and unit tests are not a provisioning certification.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from .models import SetupError

ADAPTER_VALIDATED = False
ADAPTER_VERSION = 'omv8-v02-e0c7b63'
VALIDATED_PACKAGES = {'openmediavault':'8.5.12-1','openmediavault-md':'8.1.6-1'}
BRIDGE = Path(__file__).with_name('omv_bridge.php')
NEW_UUID = 'new'
SMB_SETTINGS_KEYS = ('enable','workgroup','serverstring','loglevel','usesendfile','aio','timeserver','winssupport','winsserver','homesenable','homesbrowseable','extraoptions')


def classify_disks(tree, aliases, holders):
    """Fail closed using the complete lsblk dependency tree, not name prefixes."""
    nodes,edges={},{}
    def collect(node,parent=None):
        name=node.get('name')
        if not name or not name.startswith('/dev/'):raise ValueError('invalid block topology')
        if name in nodes:
            # Repeated MD nodes under distinct members are normal in lsblk.
            if nodes[name].get('type')!=node.get('type'):raise ValueError('ambiguous topology')
        else:nodes[name]=node
        edges.setdefault(name,set())
        if parent:edges[parent].add(name)
        for child in node.get('children',[]):collect(child,name)
    for node in tree['blockdevices']:collect(node)
    def descendants(name,seen=None):
        seen=set() if seen is None else set(seen)
        if name in seen:raise ValueError('cyclic topology')
        seen.add(name);out={name}
        for child in edges[name]:out|=descendants(child,seen)
        return out
    def mounts(node):
        return {x for x in (node.get('mountpoints') or [node.get('mountpoint')]) if x}
    root_known=any('/' in mounts(n) for n in nodes.values())
    identities={};serials={}
    for name,node in nodes.items():
        if node.get('type')!='disk':continue
        identity=sorted(a for a in aliases.get(name,[]) if re.fullmatch(r'/dev/disk/by-id/[A-Za-z0-9_.:+-]+',a) and not re.search(r'-part\d+$',a))
        identities[name]=identity[0] if identity else None
        serial=(node.get('wwn') or node.get('serial') or '').strip()
        serials[name]=serial
    result=[]
    for name,identity in identities.items():
        node=nodes[name];related=descendants(name);related_nodes=[nodes[n] for n in related]
        allmounts=set().union(*(mounts(n) for n in related_nodes))
        reason=None
        if not root_known:reason='topology_incomplete'
        elif any(m in ('/','/boot','/boot/efi','[SWAP]') or m.startswith(('/var','/usr','/opt')) for m in allmounts):reason='system_disk'
        elif allmounts:reason='mounted'
        elif any(holders.get(n) for n in related):reason='active_holder'
        elif any(n.get('type') not in ('disk','part') for n in related_nodes):reason='active_holder'
        elif node.get('ro') or node.get('rm'):reason='read_only_or_removable'
        elif not identity or not serials[name]:reason='missing_identity'
        elif list(identities.values()).count(identity)>1 or list(serials.values()).count(serials[name])>1:reason='duplicate_identity'
        elif node.get('tran') not in ('sata','sas','nvme','usb'):reason='unknown_transport'
        elif not isinstance(node.get('size'),int) or node['size']<=0:reason='topology_incomplete'
        signatures=sorted({str(n['fstype'])+(':'+str(n['uuid']) if n.get('uuid') else '') for n in related_nodes if n.get('fstype')})
        topology=[{k:n.get(k) for k in ('name','type','size','fstype','uuid','partuuid','parttype','pttype','ro','rm')} for n in sorted(related_nodes,key=lambda n:n['name'])]
        topology_hash=hashlib.sha256(json.dumps(topology,sort_keys=True).encode()).hexdigest()
        result.append({'topology':topology_hash,'id':identity or 'unavailable:'+name,'device':name,'devicefile':name,'model':(node.get('model') or '').strip(),
            'serial':serials[name],'size_bytes':node.get('size',0),'transport':node.get('tran') or 'unknown',
            'eligible':reason is None,'reason':reason,'signatures':signatures,
            'logical_sector':node.get('log-sec'),'physical_sector':node.get('phy-sec')})
    return result


def explicit_privileges(rows):
    return [{k:p[k] for k in ('type','name','perms')} for p in rows if p.get('perms') is not None]


def share_permissions(identity,permissions):
    perms={'rw':7,'ro':5,'none':0}
    users=[{'name':name,'perms':perms[level]} for name,level in sorted(permissions.items())]
    return ({'uuid':identity,'privileges':[dict(u,type='user') for u in users]},
            {'uuid':identity,'file':'','recursive':False,'replace':True,'owner':'root','group':'root',
             'userperms':7,'groupperms':0,'otherperms':0,'users':users,'groups':[]})


def raid_health(action,degraded,completed,tolerance):
    try:
        if int(degraded)>0:return {'state':'degraded','progress':0,'tolerance':tolerance}
        if action=='idle':return {'state':'healthy','progress':100,'tolerance':tolerance}
        if action in ('resync','recover','reshape','check','repair'):
            if completed=='none':return {'state':'syncing','progress':0,'tolerance':tolerance}
            done,total=(int(n.strip()) for n in completed.split('/'))
            return {'state':'syncing','progress':min(99,max(0,int(done*100/total))),'tolerance':tolerance}
    except (ValueError,ZeroDivisionError):pass
    return {'state':'degraded','progress':0,'tolerance':tolerance}


class OmvProvisioner:
    def __init__(self,system):
        self.system=system
        self._config_digest=None
        self._context_path=None

    def _require_validated(self):
        if not ADAPTER_VALIDATED:raise SetupError('provisioning_unvalidated')

    def _rpc(self,operation,params=None):
        # Never use System.run: it logs command output and has no stdin channel.
        # Error details from OMV can contain input credentials; discard them.
        request=json.dumps({'operation':operation,'params':params})
        try:
            result=subprocess.run(['/usr/bin/php8.4','-c','/etc/openmediavault',str(BRIDGE)],
                input=request,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=1800,
                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8'})
            if result.returncode:raise SetupError('omv_rpc_failed')
            response=json.loads(result.stdout)
            if not response.get('ok'):raise SetupError('omv_rpc_failed')
            return response['result']
        except (OSError,ValueError,subprocess.TimeoutExpired):raise SetupError('omv_rpc_failed') from None

    def _discover(self):
        data=json.loads(self.system.run(['lsblk','--json','--bytes','--paths','--output',
            'NAME,KNAME,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RO,RM,MOUNTPOINTS,FSTYPE,UUID,PARTUUID,PARTTYPE,PTTYPE,LOG-SEC,PHY-SEC']).stdout)
        aliases={}
        for path in Path('/dev/disk/by-id').glob('*'):
            if path.is_symlink():aliases.setdefault(str(path.resolve()),[]).append(str(path))
        holders={}
        for path in Path('/sys/class/block').glob('*'):
            holders['/dev/'+path.name]=[p.name for p in (path/'holders').glob('*')]
        return data,classify_disks(data,aliases,holders)

    def inventory(self):
        try:_,disks=self._discover()
        except (SetupError,OSError,ValueError,KeyError):disks=[]
        return {'disks':disks,'provisioning_enabled':ADAPTER_VALIDATED,
                'provisioning_reason':None if ADAPTER_VALIDATED else 'provisioning_unvalidated'}

    def prepare(self,selection,plan):
        self._require_validated()
        from .setup_models import SetupSelection
        selection=SetupSelection.model_validate(selection).model_dump()
        if selection!=plan.get('selection'):raise SetupError('storage_changed')
        if not self.system.inventory().get('supported'):raise SetupError('unsupported_host')
        if self._rpc('config_dirty',{'modules':[]}):raise SetupError('omv_configuration_changed')
        self._preflight_conflicts(selection)
        raid=selection['storage']['mode'].startswith('raid')
        packages=['acl','samba','e2fsprogs','gdisk']+(['openmediavault-md','mdadm'] if raid else [])
        self.system.run(['apt-get','-o','DPkg::Lock::Timeout=60','--no-remove','--yes','install',*packages],timeout=1800)
        self._verify_packages(raid)
        verify_schemas(raid=raid)
        for command in ('lsblk','findmnt','getfacl','getent','id','testparm'):
            self.system.run(['/usr/bin/which',command])
        if raid:self._rpc('md_candidates')
        self._config_digest=self._digest()

    def _verify_packages(self,raid):
        for package,version in VALIDATED_PACKAGES.items():
            if package=='openmediavault-md' and not raid:continue
            installed=self.system.run(['dpkg-query','--show','--showformat=${db:Status-Status}\t${Version}',package],check=False)
            if installed.returncode or installed.stdout.strip()!='installed\t'+version:
                raise SetupError('provisioning_unvalidated')

    def _preflight_conflicts(self,selection):
        for user in selection['users']:
            result=self.system.run(['getent','passwd',user['name']],check=False)
            if result.returncode==0:raise SetupError('existing_installation')
            if result.returncode!=2:raise SetupError('command_failed')
        if not selection['shares']:return
        existing=self._db('conf.system.sharedfolder')
        if any(s['name'].casefold()==e['name'].casefold() for s in selection['shares'] for e in existing):
            raise SetupError('existing_installation')
        if selection['storage']['mode']=='existing':
            volume=self._mounted(selection['storage']['volume'])
            for share in selection['shares']:
                relative='nas-'+(share['folder'] if share['folder']!='custom' else hashlib.sha256(share['name'].encode()).hexdigest()[:16])
                safe_share_path(volume['path'],relative,must_be_new=True)
        if self._rpc('smb_settings_get').get('extraoptions','').strip():raise SetupError('smb_configuration_conflict')

    def _check_target(self,expected,blank=False):
        _,disks=self._discover()
        matches=[d for d in disks if d['id']==expected['id']]
        if len(matches)!=1:raise SetupError('storage_changed')
        current=matches[0]
        for key in ('id','serial','size_bytes','transport','logical_sector','physical_sector'):
            if current.get(key)!=expected.get(key):raise SetupError('storage_changed')
        if not blank and current.get('topology')!=expected.get('topology'):raise SetupError('storage_changed')
        if blank:
            tree,_=self._discover()
            node=next((n for n in tree['blockdevices'] if n['name']==current['devicefile']),None)
            if not node or node.get('children'):raise SetupError('storage_changed')
        if not current['eligible'] or current['signatures']!=([] if blank else expected['signatures']):
            raise SetupError('storage_changed')
        return current['id']

    def _db(self,model):
        if model not in ('conf.system.filesystem.mountpoint','conf.system.sharedfolder','conf.service.smb.share'):
            raise SetupError('operation_invalid')
        return json.loads(self.system.run(['omv-confdbadm','read',model]).stdout)

    def _apply(self):
        # Exact configuration snapshot prevents applying known unrelated edits.
        # OMV serializes its own applyChanges while deploying generated config.
        self._mutate('config_apply',{'modules':[],'force':False})
        if self._rpc('config_dirty',{'modules':[]}):raise SetupError('omv_configuration_changed')
        return {'applied':True}

    def _verify_user(self,name,owner):
        entry=self.system.run(['getent','passwd',name],check=False)
        fields=entry.stdout.strip().split(':')
        if entry.returncode or len(fields)!=7 or fields[0]!=name or fields[4]!='nas-setup-'+owner or fields[6]!='/usr/sbin/nologin':
            raise SetupError('existing_installation')
        try:uid=int(fields[2])
        except ValueError:raise SetupError('existing_installation') from None
        groups=set(self.system.run(['id','-nG',name]).stdout.split())
        if uid<1000 or groups!={'users'}:raise SetupError('existing_installation')
        return {'name':name,'uid':uid}

    def _user(self,name,password,owner,journal):
        key='user.'+name
        def create():
            lookup=self.system.run(['getent','passwd',name],check=False)
            if lookup.returncode==0:raise SetupError('existing_installation')
            if lookup.returncode!=2:raise SetupError('command_failed')
            if not password or '\n' in password or '\r' in password:raise SetupError('credentials_required')
            self._mutate('user_set',{'name':name,'groups':[],'shell':'/usr/sbin/nologin','password':password,
                'email':'','comment':'nas-setup-'+owner,'disallowusermod':True,'sshpubkeys':[]})
            return self._verify_user(name,owner)
        def verify(record):
            # Creation can fail after creating passwd entry but before setting
            # its password. A marker alone cannot establish a usable account.
            if not record.get('result'):return None
            current=self._verify_user(name,owner)
            return current if current==record['result'] else None
        return journal.perform(key,create,verify)

    def _mounted(self,identity):
        matches=[v for v in self.system.inventory().get('volumes',[]) if v['id']==identity]
        if len(matches)!=1:raise SetupError('volume_missing')
        volume=matches[0];base=Path(volume['path'])
        if not str(base).startswith('/srv/') or not base.is_dir() or base.resolve()!=base:
            raise SetupError('unsafe_storage')
        if volume.get('fstype')!='ext4':raise SetupError('omv_filesystem_unsupported')
        configs=[m for m in self._db('conf.system.filesystem.mountpoint') if m['dir']==str(base)]
        if len(configs)!=1:raise SetupError('volume_missing')
        mounted=json.loads(self.system.run(['findmnt','--json','--mountpoint',str(base),'--output','TARGET,SOURCE,UUID,OPTIONS']).stdout)['filesystems']
        if len(mounted)!=1 or mounted[0].get('uuid')!=identity or 'rw' not in mounted[0].get('options','').split(','):
            raise SetupError('volume_missing')
        if str(Path(configs[0]['fsname']).resolve())!=str(Path(mounted[0]['source']).resolve()):raise SetupError('storage_changed')
        return {**volume,'mount_uuid':configs[0]['uuid']}

    def status(self,resources):
        volume=self._mounted(resources['volume'])
        if resources.get('mount_path') and volume['path']!=resources['mount_path']:raise SetupError('storage_changed')
        for share in resources.get('shares',[]):self._verify_share(share,volume)
        for user in resources.get('users',[]):
            if self._verify_user(user['name'],resources['owner'])!=user:raise SetupError('existing_installation')
        md=resources.get('md')
        if not md:
            if resources.get('targets'):self._verify_single(resources['targets'],resources['volume'],volume['path'])
            return {'state':'healthy','progress':100,'tolerance':0}
        base=Path('/sys/class/block')/Path(md['devicefile']).name/'md'
        try:
            degraded=(base/'degraded').read_text().strip()
            health=raid_health((base/'sync_action').read_text().strip(),degraded,
                (base/'sync_completed').read_text().strip(),resources['raid']['tolerance'])
        except OSError:degraded='1';health={'state':'degraded','progress':0,'tolerance':resources['raid']['tolerance']}
        self._verify_array(md,resources.get('targets',[]),allow_mounted=True,allow_degraded=degraded!='0')
        return health

    def _check_target_identity(self,expected,allowed_reasons=(None,)):
        _,disks=self._discover();matches=[d for d in disks if d['id']==expected['id']]
        if len(matches)!=1 or matches[0]['reason'] not in allowed_reasons:raise SetupError('storage_changed')
        current=matches[0]
        if any(current.get(k)!=expected.get(k) for k in ('serial','size_bytes','transport','logical_sector','physical_sector')):
            raise SetupError('storage_changed')
        return current

    def review_recovery(self,plan,journal):
        """Read-only ownership check for a fresh, session-bound resume consent."""
        checkpoint=journal.directory/'omv-config-checkpoint.json'
        if checkpoint.exists():
            if checkpoint.is_symlink() or json.loads(checkpoint.read_text()).get('digest')!=self._digest():
                raise SetupError('omv_configuration_changed')
        selected=plan['selection'];storage=selected['storage'];targets=plan.get('targets',[])
        keys=['array.create','filesystem.dispatch','filesystem.result','mount.create','mount.apply','sharing.apply','smb.enable']
        keys+=['user.'+u['name'] for u in selected.get('users',[])]
        for target in targets:
            if target.get('serial'):keys+=['wipe.'+target['serial']+'.'+suffix for suffix in ('dispatch','result')]
        for share in selected.get('shares',[]):
            relative='nas-'+(share['folder'] if share['folder']!='custom' else hashlib.sha256(share['name'].encode()).hexdigest()[:16])
            keys+=['share.'+relative+'.'+suffix for suffix in ('create','privileges','acl.dispatch','acl.result','smb')]
        records={key:journal.get(key) for key in keys}
        if any(r and (r['status']!='completed' or r.get('result') is None) for r in records.values()):
            raise SetupError('recovery_required')
        if storage['mode']=='existing':self._mounted(storage['volume']);return
        md=records.get('array.create');fs=records.get('filesystem.result');mount=records.get('mount.create')
        if md:self._verify_array(md['result'],targets,allow_mounted=bool(mount))
        if fs:
            target=md['result']['devicefile'] if md else targets[0]['id']
            if not md:self._check_target_identity(targets[0],allowed_reasons=(None,'mounted') if mount else (None,))
            actual=self._filesystem_for(target)
            if not actual or any(actual.get(k)!=fs['result'].get(k) for k in ('uuid','devicefile','type')):
                raise SetupError('recovery_required')
            if mount:
                actual_mount=self._mounted(fs['result']['uuid'])
                if actual_mount['mount_uuid']!=mount['result']['uuid']:raise SetupError('recovery_required')
            return
        # Known async handle without a committed resulting filesystem is safe
        # from repetition, but cannot yet prove ownership for a new consent.
        if records.get('filesystem.dispatch'):raise SetupError('recovery_required')
        if md:return
        for target in targets:
            key='wipe.'+target['serial']
            wiped=records.get(key+'.result')
            if records.get(key+'.dispatch') and not wiped:raise SetupError('recovery_required')
            self._check_target(target,blank=bool(wiped))

    def _start_context(self,journal):
        self._context_path=journal.directory/'omv-config-checkpoint.json'
        if self._context_path.is_symlink():raise SetupError('unsafe_storage')
        actual=self._digest()
        if self._context_path.exists():
            saved=json.loads(self._context_path.read_text())
            if saved.get('digest')!=actual:raise SetupError('omv_configuration_changed')
        elif self._rpc('config_dirty',{'modules':[]}):raise SetupError('omv_configuration_changed')
        self._config_digest=actual
        self._save_context()

    def _save_context(self):
        if self._context_path is None:return
        temp=self._context_path.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump({'digest':self._config_digest},stream);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self._context_path)
            fd=os.open(self._context_path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if temp.exists():temp.unlink()

    def _verify_single(self,targets,identity,mount_path):
        if len(targets)!=1:raise SetupError('storage_changed')
        target=targets[0];tree,disks=self._discover()
        matches=[d for d in disks if d['id']==target['id']]
        if len(matches)!=1:raise SetupError('storage_changed')
        current=matches[0]
        if current['reason']!='mounted':raise SetupError('storage_changed')
        for key in ('serial','size_bytes','transport','logical_sector','physical_sector'):
            if current.get(key)!=target.get(key):raise SetupError('storage_changed')
        fs=self._filesystem_for(target['id'])
        if not fs or fs['uuid']!=identity:raise SetupError('storage_changed')
        node=next((n for n in tree['blockdevices'] if n['name']==current['devicefile']),None)
        if not node or len(node.get('children',[]))!=1:raise SetupError('storage_changed')
        child=node['children'][0]
        if child.get('children') or [p for p in child.get('mountpoints',[]) if p]!=[mount_path]:raise SetupError('storage_changed')

    def _verify_acl(self,path,permissions):
        target=Path(path)
        if target.resolve()!=target or not target.is_dir():raise SetupError('unsafe_storage')
        output=self.system.run(['getfacl','--omit-header','--absolute-names','--',str(target)]).stdout
        actual={line.strip() for line in output.splitlines() if line.strip() and not line.startswith('#')}
        mask='rwx' if 'rw' in permissions.values() else 'r-x'
        expected={'user::rwx','group::---','other::---','mask::'+mask}
        for name,level in permissions.items():expected.add('user:'+name+':'+{'rw':'rwx','ro':'r-x','none':'---'}[level])
        expected|={'default:'+line for line in set(expected)}
        if actual!=expected:raise SetupError('share_permissions_changed')

    def _verify_share(self,share,volume):
        path=safe_share_path(volume['path'],share['relative_path'])
        if str(path)!=share['path'] or not path.is_dir():raise SetupError('storage_changed')
        obj=self._rpc('share_get',{'uuid':share['uuid']})
        expected={'uuid':share['uuid'],'name':share['name'],'mntentref':volume['mount_uuid'],
                  'reldirpath':share['relative_path']+'/','comment':share['comment']}
        if any(obj.get(k)!=v for k,v in expected.items()):raise SetupError('existing_installation')
        desired,_=share_permissions(share['uuid'],share['permissions'])
        actual=explicit_privileges(self._rpc('privileges_get',{'uuid':share['uuid']}))
        # OMV includes all users/groups, numeric IDs, and null unset privileges.
        if sorted(actual,key=lambda p:(p['type'],p['name']))!=sorted(desired['privileges'],key=lambda p:(p['type'],p['name'])):
            raise SetupError('share_permissions_changed')
        if path.stat().st_uid!=0 or path.stat().st_gid!=0:raise SetupError('share_permissions_changed')
        self._verify_acl(str(path),share['permissions'])
        if share.get('smb_uuid'):
            smb=self._rpc('smb_share_get',{'uuid':share['smb_uuid']})
            for key,value in self._smb_params(share,share['smb_uuid']).items():
                if smb.get(key)!=value:raise SetupError('share_permissions_changed')
        return share

    def _smb_params(self,share,identity):
        permissions=share['permissions']
        allowed=' '.join(n for n,p in sorted(permissions.items()) if p!='none')
        denied=' '.join(n for n,p in sorted(permissions.items()) if p=='none')
        writable=' '.join(n for n,p in sorted(permissions.items()) if p=='rw')
        readonly=' '.join(n for n,p in sorted(permissions.items()) if p=='ro')
        options=['valid users = '+allowed,'write list = '+writable,'read list = '+readonly,
                 'inherit owner = yes','nt acl support = no']
        if denied:options.append('invalid users = '+denied)
        return {'uuid':identity,'enable':True,'sharedfolderref':share['uuid'],'comment':share['comment'],
            'guest':'no','readonly':False,'browseable':True,'recyclebin':False,'recyclemaxsize':0,'recyclemaxage':0,
            'hidedotfiles':True,'inheritacls':True,'inheritpermissions':False,'easupport':False,'storedosattributes':False,
            'hostsallow':'','hostsdeny':'','audit':False,'timemachine':False,'extraoptions':'\n'.join(options)}

    def _share(self,selected,volume,owner,journal):
        relative='nas-'+(selected['folder'] if selected['folder']!='custom' else hashlib.sha256(selected['name'].encode()).hexdigest()[:16])
        key='share.'+relative;comment='nas-setup-'+owner
        base={'name':selected['name'],'folder':selected['folder'],'relative_path':relative,
            'path':str(safe_share_path(volume['path'],relative)),'permissions':selected['permissions'],'comment':comment}
        def create():
            safe_share_path(volume['path'],relative,must_be_new=True)
            for existing in self._db('conf.system.sharedfolder'):
                if existing['name'].casefold()==selected['name'].casefold() or (existing['mntentref']==volume['mount_uuid'] and existing['reldirpath'].strip('/')==relative):
                    raise SetupError('existing_installation')
            obj=self._mutate('share_set',{'uuid':NEW_UUID,'name':selected['name'],'mntentref':volume['mount_uuid'],
                'reldirpath':relative,'comment':comment,'mode':'700'})
            return {**base,'uuid':obj['uuid']}
        def verify_created(record):
            saved=record.get('result')
            if not saved:return None
            obj=self._rpc('share_get',{'uuid':saved['uuid']})
            if obj.get('comment')!=comment or obj.get('mntentref')!=volume['mount_uuid'] or obj.get('reldirpath')!=relative+'/':return None
            safe_share_path(volume['path'],relative)
            return saved
        shared=journal.perform(key+'.create',create,verify_created)
        privileges,acl=share_permissions(shared['uuid'],shared['permissions'])
        def verify_privileges(record):
            actual=explicit_privileges(self._rpc('privileges_get',{'uuid':shared['uuid']}))
            return {'set':True} if sorted(actual,key=lambda p:(p['type'],p['name']))==sorted(privileges['privileges'],key=lambda p:(p['type'],p['name'])) else None
        journal.perform(key+'.privileges',lambda:self._mutate('privileges_set',privileges) or {'set':True},verify_privileges)
        task=journal.perform(key+'.acl.dispatch',lambda:{'handle':self._mutate('acl_set',acl)})
        def acl_finish():
            self._wait_bg(task['handle']);self._verify_acl(shared['path'],shared['permissions']);return {'set':True}
        def acl_verify(record):
            self._verify_acl(shared['path'],shared['permissions']);return {'set':True}
        journal.perform(key+'.acl.result',acl_finish,acl_verify)
        def smb_create():
            if any(s['sharedfolderref']==shared['uuid'] for s in self._db('conf.service.smb.share')):raise SetupError('existing_installation')
            obj=self._mutate('smb_share_set',self._smb_params(shared,NEW_UUID))
            return {'uuid':obj['uuid']}
        def smb_verify(record):
            saved=record.get('result')
            if not saved:return None
            obj=self._rpc('smb_share_get',{'uuid':saved['uuid']})
            return saved if all(obj.get(k)==v for k,v in self._smb_params(shared,saved['uuid']).items()) else None
        smb=journal.perform(key+'.smb',smb_create,smb_verify)
        shared['smb_uuid']=smb['uuid']
        return self._verify_share(shared,volume)

    def _verify_array(self,expected,targets,allow_mounted=False,allow_degraded=False):
        arrays=[a for a in self._rpc('md_list') if a['uuid']==expected['uuid']]
        if len(arrays)!=1:raise SetupError('storage_changed')
        array=arrays[0]
        if array['level']!=expected['level'] or array['devicefile']!=expected['devicefile'] or int(array['numdevices'])!=len(targets):
            raise SetupError('storage_changed')
        members={str(Path(t['id']).resolve()) for t in targets}
        actual={str(Path(d).resolve()) for d in array['devices']}
        if (not allow_degraded and members!=actual) or not actual.issubset(members):raise SetupError('storage_changed')
        tree,disks=self._discover();current={d['id']:d for d in disks}
        array_path=str(Path(array['devicefile']).resolve())
        def inspect(node):
            # Fresh arrays are built directly on the selected whole disks. Any
            # extra partition/holder or system mount is not our expected stage.
            if node['name'] not in members|{array_path}:raise SetupError('storage_changed')
            mounts=[m for m in (node.get('mountpoints') or []) if m]
            if mounts and (not allow_mounted or any(not m.startswith('/srv/') for m in mounts)):raise SetupError('storage_changed')
            for child in node.get('children',[]):inspect(child)
        for target in targets:
            d=current.get(target['id'])
            if not d and allow_degraded:continue
            if not d:raise SetupError('storage_changed')
            for key in ('serial','size_bytes','transport','logical_sector','physical_sector'):
                if d.get(key)!=target.get(key):raise SetupError('storage_changed')
            if d['transport'] not in ('sata','sas','nvme'):raise SetupError('disk_transport')
            node=next((n for n in tree['blockdevices'] if n['name']==d['devicefile']),None)
            if not node:raise SetupError('storage_changed')
            detached=str(Path(target['id']).resolve()) not in actual
            if allow_degraded and detached:
                if d['reason'] is not None or node.get('children'):raise SetupError('storage_changed')
                continue
            if d['reason'] not in ('active_holder','mounted'):raise SetupError('storage_changed')
            inspect(node)
        return expected

    def _raid(self,mode,targets,owner,journal):
        existing=journal.get('array.create')
        if existing:
            result=existing.get('result')
            if not result:raise SetupError('recovery_required')
            return self._verify_array(result,targets,allow_mounted=True)
        def guard():
            for target in targets:
                if target['transport'] not in ('sata','sas','nvme'):raise SetupError('disk_transport')
                self._check_target(target,blank=bool(journal.get('wipe.'+target['serial']+'.result')))
        for target in targets:
            key='wipe.'+target['serial']
            def dispatch(target=target):
                guard()
                return {'handle':self._mutate('disk_wipe',{'devicefile':target['id'],'secure':False})}
            task=journal.perform(key+'.dispatch',dispatch,destructive=True)
            def finished(target=target,task=task):
                self._wait_bg(task['handle']);self._check_target(target,blank=True);return {'id':target['id']}
            def verify(record,target=target):
                self._check_target(target,blank=True);return {'id':target['id']}
            journal.perform(key+'.result',finished,verify)
        def create():
            guard()
            obj=self._mutate('md_create',{'devices':[t['id'] for t in targets],'name':'nas'+owner[:12],
                'level':{'raid1':'mirror','raid5':'raid5','raid6':'raid6'}[mode]})
            matches=[a for a in self._rpc('md_list') if a['devicefile']==obj['devicefile']]
            if len(matches)!=1:raise SetupError('recovery_required')
            if matches[0]['level']!=mode:raise SetupError('recovery_required')
            record={k:matches[0][k] for k in ('uuid','devicefile','level','devices','numdevices')}
            return self._verify_array(record,targets)
        return journal.perform('array.create',create,destructive=True)

    def _mount_new(self,filesystem,owner,journal):
        def create():
            # OMV itself rejects an existing mount registration for this UUID.
            return self._mutate('mount_set',{'id':filesystem['uuid'],'comment':'nas-setup-'+owner,'usagewarnthreshold':90})
        def verify(record):
            saved=record.get('result')
            if not saved:return None
            matches=[m for m in self._db('conf.system.filesystem.mountpoint') if m['uuid']==saved['uuid']]
            return saved if len(matches)==1 and all(matches[0].get(k)==saved.get(k) for k in ('dir','fsname','type','comment')) else None
        mount=journal.perform('mount.create',create,verify)
        journal.perform('mount.apply',self._apply,lambda record: {'applied':True} if self._mounted(filesystem['uuid'])['mount_uuid']==mount['uuid'] else None)
        return self._mounted(filesystem['uuid'])

    def _enable_smb(self,journal):
        def enable():
            settings=self._rpc('smb_settings_get')
            if settings.get('extraoptions','').strip():raise SetupError('smb_configuration_conflict')
            wanted={k:settings[k] for k in SMB_SETTINGS_KEYS};wanted['enable']=True
            self._mutate('smb_settings_set',wanted)
            return wanted
        def verify(record):
            expected=record.get('result')
            if not expected:return None
            current=self._rpc('smb_settings_get')
            return expected if all(current.get(k)==v for k,v in expected.items()) else None
        journal.perform('smb.enable',enable,verify)

    def provision(self,selection,plan,journal,passwords):
        self._require_validated()
        from .setup_models import SetupSelection
        selected=SetupSelection.model_validate(selection).model_dump()
        if selected!=plan.get('selection'):raise SetupError('storage_changed')
        self._verify_packages(selected['storage']['mode'].startswith('raid'))
        self._start_context(journal)
        owner=journal.perform('adapter.owner',lambda:{'id':uuid.uuid4().hex})['id']
        storage=selected['storage'];targets=plan.get('targets',[]);md=None
        if storage['mode']=='existing':volume=self._mounted(storage['volume'])
        else:
            if {t['id'] for t in targets}!=set(storage['disks']):raise SetupError('storage_changed')
            if storage['mode']=='single':
                if len(targets)!=1:raise SetupError('disk_count')
                target=targets[0]['id'];guard=lambda:self._check_target(targets[0])
            else:
                md=self._raid(storage['mode'],targets,owner,journal)
                target=md['devicefile'];guard=lambda:self._verify_array(md,targets)
            filesystem=self._format(target,journal,guard)
            volume=self._mount_new(filesystem,owner,journal)
        users=[]
        for user in selected['users']:
            name=user['name'];users.append(self._user(name,passwords.get(name),owner,journal))
        shares=[self._share(share,volume,owner,journal) for share in selected['shares']]
        if shares:self._enable_smb(journal)
        if users or shares:
            journal.perform('sharing.apply',self._apply,lambda record:self._verify_deployed(shares,volume))
            self._verify_deployed(shares,volume)
        resources={'volume':volume['id'],'mount_path':volume['path'],'mount_uuid':volume['mount_uuid'],
            'owner':owner,'users':users,'shares':shares,'targets':targets,'md':md,
            'media_path':next((s['path'] for s in shares if s['folder']=='media'),None),
            'raid':{'state':'healthy','progress':100,'tolerance':plan.get('tolerance',0)}}
        resources['raid']=self.status(resources)
        return resources

    def _verify_deployed(self,shares,volume):
        if self._rpc('config_dirty',{'modules':[]}):raise SetupError('omv_configuration_changed')
        for share in shares:self._verify_share(share,volume)
        if shares:
            self.system.run(['testparm','--suppress-prompt','--verbose'],timeout=30)
            self.system.run(['systemctl','is-active','smbd'],timeout=30)
        return {'applied':True}

    def _wait_bg(self,handle):
        if not isinstance(handle,str) or not re.fullmatch(r'/tmp/[A-Za-z0-9_.@-]+',handle):
            raise SetupError('recovery_required')
        end=time.monotonic()+3600;pos=0
        while time.monotonic()<end:
            result=self._rpc('exec_output',{'filename':handle,'pos':pos,'length':4096})
            if not isinstance(result,dict) or not isinstance(result.get('running'),bool):raise SetupError('omv_rpc_failed')
            pos=result['pos']
            # Deliberately discard output; it may contain credentials or private paths.
            if not result['running'] and not result.get('pendingOutput'):
                # OMV8.5.12 predates the upstream settle added after wipe/mkfs.
                self.system.run(['udevadm','settle','--timeout=30'],timeout=35)
                return
            time.sleep(1)
        raise SetupError('command_timeout')

    def _format(self,target,journal,guard):
        def dispatch():
            guard()
            previous=self._filesystem_uuids(target)
            return {'handle':self._mutate('filesystem_create',{'devicefile':target,'type':'ext4'}),'previous_uuids':previous}
        # A lost dispatch response is intentionally not reconciled from an arbitrary
        # discovered filesystem: there is no returned identity proving ownership.
        task=journal.perform('filesystem.dispatch',dispatch,destructive=True)
        def finish():
            self._wait_bg(task['handle'])
            result=self._filesystem_for(target)
            if not result or result['type']!='ext4' or result['uuid'] in task.get('previous_uuids',[]):raise SetupError('recovery_required')
            return result
        def reconcile(record):
            saved=record.get('result')
            if not saved:return None
            current=self._filesystem_for(target)
            return saved if current and all(current.get(k)==saved.get(k) for k in ('uuid','devicefile','type')) else None
        return journal.perform('filesystem.result',finish,reconcile)

    def _filesystem_uuids(self,target):
        tree,_=self._discover();target=str(Path(target).resolve());found=set()
        def collect(node,inside=False):
            inside=inside or node['name']==target
            if inside and node.get('uuid'):found.add(node['uuid'])
            for child in node.get('children',[]):collect(child,inside)
        for node in tree['blockdevices']:collect(node)
        return sorted(found)

    def _filesystem_for(self,target):
        # OMV formats physical disks on partition1, stacked devices directly.
        tree,_=self._discover();target=str(Path(target).resolve());nodes={}
        def visit(node):
            nodes[node['name']]=node
            for child in node.get('children',[]):visit(child)
        for node in tree['blockdevices']:visit(node)
        parent=nodes.get(target)
        if not parent:return None
        children=parent.get('children',[])
        if parent['type']=='disk':
            if len(children)!=1 or children[0].get('type')!='part':return None
            candidates=[children[0]]
        else:candidates=[parent]
        node=candidates[0]
        if node.get('fstype')!='ext4' or not node.get('uuid'):return None
        found=[f for f in self._rpc('filesystem_list') if f.get('uuid')==node['uuid'] and str(Path(f['devicefile']).resolve())==node['name']]
        if len(found)!=1 or found[0].get('_readonly'):return None
        return {'uuid':node['uuid'],'devicefile':node['name'],'type':'ext4'}

    def _digest(self):
        # Detect unrelated OMV edits between mutations/deployment. This is not a
        # substitute for OMV's own deployment lock or VM concurrency testing.
        return hashlib.sha256(Path('/etc/openmediavault/config.xml').read_bytes()).hexdigest()

    def _mutate(self,operation,params):
        if self._config_digest is None or self._digest()!=self._config_digest:
            raise SetupError('omv_configuration_changed')
        result=self._rpc(operation,params)
        self._config_digest=self._digest()
        self._save_context()
        return result


def safe_share_path(base,relative,must_be_new=False):
    base=Path(base)
    if base.resolve()!=base or not base.is_dir():raise SetupError('unsafe_storage')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',relative) or relative.startswith('.'):raise SetupError('unsafe_storage')
    path=base/relative
    if path.is_symlink() or path.resolve()!=path:raise SetupError('unsafe_storage')
    if must_be_new and path.exists():raise SetupError('existing_installation')
    return path

# Exact source contracts; accepting a different schema needs a reviewed adapter.
SCHEMA_SHA256 = {'rpc.config.json': '3fa1cf3dbeac08c5c012d2da399fafdee6d679bc189767fcf890eb395b52be6d', 'rpc.diskmgmt.json': '71dcc4d68ba5abc13875d6f9e2984f12bb284a41c92554b31bdf116f5b5575db', 'rpc.exec.json': '106f86f96fbe4cf4ad28bc7458470597343e8d6c4945482d98608841d40a3c00', 'rpc.filesystemmgmt.json': '132cfd9aa36ddd40afddf374322ae2ac3cddc14c7d8c6af138fea29c30bcede1', 'rpc.mdmgmt.json': '8db7aeae70316297d9102756cdee893c321bd9e77c25e9cbd8d142125aea1afb', 'rpc.sharemgmt.json': 'f30556bdee936d547510557447491eff22d75756b18e95032142d66c04757ea2', 'rpc.smb.json': '419d7c32a8736f2b069688d85ef136040f91ba2f5a9a5a22d4d51adf8842c4ea', 'rpc.usermgmt.json': '68da16c91f304b418e67665db62ed0ddb639664a93a891bbbf8d779685436f4e'}


def verify_schemas(directory=Path('/usr/share/openmediavault/datamodels'),raid=False):
    for name,digest in SCHEMA_SHA256.items():
        if name=='rpc.mdmgmt.json' and not raid:continue
        try:actual=hashlib.sha256((Path(directory)/name).read_bytes()).hexdigest()
        except OSError:raise SetupError('omv_schema_unsupported') from None
        if actual!=digest:raise SetupError('omv_schema_unsupported')
