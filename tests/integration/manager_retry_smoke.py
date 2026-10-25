"""Guarded disposable-VM retry test; inject one Docker pull failure only."""
import json
from pathlib import Path
import subprocess
import time

from manager_smoke import guard, emit, run


def main():
    guard()
    from nas_setup.catalog import APPS
    from nas_setup.engine import Engine
    from nas_setup.system import System
    state=Path('/root/nas-v030-manager-retry-final-test')
    resume=state.exists()
    if state.is_symlink():raise SystemExit('Unsafe retry state')
    class FailOnePull(System):
        pending=True
        def run(self,args,timeout=60,check=True):
            if self.pending and args[:2]==['docker','compose'] and args[-1]=='pull':
                self.pending=False
                return subprocess.CompletedProcess(args,1,stdout='',stderr='injected disposable test pull failure')
            return super().run(args,timeout=timeout,check=check)
    engine=Engine(state,System(state) if resume else FailOnePull(state))
    def api(op,data=None):return engine.call('manager_'+op,{'session':'retry-smoke','data':data or {}})
    def wait(job,status):
        deadline=time.monotonic()+300
        while True:
            job=api('job',{'id':job['id']})
            if job['status']!='running':
                assert job['status']==status
                emit('retry_job_terminal',id=job['id'],status=job['status'],error=job.get('error'))
                return job
            if time.monotonic()>deadline:raise TimeoutError('Retry smoke timed out')
            time.sleep(1)
    if resume:
        with engine.manager.connect() as db:jobs=[json.loads(row[0]) for row in db.execute('SELECT body FROM jobs ORDER BY created')]
        assert len(jobs)==2
        failed,retried=jobs
        assert failed['operation']=='deploy' and failed['status']=='failed' and failed['error']=='manager_command_failed'
        assert retried['operation']=='retry' and retried['status']=='succeeded'
        emit('resume_durable_retry_verification')
    else:
        compose='services:\n  worker:\n    image: '+APPS[0]['image']+'\n    entrypoint: [/bin/sh, -c]\n    command: ["while true; do sleep 30; done"]\n'
        plan=api('plan',{'kind':'compose','name':'retry-smoke-final','compose':compose})
        failed=wait(api('deploy',{'plan_id':plan['plan_id']}),'failed')
        assert failed['error']=='manager_command_failed'
        assert next(s for s in failed['steps'] if s['id']=='pull')['status']=='failed'
        emit('injected_pull_failure_persisted',boundary='docker compose pull')
        # Reopen durable state with an unmodified production System for the retry.
        engine=Engine(state,System(state))
        assert api('job',{'id':failed['id']})['status']=='failed'
        retried=wait(api('retry',{'id':failed['id']}),'succeeded')
    assert retried['id']!=failed['id'] and api('job',{'id':failed['id']})['status']=='failed'
    container=next(c for c in api('overview')['containers'] if c['project']=='nas-manager-retry-smoke-final')
    assert container['managed'] and container['state']=='running'
    assert run(['docker','inspect','--format','{{.Config.Image}}',container['id']]).strip()==APPS[0]['image']
    evidence={'disposable_vm_only':True,'injected_failure_boundary':'docker compose pull','original_status':failed['status'],'original_error':failed['error'],'retry_status':retried['status'],'new_job_id':retried['id']!=failed['id'],'original_history_preserved':True,'actual_container_state':container['state'],'image':APPS[0]['image']}
    (state/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    emit('MANAGER_RETRY_SMOKE_PASS',**evidence)


if __name__=='__main__':
    try:main()
    except Exception as error:
        import traceback
        frame=traceback.extract_tb(error.__traceback__)[-1]
        emit('MANAGER_RETRY_SMOKE_FAILED',location=Path(frame.filename).name+':'+str(frame.lineno),kind=type(error).__name__,code=getattr(error,'code','verification_failed'))
        raise SystemExit(1) from None
