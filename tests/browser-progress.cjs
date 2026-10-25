const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {mkdtempSync,rmSync,mkdirSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join,resolve}=require('node:path');
const net=require('node:net');
(async()=>{
 const root=resolve(__dirname,'..'),state=mkdtempSync(join(tmpdir(),'nas-progress-'));
 const port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
 const url=`http://127.0.0.1:${port}`;
 const server=spawn(join(root,'.venv/bin/nas-setup'),['demo','--state',state,'--port',String(port)],{cwd:root,stdio:'ignore'});
 let browser;
 try{
  for(let i=0;i<100;i++){try{await fetch(url+'/api/session');break;}catch{await new Promise(r=>setTimeout(r,100));}}
  browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(5000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let current={id:'progress-test',setup:true,status:'running',pause_requested:false,error:null,
   selection:{storage:{mode:'single'},users:[{name:'alice'},{name:'bob'}],apps:['immich']},
   steps:[{id:'storage_setup',status:'succeeded'},{id:'immich',status:'running',started_at:100,activity:'downloading_app'},{id:'verify',status:'pending'}],
   observed_at:165,resume_password_users:[],resources:{},links:{}};
  await page.route('**/api/job',r=>r.fulfill({json:current}));
  await page.goto(url);await page.locator('#demo-login').click();await page.locator('#screen-progress').waitFor({state:'visible'});
  await page.locator('#lang-en').click();
  assert.equal(await page.locator('#progress-percent').innerText(),'33%');
  await page.locator('#activity-panel').waitFor({state:'visible'});
  assert.match(await page.locator('#activity-label').innerText(),/Downloading/);
  assert.match(await page.locator('#activity-elapsed').innerText(),/1:0[5-9]/);
  assert(await page.locator('#resume-credentials').isHidden());
  // Repeated status snapshots must not rebuild/reset the animated current step.
  await page.evaluate(()=>{document.querySelector('#job-steps li[data-status=running]').dataset.preserved='yes';});
  await page.evaluate(()=>poll());
  assert.equal(await page.locator('#job-steps li[data-status=running]').getAttribute('data-preserved'),'yes');
  current.status='failed';current.error='installation_failed';current.steps[0].status='failed';current.steps[1].status='pending';current.resume_password_users=['bob'];
  await page.evaluate(()=>poll());
  assert.equal(await page.locator('.resume-password').count(),1);
  assert.equal(await page.locator('.resume-password').getAttribute('data-user'),'bob');
  await page.locator('.resume-password').fill('ExamplePassword-2026!');
  await page.locator('#lang-pt').click();assert.match(await page.locator('#resume-passwords label').innerText(),/Senha/);
  assert.equal(await page.locator('.resume-password').inputValue(),'ExamplePassword-2026!');
  await page.locator('#lang-en').click();
  // Hold an older poll until after Resume succeeds; it must not resurrect the form.
  await page.unroute('**/api/job');
  const stale=structuredClone(current);let release,seen;
  const held=new Promise(r=>release=r),requested=new Promise(r=>seen=r);
  await page.route('**/api/job',async r=>{seen();await held;await r.fulfill({json:stale});});
  const oldPoll=page.evaluate(()=>poll());await requested;
  await page.route('**/api/setup/resume',r=>{assert.deepEqual(r.request().postDataJSON().passwords,{bob:'ExamplePassword-2026!'});current={...current,status:'running',error:null,resume_password_users:[]};return r.fulfill({json:current});});
  await page.locator('#resume').click();await page.locator('#pause').waitFor({state:'visible'});
  release();await oldPoll;
  assert(await page.locator('#resume-credentials').isHidden(),'stale response cannot restore password prompt');
  assert.equal(await page.locator('.resume-password').count(),0,'accepted secrets removed from DOM');
  assert.equal(await page.evaluate(()=>job.status),'running');
  await page.unroute('**/api/job');
  current.steps[0]={id:'storage_setup',status:'succeeded'};current.steps[1]={id:'immich',status:'running',started_at:100,activity:'checking_app'};
  await page.route('**/api/job',r=>r.fulfill({json:current}));await page.evaluate(()=>poll());
  await page.locator('#lang-pt').click();assert.match(await page.locator('#activity-label').innerText(),/Aguardando/);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  mkdirSync(join(root,'test-results'),{recursive:true});await page.screenshot({path:join(root,'test-results/v023-progress-mobile.png'),fullPage:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.activity-sweep').evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.unroute('**/api/job');await page.route('**/api/job',r=>r.abort());await page.evaluate(()=>poll());
  assert.match(await page.locator('#activity-connection').innerText(),/Sem conexão/);
  assert.equal(await page.locator('#activity-panel').getAttribute('data-connected'),'false');
  assert.equal(await page.locator('#job-steps li[data-status=running] .step-icon').evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.unroute('**/api/job');current.status='succeeded';current.steps=current.steps.map(s=>({...s,status:'succeeded'}));
  await page.route('**/api/job',r=>r.fulfill({json:current}));await page.evaluate(()=>poll());
  assert(await page.locator('#activity-panel').isHidden());assert(await page.locator('#resume-credentials').isHidden());
  assert.equal(await page.locator('#progress-percent').innerText(),'100%');
  // Existing jobs without the new fields remain readable, without invented time.
  delete current.observed_at;delete current.resume_password_users;current.status='running';current.steps[1]={id:'immich',status:'running'};
  await page.evaluate(()=>poll());assert(await page.locator('#activity-elapsed').isHidden());
  assert.deepEqual(errors,[]);console.log('Progress browser PASS: truthful progress, activity, elapsed, missing users, stale resume, disconnection, PT/EN, reduced motion, mobile, legacy jobs.');
 }finally{await browser?.close();server.kill();rmSync(state,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
