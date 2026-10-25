'use strict';
const $=id=>document.getElementById(id);
let dictionaries={},lang=localStorage.getItem('nas-language')||'pt-BR',csrf='',screen='login',inventory=null,plan=null,job=null,demo=false,polling=false,errorCode=null;
let jobEpoch=0,busyActions=0,lastJobAt=0,connectionFailed=false,stepsKey="";
let pendingInfo=null,pendingChecked=null,pendingRequest=null;
let draft={storage:{mode:'raid1',disks:[],volume:null},users:[{name:''}],shares:[],apps:[],timezone:'America/Sao_Paulo',machine_learning:false},passwords=[];
try{const saved=JSON.parse(localStorage.getItem('nas-setup-draft'));if(saved?.selection){draft=saved.selection;screen=saved.screen||'storage';}}catch{}
const t=(key,values={})=>Object.entries(values).reduce((s,[k,v])=>s.replaceAll(`{${k}}`,String(v)),dictionaries[lang]?.[key]||dictionaries.en?.[key]||key);
const text=(id,value)=>{if($(id).textContent!==value)$(id).textContent=value;};
function node(tag,cls,value){const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=value;return e;}
function error(code){errorCode=code;$('error').hidden=!code;if(code)text('error',message(code));}
function message(code){return dictionaries[lang]?.['error_'+code]||t('error_request_invalid');}
async function api(path,method='GET',body){
 let response;try{response=await fetch('/api/'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});}catch{throw new Error('network_error');}
 let data;try{data=await response.json();}catch{throw new Error('network_error');}
 if(!response.ok)throw new Error(data.error||(response.status===422?'request_invalid':'installation_failed'));return data;
}
async function action(button,fn){jobEpoch++;busyActions++;button.disabled=true;error(null);try{await fn();}catch(e){error(e.message);}finally{busyActions--;jobEpoch++;button.disabled=false;updateSelection();}}
function save(){localStorage.setItem('nas-setup-draft',JSON.stringify({selection:draft,screen:['storage','shares','apps'].includes(screen)?screen:'apps'}));}
function invalidate(){plan=null;save();}
function show(name){screen=name;document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='screen-'+name);document.querySelectorAll('#navigation li').forEach(s=>s.classList.toggle('active',s.dataset.step===name));error(null);if(inventory&&!job)save();window.scrollTo({top:0,behavior:'instant'});}
function selection(){return {storage:{...draft.storage,disks:draft.storage.mode==='existing'?[]:[...draft.storage.disks],volume:draft.storage.mode==='existing'?draft.storage.volume:null},users:draft.users.map(u=>({name:u.name.trim()})),shares:draft.shares.filter(s=>s.enabled).map(s=>({name:s.name.trim(),folder:s.folder,permissions:Object.fromEntries(draft.users.map(u=>[u.name.trim(),s.permissions[u.name]||'none']))})),apps:[...draft.apps],timezone:draft.timezone.trim(),machine_learning:draft.apps.includes('immich')&&draft.machine_learning};}
function chosenDisks(){return (inventory?.disks||[]).filter(d=>draft.storage.disks.includes(d.id));}
function canUseDisk(d){return d.eligible&&inventory.provisioning_enabled!==false&&(draft.storage.mode==='single'||['sata','sas','nvme'].includes((d.transport||'').toLowerCase()));}
function validStorage(){if(!inventory?.supported||inventory.provisioning_enabled===false)return false;if(draft.storage.mode==='existing')return !!inventory.volumes?.some(v=>v.id===draft.storage.volume);if(inventory.provisioning_enabled===false)return false;const disks=chosenDisks(),n=disks.length;return disks.every(canUseDisk)&&({single:n===1,raid1:n===2,raid5:n>=3,raid6:n>=4}[draft.storage.mode]);}
function updateSelection(){
 document.querySelectorAll('.app-card').forEach(c=>c.classList.toggle('selected',draft.apps.includes(c.dataset.app)));
 text('selection-count',t('selected',{count:draft.apps.length}));$('apps-next').disabled=!inventory?.supported;$('storage-next').disabled=!validStorage();$('database-options').hidden=!draft.apps.includes('immich');
 const checks=[...document.querySelectorAll('.erase-check')];$('install').disabled=!plan||(plan.confirmation_required||plan.destructive)&&(!checks.length||!checks.every(c=>c.checked)||$('erase-confirmation').value!=='ERASE');
}
function applyLanguage(){
 document.documentElement.lang=lang;localStorage.setItem('nas-language',lang);document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n));
 $('lang-pt').classList.toggle('active',lang==='pt-BR');$('lang-en').classList.toggle('active',lang==='en');
 if(inventory){renderInventory();renderUsers();renderShares();}if(plan)renderPlan();if(job)renderJob();if(errorCode)text('error',message(errorCode));updateSelection();
}
function toleranceLabel(count){return t(count===0?'toleranceNone':count===1?'toleranceOne':'tolerance',{count});}
function size(bytes){return `${(Number(bytes||0)/1e12).toLocaleString(lang,{minimumFractionDigits:2,maximumFractionDigits:2})} TB`;}
function diskReason(d){if(!d.eligible)return t('disk_'+d.reason)===('disk_'+d.reason)?t('disk_unavailable'):t('disk_'+d.reason);if(!canUseDisk(d))return t(inventory.provisioning_enabled===false?'provisionGate':'disk_transport');return t('diskAvailable');}
function renderInventory(){
 text('hostname',inventory.hostname);text('host-os',inventory.os);text('cpu',inventory.cpu);text('memory',inventory.memory_gb+' GB RAM');text('host-badge',t(inventory.supported?'compatible':'incompatible'));
 $('provision-gate').hidden=inventory.provisioning_enabled!==false;$('storage-mode').value=draft.storage.mode;
 for(const o of $('storage-mode').options)o.disabled=inventory.provisioning_enabled===false&&o.value!=='existing';
 const volumes=$('volume');volumes.replaceChildren(new Option(t('chooseVolume'),''));for(const v of inventory.volumes||[])volumes.add(new Option(`${v.label} — ${t('free',{space:v.free_gb})}`,v.id));volumes.value=draft.storage.volume||'';
 const list=$('disk-list');list.replaceChildren();for(const d of inventory.disks||[]){
  const card=node('label','disk-card'+(!canUseDisk(d)?' unavailable':'')+(draft.storage.disks.includes(d.id)?' selected':''));
  const input=node('input');input.type='checkbox';input.value=d.id;input.checked=draft.storage.disks.includes(d.id);input.disabled=draft.storage.mode==='existing'||!canUseDisk(d);input.setAttribute('aria-label',`${d.model} ${d.serial}`);
  input.onchange=()=>{draft.storage.disks=input.checked?[...draft.storage.disks,d.id]:draft.storage.disks.filter(id=>id!==d.id);invalidate();renderInventory();updateSelection();};
  const header=node('div','disk-top');header.append(node('span','disk-icon','▤'),input);card.append(header,node('strong','disk-model',d.model),node('span','disk-size',`${size(d.size_bytes)} · ${(d.transport||'?').toUpperCase()}`),node('code','disk-serial',d.serial||d.id),node('span','disk-state',diskReason(d)));list.append(card);
 }
 renderCapacity();const capable=['ssse3','sse4_1','sse4_2','popcnt','cx16','lahf_lm'].every(f=>(inventory.cpu_flags||[]).includes(f));$('machine-learning').disabled=!capable;if(!capable)draft.machine_learning=false;$('machine-learning').checked=draft.machine_learning;text('ml-hint',t(capable?'mlHint':'mlUnsupported'));
 for(const c of document.querySelectorAll('.app-card input'))c.checked=draft.apps.includes(c.value);$('timezone').value=draft.timezone;
}
function renderCapacity(){
 const mode=draft.storage.mode,disks=chosenDisks(),n=disks.length,min=n?Math.min(...disks.map(d=>Number(d.size_bytes))):0;
 const capacity=({single:min,raid1:min,raid5:Math.max(0,n-1)*min,raid6:Math.max(0,n-2)*min})[mode]||0;
 $('existing-options').hidden=mode!=='existing';text('layout-hint',t('layout_'+mode));text('capacity-value',mode==='existing'?t('existingCapacity'):validStorage()?size(capacity):'—');text('capacity-binary',mode!=='existing'&&validStorage()?`${(capacity/2**40).toLocaleString(lang,{maximumFractionDigits:2})} TiB · ${t('beforeOverhead')}`:'');text('tolerance-value',mode==='existing'?t('checkOmv'):toleranceLabel(mode==='single'?0:mode==='raid6'?2:1));$('mixed-capacity').hidden=new Set(disks.map(d=>d.size_bytes)).size<2;
}
function inputField(label,cls,type,value,oninput){const wrap=node('label','field');wrap.append(node('span','',label));const input=node('input',cls);input.type=type;input.value=value||'';input.autocomplete=type==='password'?'new-password':'off';input.maxLength=type==='password'?128:32;input.oninput=()=>oninput(input.value);wrap.append(input);return wrap;}
function renderUsers(){
 const list=$('users-list');list.replaceChildren();draft.users.forEach((u,i)=>{
  const row=node('div','user-row');row.append(inputField(t('username'),'user-name','text',u.name,value=>{const old=u.name;u.name=value;for(const s of draft.shares){s.permissions[value]=s.permissions[old]||'none';if(old!==value)delete s.permissions[old];}invalidate();renderShares();}),inputField(t('password'),'user-password','password',passwords[i]?.password,value=>{passwords[i]={...passwords[i],password:value};}),inputField(t('passwordConfirm'),'user-confirm','password',passwords[i]?.confirm,value=>{passwords[i]={...passwords[i],confirm:value};}));
  const remove=node('button','text-button',t('remove'));remove.type='button';remove.setAttribute('aria-label',t('removeUser',{name:u.name||i+1}));remove.onclick=()=>{draft.users.splice(i,1);passwords.splice(i,1);for(const s of draft.shares)delete s.permissions[u.name];invalidate();renderUsers();renderShares();};row.append(remove);list.append(row);
 });
 if(!draft.users.length)list.append(node('p','hint',t('noUsers')));
}
function renderShares(){
 const list=$('shares-list');list.replaceChildren();draft.shares.forEach((s,i)=>{
  const row=node('div','share-row');row.dataset.share=s.folder;
  const heading=node('div','share-heading'),label=node('label','share-toggle'),enabled=node('input');enabled.type='checkbox';enabled.checked=s.enabled;enabled.setAttribute('aria-label',t('enableShare',{name:s.name}));enabled.onchange=()=>{s.enabled=enabled.checked;invalidate();renderShares();};label.append(enabled,node('span','',t('folder_'+s.folder)));heading.append(label);
  const name=node('input','share-name');name.value=s.name;name.maxLength=64;name.disabled=!s.enabled;name.setAttribute('aria-label',t('shareName',{folder:t('folder_'+s.folder)}));name.oninput=()=>{s.name=name.value;invalidate();};heading.append(name);row.append(heading);
  if(s.enabled){const matrix=node('div','permissions-matrix');draft.users.forEach((u,j)=>{const field=node('label','permission-field');field.append(node('span','',u.name||t('userNumber',{n:j+1})));const select=node('select','permission-select');select.setAttribute('aria-label',t('permissionFor',{user:u.name||j+1,share:s.name}));for(const value of ['rw','ro','none'])select.add(new Option(t('permission_'+value),value));select.value=s.permissions[u.name]||'none';select.onchange=()=>{s.permissions[u.name]=select.value;invalidate();};field.append(select);matrix.append(field);});row.append(matrix);}
  list.append(row);
 });
}
function validateUsers(){const names=draft.users.map(u=>u.name.trim());if(names.some(n=>!/^[a-z][a-z0-9_-]{0,30}$/.test(n))||new Set(names).size!==names.length)throw new Error('username_invalid');if(draft.shares.some(s=>s.enabled)&&!names.length)throw new Error('permissions_invalid');for(let i=0;i<names.length;i++){const p=passwords[i]||{};if(!p.password||p.password.length<12||p.password.length>128)throw new Error('password_required');if(p.password!==p.confirm)throw new Error('password_mismatch');}}
function labelStep(id){return ['immich','jellyfin','tailscale'].includes(id)?{immich:'Immich',jellyfin:'Jellyfin',tailscale:'Tailscale'}[id]:t(id);}
function renderPlan(){
 const summary=$('review-summary');summary.replaceChildren(node('h3','',t('mode_'+plan.selection.storage.mode)),node('p','',`${size(plan.capacity_bytes)} · ${plan.selection.storage.mode==='existing'?t('checkOmv'):toleranceLabel(plan.tolerance||0)}`));
 for(const s of plan.selection.shares||[]){summary.append(node('strong','',s.name),node('p','',Object.entries(s.permissions).map(([u,p])=>`${u}: ${t('permission_'+p)}`).join(' · ')));}
 if(plan.selection.users?.length)summary.append(node('p','',`${t('fileUsers')}: ${plan.selection.users.map(u=>u.name).join(', ')}`));
 if(!plan.selection.apps.length)summary.append(node('p','',t('noApps')));
 const rows=$('plan-list');rows.replaceChildren();for(const [i,step] of plan.steps.entries()){const row=node('div','plan-row');row.append(node('strong','',labelStep(typeof step==='string'?step:step.id)),node('span','',String(i+1).padStart(2,'0')));rows.append(row);}
 text('plan-storage',t(plan.recovery?'recoveryHint':plan.destructive?'newFilesystem':'reuseHint'));$('warnings').replaceChildren();for(const warning of plan.warnings||[])$('warnings').append(node('div','note',dictionaries[lang]?.['warning_'+warning]||t('planWarning')));
 const checked=new Set([...document.querySelectorAll('.erase-check:checked')].map(c=>c.value)),targets=$('erase-targets');targets.replaceChildren();$('erase-review').hidden=!(plan.destructive||plan.confirmation_required);
 for(const d of plan.targets||[]){const label=node('label','erase-target'),check=node('input','erase-check');check.type='checkbox';check.value=d.id;check.checked=checked.has(d.id);check.onchange=updateSelection;const detail=node('span');detail.append(node('strong','',`${d.model} · ${size(d.size_bytes)}`),node('code','',`${d.serial||d.id} · ${(d.transport||'').toUpperCase()}`),node('span','',t('eraseAcknowledge')));const signatures=d.signatures||inventory.disks.find(x=>x.id===d.id)?.signatures||[];detail.append(node('small','',`${t('signatures')}: ${signatures.length?signatures.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(', '):t('noSignatures')}`));label.append(check,detail);targets.append(label);}
 updateSelection();
}
async function review(){validateUsers();plan=await api('setup/plan','POST',selection());$('erase-targets').replaceChildren();$('erase-confirmation').value='';renderPlan();show('review');}
function raidPending(){const raid=job?.selection?.storage?.mode?.startsWith('raid')?job?.resources?.raid:null;return job?.status==='waiting-for-sync'||raid&&['syncing','resyncing','recovering','degraded','pending','unknown'].includes(raid.state);}
function renderJob(){
 if(!['failed','interrupted','paused'].includes(job.status)){pendingInfo=null;pendingChecked=null;}
 const status=job.status,pending=raidPending();text('progress-title',t((pending&&status==='succeeded'?'waiting-for-sync':status)+'Title'));text('progress-description',t((pending&&status==='succeeded'?'waiting-for-sync':status)+'Description'));
 const steps=job.steps||[],done=steps.filter(s=>s.status==='succeeded').length,percent=steps.length?Math.round(done/steps.length*100):0;text('progress-caption',t('stepsComplete',{done,total:steps.length}));text('progress-percent',percent+'%');$('progress-bar').setAttribute('aria-valuenow',String(percent));$('progress-bar').setAttribute('aria-valuetext',t('stepsComplete',{done,total:steps.length}));$('progress-fill').style.width=percent+'%';
 const nextStepsKey=JSON.stringify([job.id,lang,steps.map(s=>[s.id,s.status])]);
 if(stepsKey!==nextStepsKey){stepsKey=nextStepsKey;$('job-steps').replaceChildren();for(const [i,s] of steps.entries()){const li=node('li');li.dataset.status=s.status;li.append(node('span','step-icon',s.status==='succeeded'?'✓':String(i+1)),node('span','',labelStep(s.id)),node('span','step-status',t(s.status)));$('job-steps').append(li);}}
 renderActivity();
 $('pause').hidden=status!=='running';$('pause').disabled=job.pause_requested;text('pause',t(job.pause_requested?'pausePending':'pause'));$('resume').hidden=!['failed','interrupted','paused','waiting-for-sync'].includes(status);text('resume',t(status==='waiting-for-sync'?'checkSync':'resume'));$('finish').hidden=status!=='succeeded'||!!pending;
 $('job-error').hidden=!job.error;if(job.error)text('job-error',message(job.error));$('tailscale-pair').hidden=status!=='waiting-for-user';
 const raid=job.selection?.storage?.mode?.startsWith('raid')?job.resources?.raid:null;$('raid-status').hidden=!raid;if(raid)text('raid-status',`${t('raid_'+raid.state)===('raid_'+raid.state)?t('raid_unknown'):t('raid_'+raid.state)}${raid.progress!==undefined&&raid.progress!==null?' · '+Number(raid.progress).toFixed(0)+'%':''}`);
 const retrying=job.setup&&['failed','interrupted','paused'].includes(status);
 const required=retrying?(job.resume_password_users??(!steps.some(s=>s.id==='storage_setup'&&s.status==='succeeded')?(job.selection?.users||[]).map(u=>u.name):[])):[];
 $('resume-credentials').hidden=!required.length;
 const existing=[...document.querySelectorAll('.resume-password')].map(e=>e.dataset.user);
 if(JSON.stringify(existing)!==JSON.stringify(required)){
  $('resume-passwords').replaceChildren();
  for(const name of required){const f=inputField(`${t('password')} · ${name}`,'resume-password','password','',()=>{});f.querySelector('input').dataset.user=name;$('resume-passwords').append(f);}
 }
 for(const input of document.querySelectorAll('.resume-password'))input.parentElement.firstElementChild.textContent=`${t('password')} · ${input.dataset.user}`;
 $('results').replaceChildren();if(job.resources?.shares?.length){const panel=node('div','panel share-results');panel.append(node('h3','',t('sharedFolders')),node('p','',demo?t('demoShares'):t('sharesReady')));const host=demo?'NAS-IP':location.hostname;for(const s of job.resources.shares){panel.append(node('strong','',s.name),node('code','',`\\\\${host}\\${s.name}`),node('code','',`smb://${host}/${encodeURIComponent(s.name)}`));}panel.append(node('p','',`${t('fileUsers')}: ${(job.selection?.users||[]).map(u=>u.name).join(', ')}`),node('p','',t('backupReminder')));if(!demo){const a=node('a','secondary',t('manageOmv'));a.href=`http://${location.hostname}/`;a.target='_blank';a.rel='noopener noreferrer';panel.append(a);}$('results').append(panel);}
 if(['succeeded','waiting-for-sync'].includes(status))for(const [app,details] of Object.entries(job.links||{})){const panel=node('div','panel');panel.append(node('h3','',labelStep(app)),node('p','',t(demo?'demoResult':app==='tailscale'?'remoteReady':'serviceReady')));if(!demo&&details.port){const a=node('a','secondary',t('openApp'));a.href=`http://${location.hostname}:${details.port}`;a.target='_blank';a.rel='noopener noreferrer';panel.append(a);}if(!demo&&details.ips?.length)panel.append(node('p','',details.ips.join(' · ')));$('results').append(panel);}
 renderPending();
}
function renderActivity(){
 const active=job?.status==='running';$('activity-panel').hidden=!active;
 if(!active)return;
 const step=(job.steps||[]).find(s=>s.status==='running');
 text('activity-step',step?labelStep(step.id):t('activityPreparing'));
 text('activity-label',step?.activity?t('activity_'+step.activity):t('activityWorking'));
 const age=lastJobAt?Math.max(0,Date.now()-lastJobAt):0;
 const connected=!!lastJobAt&&!connectionFailed&&age<10000;
 $('activity-panel').dataset.connected=String(connected);$('job-steps').dataset.connected=String(connected);
 text('activity-connection',t(connected?'activityConnected':'activityDisconnected'));
 const hasTime=Number.isFinite(step?.started_at)&&Number.isFinite(job.observed_at);
 $('activity-elapsed').hidden=!hasTime;
 if(hasTime){const elapsed=Math.max(0,Math.floor(job.observed_at-step.started_at+age/1000));const duration=`${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')}`;text('activity-elapsed',t('activityElapsed',{duration}));}
}
function renderPending(){
 $('pending-config').hidden=!pendingInfo;
 $('pending-modules').replaceChildren(...(pendingInfo?.modules||[]).map(name=>node('li','',dictionaries[lang]?.['module_'+name]?`${t('module_'+name)} (${name})`:name)));
 if(pendingInfo){$('resume').hidden=true;$('job-error').hidden=true;}
}
async function loadPending(){
 if(job?.error!=='omv_configuration_changed'||!['failed','interrupted','paused'].includes(job.status)||pendingChecked===job.id)return;
 if(pendingRequest)return pendingRequest;
 const id=job.id;
 pendingRequest=(async()=>{const info=await api('setup/recovery','POST',{});
  if(job.id===id&&['failed','interrupted','paused'].includes(job.status)){
   pendingChecked=id;pendingInfo=info.pending_id&&!(job.selection?.storage?.mode?.startsWith('raid')&&info.modules.length===1&&info.modules[0]==='mdadm')?info:null;renderPending();
  }
 })();
 try{await pendingRequest;}finally{pendingRequest=null;}
}
async function poll(force=false){
 if(polling||(!force&&busyActions)||screen==='finished')return;
 const epoch=jobEpoch;polling=true;
 try{
  const current=await api('job');if(epoch!==jobEpoch)return;
  lastJobAt=Date.now();if(connectionFailed)error(null);connectionFailed=false;
  if(current){job=current;if(screen!=='progress')show('progress');renderJob();await loadPending();
   if(job.status==='waiting-for-user'){const ts=await api('tailscale');if(epoch!==jobEpoch)return;const link=$('tailscale-link');link.hidden=!ts.url;if(ts.url?.startsWith('https://login.tailscale.com/'))link.href=ts.url;else link.removeAttribute('href');}
  }
 }catch(e){if(epoch===jobEpoch){connectionFailed=true;renderActivity();error(e.message);}}
 finally{polling=false;}
}
async function entered(){
 inventory=await api('inventory');if(!draft.shares.length&&!localStorage.getItem('nas-setup-draft'))draft.shares=['files','media','backups'].map(folder=>({name:t('folder_'+folder),folder,permissions:{'':'rw'},enabled:true}));
 if(inventory.provisioning_enabled===false)draft.storage={mode:'existing',disks:[],volume:draft.storage.volume};renderInventory();renderUsers();renderShares();updateSelection();const savedScreen=screen;await poll(true);if(!job)show(['storage','shares','apps'].includes(savedScreen)?savedScreen:'storage');
}
async function resume(approvePending=false){
 await loadPending();
 if(pendingInfo&&!approvePending){renderPending();return;}
 const credentials=$('resume-credentials').hidden?{}:Object.fromEntries([...document.querySelectorAll('.resume-password')].filter(e=>e.value).map(e=>[e.dataset.user,e.value]));
 try{job=await api(job.setup?'setup/resume':'resume','POST',job.setup?{passwords:credentials,...(approvePending&&pendingInfo?{pending_id:pendingInfo.pending_id}:{})}:undefined);}
 catch(e){if(['omv_configuration_changed','confirmation_required'].includes(e.message)){pendingInfo=null;pendingChecked=null;await loadPending();}throw e;}
 document.querySelectorAll('.resume-password').forEach(input=>input.value='');
 renderJob();
}
async function init(){
 dictionaries=await fetch('/static/i18n.json').then(r=>r.json());if(!dictionaries[lang])lang='pt-BR';applyLanguage();const session=await api('session');demo=session.demo;$('demo-banner').hidden=!demo;$('demo-login').hidden=!demo;if(session.authenticated){csrf=session.csrf;await entered();}
 $('lang-pt').onclick=()=>{lang='pt-BR';applyLanguage();};$('lang-en').onclick=()=>{lang='en';applyLanguage();};
 $('login-form').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const result=await api('login','POST',{code:$('access-code').value.trim()});csrf=result.csrf;$('access-code').value='';await entered();});};$('demo-login').onclick=e=>action(e.currentTarget,async()=>{const result=await api('demo-login','POST');csrf=result.csrf;await entered();});
 $('storage-mode').onchange=()=>{draft.storage.mode=$('storage-mode').value;invalidate();renderInventory();updateSelection();};$('volume').onchange=()=>{draft.storage.volume=$('volume').value||null;invalidate();updateSelection();};
 $('storage-next').onclick=()=>{if(validStorage())show('shares');};$('shares-next').onclick=e=>action(e.currentTarget,async()=>{validateUsers();show('apps');});
 $('add-user').onclick=()=>{draft.users.push({name:''});passwords.push({});invalidate();renderUsers();renderShares();};$('add-share').onclick=()=>{draft.shares.push({name:t('newShare'),folder:'custom',permissions:{},enabled:true});invalidate();renderShares();};
 for(const c of document.querySelectorAll('.app-card input'))c.onchange=()=>{draft.apps=[...document.querySelectorAll('.app-card input:checked')].map(i=>i.value);invalidate();updateSelection();};$('timezone').oninput=()=>{draft.timezone=$('timezone').value;invalidate();};$('machine-learning').onchange=()=>{draft.machine_learning=$('machine-learning').checked;invalidate();};
 $('apps-next').onclick=e=>action(e.currentTarget,review);for(const b of document.querySelectorAll('.back'))b.onclick=()=>{invalidate();show(b.dataset.target);};$('erase-confirmation').oninput=updateSelection;
 $('install').onclick=e=>action(e.currentTarget,async()=>{const creds=Object.fromEntries(draft.users.map((u,i)=>[u.name.trim(),passwords[i]?.password||'']));job=await api('setup/start','POST',{plan_id:plan.plan_id,confirmation:$('erase-confirmation').value,erase_disks:[...document.querySelectorAll('.erase-check:checked')].map(c=>c.value),passwords:creds});passwords=[];document.querySelectorAll('input[type=password]').forEach(i=>i.value='');localStorage.removeItem('nas-setup-draft');show('progress');renderJob();});
 $('pause').onclick=e=>action(e.currentTarget,async()=>{job=await api('pause','POST');renderJob();});$('resume').onclick=e=>action(e.currentTarget,resume);$('apply-pending').onclick=e=>action(e.currentTarget,()=>resume(true));$('check-tailscale').onclick=e=>action(e.currentTarget,resume);$('finish').onclick=e=>action(e.currentTarget,async()=>{await api('finish','POST');show('finished');});
 setInterval(()=>{if(csrf&&screen==='progress')poll();},1500);
 setInterval(()=>{if(screen==='progress')renderActivity();},1000);
}
init().catch(e=>error(e.message));
