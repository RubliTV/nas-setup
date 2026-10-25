"""Durable installation orchestration; no shell commands accepted from the UI."""
import threading
from .jobs import JobStore
from .progress import reporting
from .models import Selection, SetupError, make_plan

class Engine:
    def __init__(self,state_dir,system):
        self.store=JobStore(state_dir);self.system=system;self.lock=threading.Lock()
        self.store.interrupt_running()
        from .setup_engine import WizardEngine
        if getattr(system,'demo',False):
            from .demo_setup import DemoProvisioner
            provisioner=DemoProvisioner(system)
        else:
            from .omv import OmvProvisioner
            provisioner=OmvProvisioner(system)
        self.wizard=WizardEngine(self,provisioner)

    def call(self,operation,payload=None):
        payload=payload or {}
        if operation.startswith('setup_'):return self.wizard.call(operation[6:],payload)
        if operation in ('plan','start'):
            selection=Selection(**payload)
            plan=make_plan(selection,self.system.inventory())
            if operation=='plan':return plan
            with self.lock:
                job=self.store.create(selection.model_dump(),plan['steps'])
                self._launch(job)
            return job
        if payload:raise SetupError('operation_invalid')
        if operation=='inventory':return self.wizard.inventory()
        if operation=='job':return self.wizard.present(self.wizard.refresh(self.store.get()))
        if operation=='tailscale':return self.system.tailscale_status()
        if operation=='pause':
            with self.lock:
                job=self.store.get()
                if not job or job['status']!='running':raise SetupError('job_missing')
                return self.store.update(job['id'],pause_requested=True)
        if operation=='resume':
            with self.lock:
                job=self.store.get()
                if not job or job['status'] not in ('failed','interrupted','paused','waiting-for-user'):raise SetupError('job_active')
                if job.get('setup'):raise SetupError('confirmation_required')
                make_plan(Selection(**job['selection']),self.system.inventory())
                job=self.store.update(job['id'],status='running',error=None,pause_requested=False)
                self._launch(job);return job
        if operation=='finish':
            job=self.wizard.refresh(self.store.get())
            if not job or job['status']!='succeeded':raise SetupError('job_active')
            self.system.finish();return {'status':'finished'}
        raise SetupError('operation_invalid')

    def _launch(self,job):
        threading.Thread(target=self._run,args=(job,),daemon=True).start()

    def _run(self,job):
        identity=job['id'];selection=Selection(**job['selection']);step=None
        with reporting(lambda code:self.store.activity(identity,step,code)):
            try:
                for record in job['steps']:
                    step=record['id']
                    if self.store.get(identity)['pause_requested']:
                        self.store.update(identity,status='paused');return
                    self.store.step(identity,step,'running')
                    # Always re-inspect the host and mounts, including on retry.
                    make_plan(selection,self.system.inventory())
                    if step=='preflight':self.system.preflight(selection)
                    elif step=='docker':self.system.install_docker(selection)
                    elif step in ('immich','jellyfin'):self.system.install_app(step,selection)
                    elif step=='tailscale':self.system.install_tailscale()
                    elif step=='verify':
                        result=self.system.verify(selection)
                        self.store.update(identity,links=result['links'])
                        if result.get('waiting'):
                            self.store.step(identity,step,'waiting-for-user')
                            self.store.update(identity,status='waiting-for-user');return
                    self.store.step(identity,step,'succeeded')
                self.store.update(identity,status='succeeded')
            except Exception as exc:
                code=exc.code if isinstance(exc,SetupError) else 'installation_failed'
                if hasattr(self.system,'diagnostic'):
                    try:self.system.diagnostic(step or 'job',type(exc).__name__+': '+str(exc))
                    except OSError:pass
                if step:self.store.step(identity,step,'failed')
                self.store.update(identity,status='failed',error=code)
