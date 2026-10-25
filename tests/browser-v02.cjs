const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {mkdtempSync,rmSync,mkdirSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join,resolve}=require('node:path');
const net=require('node:net');
// Catches wrong RAID capacity/minima, silently lost permissions, password leakage,
// accidental erasure consent, lost non-secret drafts, and false completion during sync.
(async()=>{
 const root=resolve(__dirname,'..'),state=mkdtempSync(join(tmpdir(),'nas-v02-'));
 const port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
 const url=`http://127.0.0.1:${port}`;
 const server=spawn(join(root,'.venv/bin/nas-setup'),['demo','--state',state,'--port',String(port)],{cwd:root,stdio:'ignore'});
 let browser;
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fetch(url+'/api/session');ready=true;break;}catch{await new Promise(r=>setTimeout(r,100));}}
  assert(ready,'demo server starts');
  browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
  let authCookies=[];
  // Exercise every planner from the UI, with no accounts/shares/apps required.
  for(const [mode,count] of [['existing',0],['single',1],['raid5',3],['raid6',4]]){
   const context=await browser.newContext(),p=await context.newPage();p.setDefaultTimeout(5000);
   if(authCookies.length)await context.addCookies(authCookies);
   await p.goto(url);if(!authCookies.length)await p.locator('#demo-login').click();await p.locator('#screen-storage').waitFor({state:'visible'});
   authCookies=await context.cookies();
   await p.locator('#lang-en').click();await p.locator('#storage-mode').selectOption(mode);
   if(mode==='existing')await p.locator('#volume').selectOption('demo-data');
   else for(let i=0;i<count;i++)await p.locator('.disk-card input:not(:disabled)').nth(i).check();
   await p.locator('#storage-next').click();await p.locator('.user-row .text-button').click();
   for(const toggle of await p.locator('.share-toggle input').all())await toggle.uncheck();
   await p.locator('#shares-next').click();await p.locator('#apps-next').click();await p.locator('#screen-review').waitFor({state:'visible'});
   assert.equal(await p.locator('.erase-check').count(),count);
   assert.equal(await p.locator('#install').isDisabled(),count>0);
   assert.equal(await p.locator('#erase-review').isVisible(),count>0);
   if(mode==='raid5')assert.match(await p.locator('#review-summary').innerText(),/Tolerates 1 disk failure/);
   if(mode==='single'){
    // A recovery plan preserves completed disk operations and needs no new erase grant.
    await p.locator('#review-back').click();
    await p.route('**/api/setup/plan',async route=>{const r=await route.fetch();const body=await r.json();body.destructive=false;body.confirmation_required=false;body.recovery=true;await route.fulfill({response:r,json:body});});
    await p.locator('#apps-next').click();await p.locator('#screen-review').waitFor({state:'visible'});
    assert(await p.locator('#erase-review').isHidden());
    assert.match(await p.locator('#plan-storage').innerText(),/completed disk operations will not be repeated/);
    assert(await p.locator('#install').isEnabled());
   }
   await context.close();
  }
  // The real provisioning gate also blocks the existing-volume path until validated.
  {
   const context=await browser.newContext(),p=await context.newPage();
   await p.route('**/api/inventory',async route=>{const r=await route.fetch();const body=await r.json();body.provisioning_enabled=false;await route.fulfill({response:r,json:body});});
   if(authCookies.length)await context.addCookies(authCookies);
   await p.goto(url);if(!authCookies.length)await p.locator('#demo-login').click();await p.locator('#screen-storage').waitFor({state:'visible'});
   authCookies=await context.cookies();
   await p.locator('#volume').selectOption('demo-data');assert(await p.locator('#storage-next').isDisabled(),'unvalidated provisioning cannot advance');
   await context.close();
  }
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(5000);
  const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().includes('/api/setup/'))requests.push({url:r.url(),body:r.postDataJSON()});});
  await page.context().addCookies(authCookies);await page.goto(url);
  await page.locator('#screen-storage').waitFor({state:'visible'});
  assert.equal(await page.locator('.disk-card input:checked').count(),0,'erasure targets never preselected');
  assert(await page.locator('.disk-card input:disabled').count()>=1,'OS disk cannot be selected');
  await page.locator('#storage-mode').selectOption('raid1');
  assert(await page.locator('#storage-next').isDisabled(),'mirror requires two explicit disks');
  const disks=page.locator('.disk-card input:not(:disabled)');
  await disks.nth(0).check();await disks.nth(1).check();
  assert.match(await page.locator('#capacity-value').innerText(),/1[.,]00 TB/);
  await page.locator('#storage-mode').selectOption('raid5');assert(await page.locator('#storage-next').isDisabled());
  await disks.nth(2).check();assert.match(await page.locator('#capacity-value').innerText(),/2[.,]00 TB/);
  await page.locator('#storage-mode').selectOption('raid6');assert(await page.locator('#storage-next').isDisabled());
  await disks.nth(3).check();assert.match(await page.locator('#capacity-value').innerText(),/2[.,]00 TB/);
  await disks.nth(3).uncheck();await disks.nth(2).uncheck();await page.locator('#storage-mode').selectOption('raid1');
  await page.locator('#storage-next').click();
  await page.locator('.user-name').first().fill('alice');
  await page.locator('.user-password').first().fill('ExamplePass-2026!');
  const passwordField=page.locator('.user-password').first();
  const passwordToggle=passwordField.locator('..').locator('.password-toggle');
  assert.equal(await passwordField.getAttribute('type'),'password');
  await passwordToggle.click();
  assert.equal(await passwordField.getAttribute('type'),'text');
  assert.equal(await passwordField.inputValue(),'ExamplePass-2026!');
  assert.equal(await passwordToggle.getAttribute('aria-pressed'),'true');
  assert.match(await passwordToggle.innerText(),/Ocultar/);
  await passwordToggle.click();
  assert.equal(await passwordField.getAttribute('type'),'password');
  await page.locator('.user-confirm').first().fill('wrong');
  await page.locator('#shares-next').click();assert(await page.locator('#screen-shares').isVisible());
  await page.locator('.user-confirm').first().fill('ExamplePass-2026!');
  await page.locator('#add-user').click();await page.locator('.user-name').nth(1).fill('bob');
  await page.locator('.user-password').nth(1).fill('OtherPass-2026!');await page.locator('.user-confirm').nth(1).fill('OtherPass-2026!');
  await page.locator('[data-share="files"] .permission-select').nth(1).selectOption('ro');
  await page.locator('[data-share="media"] .permission-select').nth(1).selectOption('none');
  await page.locator('#lang-en').click();assert.equal(await page.locator('.user-name').first().inputValue(),'alice');
  assert.equal(await page.locator('.password-toggle').first().innerText(),'Show');
  assert.equal(await page.locator('[data-share="files"] .permission-select').nth(1).inputValue(),'ro');
  const storage=await page.evaluate(()=>JSON.stringify(localStorage));assert(!storage.includes('ExamplePass'));assert(!storage.includes('OtherPass'));
  await page.reload();await page.locator('#screen-shares').waitFor({state:'visible'});
  assert.equal(await page.locator('.user-password').first().inputValue(),'','passwords never survive reload');
  assert.equal(await page.locator('[data-share="media"] .permission-select').nth(1).inputValue(),'none');
  for(const [i,pw] of ['ExamplePass-2026!','OtherPass-2026!'].entries()){await page.locator('.user-password').nth(i).fill(pw);await page.locator('.user-confirm').nth(i).fill(pw);}
  await page.locator('.user-password').first().locator('..').locator('.password-toggle').click();
  assert.equal(await page.locator('.user-password').first().getAttribute('type'),'text');
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  mkdirSync(join(root,'test-results'),{recursive:true});
  await page.screenshot({path:join(root,'test-results/v024-password-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#shares-next').click();
  for(const c of await page.locator('.app-card input').all())await c.uncheck();
  await page.locator('#apps-next').click();await page.locator('#screen-review').waitFor({state:'visible'});
  assert(await page.locator('#install').isDisabled(),'per-drive and typed consent required');
  assert.equal(await page.locator('.erase-check').count(),2);
  for(const c of await page.locator('.erase-check').all())await c.check();
  assert(await page.locator('#install').isDisabled());await page.locator('#erase-confirmation').fill('ERASE');
  const planned=requests.find(r=>r.url.endsWith('/plan')).body;
  assert.deepEqual(planned.users,[{name:'alice'},{name:'bob'}]);assert.deepEqual(planned.apps,[]);
  assert.equal(planned.shares.find(s=>s.folder==='files').permissions.bob,'ro');
  assert.equal(planned.shares.find(s=>s.folder==='media').permissions.bob,'none');
  assert(!JSON.stringify(planned).includes('Pass-2026'));
  mkdirSync(join(root,'test-results'),{recursive:true});await page.screenshot({path:join(root,'test-results/v02-review-en.png'),fullPage:true});
  await page.locator('#install').click();await page.locator('#screen-progress').waitFor({state:'visible'});
  assert.equal(await page.locator('.user-password').first().inputValue(),'','revealed password cleared after submission');
  await page.waitForFunction(()=>document.querySelector('#raid-status').textContent.includes('synchroniz'));
  assert(await page.locator('#finish').isHidden(),'syncing array cannot finish');
  await page.reload();await page.locator('#screen-progress').waitFor({state:'visible'});
  await page.locator('#finish').waitFor({state:'visible',timeout:60000});
  assert.match(await page.locator('#results').innerText(),/alice/);
  assert.match(await page.locator('#results').innerText(),/smb:\/\//);
  await page.locator('#lang-pt').click();await page.screenshot({path:join(root,'test-results/v02-completed-pt.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:join(root,'test-results/v02-mobile.png'),fullPage:true});
  // Adapter status reports healthy for non-RAID resources too; do not call that RAID protection.
  await page.route('**/api/job',async route=>{const r=await route.fetch();const body=await r.json();body.selection.storage.mode='single';await route.fulfill({response:r,json:body});});
  await page.reload();await page.locator('#finish').waitFor({state:'visible'});
  assert(await page.locator('#raid-status').isHidden(),'single-disk storage is never labeled healthy RAID');
  // Retry remains on progress even if an older server requests a new plan.
  await page.unroute('**/api/job');
  let retryJob=await page.evaluate(async()=>await (await fetch('/api/job')).json());
  retryJob.status='failed';retryJob.error='omv_configuration_changed';
  delete retryJob.resume_password_users; // legacy response, before per-account metadata
  retryJob.steps=retryJob.steps.map(s=>({...s,status:s.id==='storage_prerequisites'?'succeeded':s.id==='storage_setup'?'failed':'pending'}));
  retryJob.resources={};
  await page.route('**/api/job',route=>route.fulfill({json:retryJob}));
  await page.route('**/api/setup/resume',route=>route.fulfill({status:400,json:{error:'plan_missing'}}));
  await page.reload();await page.locator('#resume-passwords input').first().waitFor({state:'visible'});
  for(const input of await page.locator('#resume-passwords input').all())await input.fill('RetryPassword-2026!');
  const plansBefore=requests.filter(r=>r.url.endsWith('/plan')).length;
  await page.locator('#resume').click();await page.locator('#error').waitFor({state:'visible'});
  assert(await page.locator('#screen-progress').isVisible(),'retry errors stay on progress');
  assert.equal(requests.filter(r=>r.url.endsWith('/plan')).length,plansBefore,'retry does not request another erase plan');
  // mdadm is auto-applicable only to RAID jobs; other modes need the button.
  retryJob.selection.storage.mode='single';
  await page.route('**/api/setup/recovery',route=>route.fulfill({json:{pending_id:'single-pending',modules:['mdadm']}}));
  await page.reload();await page.locator('#pending-config').waitFor({state:'visible'});
  assert.match(await page.locator('#pending-modules').innerText(),/mdadm/);
  await page.unroute('**/api/setup/recovery');retryJob.selection.storage.mode='raid1';
  // Unattributed OMV changes can be approved inline, without disk review.
  await page.route('**/api/setup/recovery',route=>route.fulfill({json:{pending_id:'pending-test-token',modules:['fstab','mdadm']}}));
  await page.reload();await page.locator('#pending-config').waitFor({state:'visible'});
  assert.match(await page.locator('#pending-modules').innerText(),/fstab/);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:join(root,'test-results/v021-pending-mobile.png'),fullPage:true});
  assert(await page.locator('#screen-progress').isVisible());
  for(const input of await page.locator('#resume-passwords input').all())await input.fill('RetryPassword-2026!');
  await page.unroute('**/api/setup/resume');
  await page.route('**/api/setup/resume',async route=>{retryJob={...retryJob,status:'running',error:null};await route.fulfill({json:retryJob});});
  await page.locator('#apply-pending').click();await page.locator('#pause').waitFor({state:'visible'});
  assert.equal(requests.filter(r=>r.url.endsWith('/resume')).at(-1).body.pending_id,'pending-test-token');
  assert(await page.locator('#screen-progress').isVisible());
  assert.equal(await page.locator('#resume-passwords input').count(),0,'accepted retry removes password fields');
  assert.deepEqual(errors,[]);console.log('v0.2 browser integration passed: layouts, permissions, secrets, consent, reload, results, PT/EN and mobile.');
 }finally{await browser?.close();server.kill();rmSync(state,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
