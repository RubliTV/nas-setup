"""Explicit simulation of disk provisioning; never invokes system commands."""
import time
from pathlib import Path
from .models import SetupError


class DemoProvisioner:
    def __init__(self,system):
        self.system=system
        self.disks=[{'id':f'demo-disk-{i}','model':'Demo System SSD' if i==0 else 'Demo HDD 1 TB',
            'serial':f'DEMO-{i:04d}','size_bytes':64*10**9 if i==0 else 10**12,
            'transport':'sata','eligible':i!=0,'reason':'system_disk' if i==0 else None,'signatures':[]}
            for i in range(5)]
        self.sync_until=0

    def inventory(self):return {'disks':self.disks,'provisioning_enabled':True}

    def review_recovery(self,plan,journal):
        return None

    def prepare(self,selection,plan):time.sleep(self.system.delay)

    def provision(self,selection,plan,journal,passwords):
        def create():
            time.sleep(self.system.delay)
            root=self.system.data/'shared';root.mkdir(exist_ok=True)
            shares=[]
            for share in selection['shares']:
                path=root/share['name'];path.mkdir(exist_ok=True)
                shares.append({'name':share['name'],'path':str(path),'folder':share['folder'],
                    'permissions':share['permissions'],'demo':True})
            self.sync_until=time.monotonic()+max(.03,self.system.delay*2)
            return {'volume':selection['storage'].get('volume') or 'demo-data',
                'media_path':next((s['path'] for s in shares if s['folder']=='media'),None),
                'shares':shares,'users':[u['name'] for u in selection['users']],
                'raid':{'state':'syncing' if plan['tolerance'] else 'healthy','progress':0 if plan['tolerance'] else 100,'tolerance':plan['tolerance']},
                'demo':True}
        return journal.perform('demo-storage',create,destructive=plan['destructive'])

    def status(self,resources):
        if not self.system.disk_available:raise SetupError('volume_missing')
        tolerance=resources.get('raid',{}).get('tolerance',0)
        syncing=bool(tolerance) and time.monotonic()<self.sync_until
        return {'state':'syncing' if syncing else 'healthy','progress':45 if syncing else 100,'tolerance':tolerance}
