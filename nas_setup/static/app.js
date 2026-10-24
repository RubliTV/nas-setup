'use strict';
const $=id=>document.getElementById(id);
let dictionaries={},lang=localStorage.getItem('nas-language')||'pt-BR',csrf='',screen='login',inventory=null,plan=null,job=null,demo=false,polling=false,errorCode=null;
const t=(key,values={})=>Object.entries(values).reduce((s,[k,v])=>s.replaceAll(`{${k}}`,String(v)),dictionaries[lang]?.[key]||key);
const text=(id,value)=>$(id).textContent=value;
function error(code){errorCode=code;$('error').hidden=!code;if(code)text('error',t('error_'+code));}
async function api(path,method='GET',body){
 let response;
 try{response=await fetch('/api/'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});}
 catch{throw new Error('network_error');}
 let data;try{data=await response.json();}catch{throw new Error('network_error');}
 if(!response.ok)throw new Error(data.error||(response.status===422?'request_invalid':'installation_failed'));
 return data;
}
async function action(button,fn){
 button.disabled=true;error(null);
 try{await fn();}catch(e){error(e.message);}finally{button.disabled=false;updateSelection();}
}
function show(name){screen=name;document.querySelectorAll('.screen').forEach(s=>s.hidden=s.id!=='screen-'+name);document.querySelectorAll('#navigation li').forEach(s=>s.classList.toggle('active',s.dataset.step===name));error(null);}
function selection(){return {apps:[...document.querySelectorAll('.app-card input:checked')].map(i=>i.value),volume:$('volume').value||null,database_volume:$('database-volume').value||null,machine_learning:$('machine-learning').checked,timezone:$('timezone').value.trim()};}
function updateSelection(){
 const selected=selection();document.querySelectorAll('.app-card').forEach(c=>c.classList.toggle('selected',c.querySelector('input').checked));
 text('selection-count',t('selected',{count:selected.apps.length}));
 $('apps-next').disabled=!selected.apps.length||!inventory?.supported;
 $('database-options').hidden=!selected.apps.includes('immich');
}
function applyLanguage(){
 document.documentElement.lang=lang;localStorage.setItem('nas-language',lang);
 document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n));
 $('lang-pt').classList.toggle('active',lang==='pt-BR');$('lang-en').classList.toggle('active',lang==='en');
 if(inventory)renderInventory();if(plan)renderPlan();if(job)renderJob();if(errorCode)text('error',t('error_'+errorCode));updateSelection();
}
function fillVolumes(id){
 const select=$(id),previous=select.value;select.replaceChildren(new Option(t('chooseVolume'),''));
 for(const volume of inventory.volumes)select.add(new Option(`${volume.label} — ${t('free',{space:volume.free_gb})}`,volume.id));
 select.value=previous;
}
function renderInventory(){
 text('hostname',inventory.hostname);text('host-os',inventory.os);text('cpu',inventory.cpu);text('memory',inventory.memory_gb+' GB RAM');text('host-badge',t(inventory.supported?'compatible':'incompatible'));
 fillVolumes('volume');fillVolumes('database-volume');
 const capable=['ssse3','sse4_1','sse4_2','popcnt','cx16','lahf_lm'].every(f=>inventory.cpu_flags.includes(f));
 $('machine-learning').disabled=!capable;if(!capable)$('machine-learning').checked=false;
 text('ml-hint',t(capable?'mlHint':'mlUnsupported'));
}
function labelStep(id){return ['immich','jellyfin','tailscale'].includes(id)?{immich:'Immich',jellyfin:'Jellyfin',tailscale:'Tailscale'}[id]:t(id);}
function node(tag,cls,value){const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=value;return e;}
function renderPlan(){
 const rows=$('plan-list');rows.replaceChildren();
 plan.steps.forEach((step,i)=>{const row=node('div','plan-row');row.append(node('strong','',labelStep(step)),node('span','',String(i+1).padStart(2,'0')));rows.append(row);});
 const vol=inventory.volumes.find(v=>v.id===plan.selection.volume),db=inventory.volumes.find(v=>v.id===plan.selection.database_volume);
 const storageParts=vol?[t('filesMedia')+': '+vol.label]:[];
 if(db&&plan.apps.includes('immich'))storageParts.push(t('database')+': '+db.label);
 text('plan-storage',storageParts.join(' · '));
 $('warnings').replaceChildren();for(const warning of plan.warnings)$('warnings').append(node('div','note',t('warning_'+warning)));
}
async function review(){plan=await api('plan','POST',selection());renderPlan();$('review-back').dataset.target=selection().apps.some(a=>a!=='tailscale')?'storage':'apps';show('review');}
function renderJob(){
 const status=job.status; text('progress-title',t(status+'Title'));text('progress-description',t(status+'Description'));
 const done=job.steps.filter(s=>s.status==='succeeded').length,percent=Math.round(done/job.steps.length*100);
 text('progress-caption',t('stepsComplete',{done,total:job.steps.length}));text('progress-percent',percent+'%');$('progress-bar').value=percent;
 $('job-steps').replaceChildren();job.steps.forEach((s,i)=>{const li=node('li');li.dataset.status=s.status;li.append(node('span','step-icon',s.status==='succeeded'?'✓':String(i+1)),node('span','',labelStep(s.id)),node('span','step-status',t(s.status)));$('job-steps').append(li);});
 $('pause').hidden=status!=='running';$('pause').disabled=job.pause_requested;
 $('resume').hidden=!['failed','interrupted','paused'].includes(status);$('finish').hidden=status!=='succeeded';
 $('job-error').hidden=!job.error;if(job.error)text('job-error',t('error_'+job.error));
 $('tailscale-pair').hidden=status!=='waiting-for-user';
 $('results').replaceChildren();
 if(status==='succeeded')for(const [app,details] of Object.entries(job.links)){
  const panel=node('div','panel');panel.append(node('h3','',labelStep(app)),node('p','',t(demo?'demoResult':app==='tailscale'?'remoteReady':'serviceReady')));
  if(!demo&&details.port){const a=node('a','secondary',t('openApp'));a.href=`http://${location.hostname}:${details.port}`;a.target='_blank';a.rel='noopener noreferrer';panel.append(a);}
  if(!demo&&details.ips?.length)panel.append(node('p','',details.ips.join(' · ')));
  $('results').append(panel);
 }
}
async function poll(){
 if(polling||screen==='finished')return;polling=true;
 try{
  const current=await api('job');if(current){job=current;if(screen!=='progress')show('progress');renderJob();
   if(job.status==='waiting-for-user'){
    const ts=await api('tailscale');const link=$('tailscale-link');link.hidden=!ts.url;
    if(ts.url&&ts.url.startsWith('https://login.tailscale.com/'))link.href=ts.url;else link.removeAttribute('href');
   }
  }
 }catch(e){error(e.message);}finally{polling=false;}
}
async function entered(){inventory=await api('inventory');renderInventory();updateSelection();await poll();if(!job)show('apps');}
async function init(){
 dictionaries=await fetch('/static/i18n.json').then(r=>r.json());if(!dictionaries[lang])lang='pt-BR';applyLanguage();
 const session=await api('session');demo=session.demo;$('demo-banner').hidden=!demo;$('demo-login').hidden=!demo;
 if(session.authenticated){csrf=session.csrf;await entered();}
 $('lang-pt').onclick=()=>{lang='pt-BR';applyLanguage();};$('lang-en').onclick=()=>{lang='en';applyLanguage();};
 document.querySelectorAll('.app-card input').forEach(c=>c.onchange=updateSelection);
 $('login-form').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{const result=await api('login','POST',{code:$('access-code').value.trim()});csrf=result.csrf;$('access-code').value='';await entered();});};
 $('demo-login').onclick=e=>action(e.currentTarget,async()=>{const result=await api('demo-login','POST');csrf=result.csrf;await entered();});
 $('apps-next').onclick=e=>action(e.currentTarget,async()=>{if(selection().apps.some(a=>a!=='tailscale'))show('storage');else await review();});
 $('storage-next').onclick=e=>action(e.currentTarget,review);
 document.querySelectorAll('.back').forEach(b=>b.onclick=()=>show(b.dataset.target));
 $('install').onclick=e=>action(e.currentTarget,async()=>{job=await api('install','POST',plan.selection);show('progress');renderJob();});
 $('pause').onclick=e=>action(e.currentTarget,async()=>{job=await api('pause','POST');renderJob();});
 $('resume').onclick=e=>action(e.currentTarget,async()=>{job=await api('resume','POST');renderJob();});
 $('check-tailscale').onclick=e=>action(e.currentTarget,async()=>{job=await api('resume','POST');renderJob();});
 $('finish').onclick=e=>action(e.currentTarget,async()=>{await api('finish','POST');show('finished');});
 setInterval(()=>{if(csrf&&screen==='progress')poll();},1500);
}
init().catch(e=>error(e.message));
