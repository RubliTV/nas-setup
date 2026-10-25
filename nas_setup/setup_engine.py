"""Session-bound plans and durable setup orchestration for NAS provisioning."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from pydantic import Field, SecretStr, field_validator
from .models import SetupError, Selection, make_plan
from .setup_models import StrictModel, SetupSelection, plan_setup, inventory_fingerprint
from .journal import OperationJournal


class SetupStart(StrictModel):
    plan_id: str | None = Field(default=None,max_length=128)
    pending_id: str | None = Field(default=None,max_length=128)
    confirmation: str = Field(default='',max_length=32)
    erase_disks: list[str] = Field(default_factory=list,max_length=16)
    passwords: dict[str,SecretStr] = Field(default_factory=dict,max_length=32)

    @field_validator('passwords')
    @classmethod
    def password_lengths(cls,values):
        for value in values.values():
            secret=value.get_secret_value()
            if not 12<=len(secret)<=128 or any(c in secret for c in ('\n','\r','\0')):
                raise ValueError('password must contain 12 to 128 characters without control delimiters')
        return values


def private_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.private-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


class WizardEngine:
    def __init__(self,engine,provisioner):
        self.engine=engine;self.system=engine.system;self.provisioner=provisioner
        self.plans={};self.grants={};self.pending_offers={};self.pending_runs={}
        self.directory=engine.store.directory/'setup';self.directory.mkdir(exist_ok=True,mode=0o700)
        # A restart discards credentials and unused plans; a saved job retains its
        # original authorization, subject to recovery verification on every retry.
        for path in self.directory.glob('*/credentials.json'):path.unlink()

    def inventory(self):return {**self.system.inventory(),**self.provisioner.inventory()}

    def call(self,operation,payload):
        if set(payload)!={'session','data'} or not isinstance(payload['session'],str) or not payload['session']:
            raise SetupError('request_invalid')
        session=payload['session'];data=payload['data']
        if operation=='recovery':
            if data:raise SetupError('request_invalid')
            return self.recovery(session)
        if operation=='plan':return self.plan(SetupSelection(**data),session)
        if operation=='start':return self.start(SetupStart(**data),session)
        if operation=='resume':return self.resume(SetupStart(**data),session)
        raise SetupError('operation_invalid')

    def _recoverable(self,job,selection):
        return bool(job and job.get('setup') and job['selection']==selection.model_dump()
                    and job['status'] in {'failed','interrupted','paused'}
                    and not any(s['id']=='storage_setup' and s['status']=='succeeded' for s in job['steps']))

    def _recovery_fingerprint(self,job,inventory):
        if not inventory.get('supported'):raise SetupError('unsupported_host')
        journal=OperationJournal(self.directory/job['id']/'journal')
        self.provisioner.review_recovery(job['plan'],journal)
        ids=set(job['selection']['storage']['disks'])
        volume=job['selection']['storage']['volume']
        snapshot={'job':job['id'],'enabled':inventory.get('provisioning_enabled'),
                  'disks':sorted((d for d in inventory.get('disks',[]) if d['id'] in ids),key=lambda d:d['id']),
                  'volumes':[{k:v.get(k) for k in ('id','path','fstype')} for v in inventory.get('volumes',[]) if v['id']==volume],
                  'journal':journal.snapshot()}
        return hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()

    def plan(self,selection,session):
        with self.engine.lock:
            inv=self.inventory();job=self.engine.store.get()
            recovery=self._recoverable(job,selection)
            if recovery:
                fingerprint=self._recovery_fingerprint(job,inv)
                completed=OperationJournal(self.directory/job['id']/'journal').get('filesystem.result')
                destructive=job['plan']['destructive'] and not (completed and completed['status']=='completed' and completed['result'])
                plan={**job['plan'],'destructive':bool(destructive),
                      'warnings':[w for w in job['plan']['warnings'] if destructive or w!='erase_selected_disks']}
            else:
                plan=plan_setup(selection,inv)
                fingerprint=inventory_fingerprint(selection,inv)
            self.plans={k:p for k,p in self.plans.items() if p['expires']>time.time()}
            if len(self.plans)>=128:raise SetupError('rate_limited')
            identity=secrets.token_urlsafe(24);expires=time.time()+900
            self.plans[identity]={'session':session,'expires':expires,'plan':plan,'fingerprint':fingerprint,
                                  'recovery_job':job['id'] if recovery else None}
        return {**plan,'plan_id':identity,'expires':expires,'confirmation_required':plan['destructive'],'recovery':recovery}

    def _confirmed(self,body,session):
        record=self.plans.get(body.plan_id)
        if not record or record['session']!=session:raise SetupError('plan_missing')
        if record['expires']<time.time():raise SetupError('plan_expired')
        plan=record['plan'];selection=SetupSelection(**plan['selection'])
        if plan['destructive'] and (body.confirmation!='ERASE' or sorted(body.erase_disks)!=sorted(selection.storage.disks)):
            raise SetupError('confirmation_required')
        if record.get('recovery_job'):
            job=self.engine.store.get()
            if not self._recoverable(job,selection) or job['id']!=record['recovery_job']:raise SetupError('storage_changed')
            current=self._recovery_fingerprint(job,self.inventory())
        else:
            try:current=inventory_fingerprint(selection,self.inventory())
            except SetupError:raise SetupError('storage_changed')
        if current!=record['fingerprint']:raise SetupError('storage_changed')
        if not self.inventory().get('provisioning_enabled'):raise SetupError('provisioning_unvalidated')
        return plan

    def _credentials(self,body,selection):
        names={u['name'] for u in selection['users']}
        if set(body.passwords)!=names:raise SetupError('password_required')
        return {name:body.passwords[name].get_secret_value() for name in names}

    def start(self,body,session):
        with self.engine.lock:
            if body.pending_id is not None:raise SetupError('request_invalid')
            plan=self._confirmed(body,session)
            if self.plans[body.plan_id].get('recovery_job'):raise SetupError('recovery_resume_only')
            credentials=self._credentials(body,plan['selection'])
            job=self.engine.store.create(plan['selection'],plan['steps'],setup=True,plan=plan,resources={})
            self.plans.pop(body.plan_id);self.grants[job['id']]=session
            private_json(self.directory/job['id']/'credentials.json',credentials)
            self._launch(job)
            return job

    def recovery(self,session):
        with self.engine.lock:
            job=self.engine.store.get()
            if not job or not job.get('setup'):raise SetupError('job_missing')
            if job['status'] not in {'failed','interrupted','paused'}:raise SetupError('job_active')
            journal=OperationJournal(self.directory/job['id']/'journal')
            pending=self.provisioner.pending_configuration(job['plan'],journal)
            if not pending:return {'pending_id':None,'modules':[]}
            fingerprint=self._recovery_fingerprint(job,self.inventory())
            self.pending_offers={k:v for k,v in self.pending_offers.items() if v['expires']>time.time()}
            if len(self.pending_offers)>=128:raise SetupError('rate_limited')
            token=secrets.token_urlsafe(24)
            self.pending_offers[token]={'job':job['id'],'session':session,'expires':time.time()+900,
                                        'snapshot':pending,'fingerprint':fingerprint}
            return {'pending_id':token,'modules':pending['modules']}

    def resume(self,body,session):
        with self.engine.lock:
            job=self.engine.store.get()
            if not job or not job.get('setup'):raise SetupError('job_missing')
            if job['status'] not in {'failed','interrupted','paused','waiting-for-user','waiting-for-sync'}:
                raise SetupError('job_active')
            storage_done=any(s['id']=='storage_setup' and s['status']=='succeeded' for s in job['steps'])
            if not storage_done:
                # Jobs are created only after explicit confirmation and kept in the
                # root-private database. Resume that exact plan, never a new one.
                if job['plan']['selection']!=job['selection']:raise SetupError('storage_changed')
                if body.plan_id is not None:
                    plan=self._confirmed(body,session)
                    if plan['selection']!=job['selection']:raise SetupError('storage_changed')
                inv=self.inventory()
                if not inv.get('provisioning_enabled'):raise SetupError('provisioning_unvalidated')
                fingerprint=self._recovery_fingerprint(job,inv)
                pending=None
                if body.pending_id is not None:
                    offer=self.pending_offers.get(body.pending_id)
                    if not offer or offer['session']!=session or offer['job']!=job['id']:
                        raise SetupError('confirmation_required')
                    journal=OperationJournal(self.directory/job['id']/'journal')
                    if (offer['expires']<time.time() or offer['fingerprint']!=fingerprint or
                        offer['snapshot']!=self.provisioner.pending_configuration(job['plan'],journal)):
                        raise SetupError('omv_configuration_changed')
                    pending=offer['snapshot']
                credentials=self._credentials(body,job['selection'])
                if body.plan_id is not None:self.plans.pop(body.plan_id)
                self.grants[job['id']]=session
                private_json(self.directory/job['id']/'credentials.json',credentials)
                if pending:
                    self.pending_runs[job['id']]=pending
                    self.pending_offers.pop(body.pending_id)
            elif body.passwords or body.pending_id:raise SetupError('request_invalid')
            job=self.engine.store.update(job['id'],status='running',error=None,pause_requested=False)
            self._launch(job);return job

    def _launch(self,job):threading.Thread(target=self._run,args=(job,),daemon=True).start()

    def refresh(self,job):
        if job and job.get('setup') and job['status'] in {'waiting-for-sync','succeeded'}:
            with self.engine.lock:
                current=self.engine.store.get()
                if current['id']!=job['id'] or current['status'] not in {'waiting-for-sync','succeeded'}:return current
                try:
                    raid=self.provisioner.status(current['resources'])
                    resources={**current['resources'],'raid':raid}
                    current=self.engine.store.update(job['id'],resources=resources)
                    if raid['state']=='healthy':
                        if any(s['id']=='raid_sync' for s in current['steps']):
                            self.engine.store.step(job['id'],'raid_sync','succeeded')
                        current=self.engine.store.update(job['id'],status='succeeded',error=None)
                    elif raid['state']=='degraded':
                        current=self.engine.store.update(job['id'],status='failed',error='raid_degraded')
                    else:
                        current=self.engine.store.update(job['id'],status='waiting-for-sync',error=None)
                except SetupError as exc:current=self.engine.store.update(job['id'],status='failed',error=exc.code)
                return current
        return job

    def _run(self,job):
        identity=job['id'];step=None;credentials_path=self.directory/identity/'credentials.json'
        journal=OperationJournal(self.directory/identity/'journal')
        selection=job['selection'];plan=job['plan']
        try:
            pending=self.pending_runs.pop(identity,None)
            if pending:
                step='storage_prerequisites'
                self.engine.store.step(identity,step,'running')
                self.provisioner.apply_pending_configuration(plan,journal,pending)
                self.engine.store.step(identity,step,'succeeded')
            for record in job['steps']:
                step=record['id']
                current=self.engine.store.get(identity)
                if current['pause_requested']:
                    self.engine.store.update(identity,status='paused');return
                if record['status']=='succeeded' and step!='raid_sync':
                    if step=='storage_setup':self.provisioner.status(current['resources'])
                    continue
                self.engine.store.step(identity,step,'running')
                if step=='storage_prerequisites':self.provisioner.prepare(selection,plan)
                elif step=='storage_setup':
                    if identity not in self.grants:raise SetupError('confirmation_required')
                    if not credentials_path.exists():raise SetupError('credentials_required')
                    passwords=json.loads(credentials_path.read_text())
                    try:resources=self.provisioner.provision(selection,plan,journal,passwords)
                    finally:passwords.clear()
                    self.engine.store.update(identity,resources=resources)
                    credentials_path.unlink(missing_ok=True)
                elif step=='raid_sync':
                    current=self.engine.store.get(identity)
                    raid=self.provisioner.status(current['resources'])
                    self.engine.store.update(identity,resources={**current['resources'],'raid':raid})
                    if raid['state']=='degraded':raise SetupError('raid_degraded')
                    if raid['state']!='healthy':
                        self.engine.store.step(identity,step,'waiting-for-sync')
                        self.engine.store.update(identity,status='waiting-for-sync');return
                else:
                    resources=self.engine.store.get(identity)['resources']
                    self.provisioner.status(resources)
                    app_selection=Selection(apps=selection['apps'] or ['tailscale'],volume=resources['volume'],
                        database_volume=resources['volume'],machine_learning=selection['machine_learning'],timezone=selection['timezone'])
                    if selection['apps']:make_plan(app_selection,self.system.inventory())
                    if step=='docker':self.system.install_docker(app_selection)
                    elif step in ('immich','jellyfin'):
                        if step=='jellyfin' and resources.get('media_path') and hasattr(self.system,'approve_media'):
                            self.system.approve_media(resources)
                        self.system.install_app(step,app_selection)
                    elif step=='tailscale':self.system.install_tailscale()
                    elif step=='verify':
                        result=self.system.verify(app_selection) if selection['apps'] else {'links':{},'waiting':False}
                        self.engine.store.update(identity,links=result['links'])
                        if result.get('waiting'):
                            self.engine.store.step(identity,step,'waiting-for-user')
                            self.engine.store.update(identity,status='waiting-for-user');return
                self.engine.store.step(identity,step,'succeeded')
            self.engine.store.update(identity,status='succeeded')
        except Exception as exc:
            code=exc.code if isinstance(exc,SetupError) else 'installation_failed'
            # Do not serialize exception messages here: third-party RPC errors may contain passwords.
            if hasattr(self.system,'diagnostic'):
                try:self.system.diagnostic(step or 'setup',code)
                except OSError:pass
            if step:self.engine.store.step(identity,step,'failed')
            self.engine.store.update(identity,status='failed',error=code)
            self.grants.pop(identity,None)
        finally:
            credentials_path.unlink(missing_ok=True)
