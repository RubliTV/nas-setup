const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {spawn}=require('node:child_process');
const {mkdtempSync,rmSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
(async()=>{
 const state=mkdtempSync(join(tmpdir(),'nas-browser-'));
 const url=process.env.NAS_DEMO_URL||'http://127.0.0.1:8767';
 const server=process.env.NAS_DEMO_URL?null:spawn('.venv/bin/nas-setup',['demo','--state',state,'--port','8767'],{stdio:'ignore'});
 let ready=false;
 for(let i=0;i<100;i++){try{await fetch(url+'/api/session');ready=true;break;}catch{await new Promise(r=>setTimeout(r,100));}}
 if(!ready){server?.kill();throw new Error('demo did not start');}
 let browser;
 try{
 browser=await chromium.launch({headless:true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE}: {})});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(5000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.getByRole('button',{name:'Explorar demonstração'}).click();
 await page.getByRole('heading',{name:'Escolha o que seu NAS vai fazer.'}).waitFor();
 await page.screenshot({path:'test-results/apps-pt.png',fullPage:true});
 await page.getByLabel('Jellyfin', {exact:true}).check();
 await page.getByRole('button',{name:'English',exact:true}).click();
 assert(await page.getByLabel('Jellyfin', {exact:true}).isChecked());
 await page.getByRole('button',{name:'Continue',exact:true}).click();
 await page.getByLabel('Files and media').selectOption('demo-data');
 await page.getByRole('button',{name:'Review installation',exact:true}).click();
 await page.getByRole('button',{name:'Install selected apps',exact:true}).click();
 await page.getByRole('heading',{name:'Installation in progress.'}).waitFor();
 await page.getByRole('button',{name:'Português',exact:true}).click();
 await page.reload();
 await page.getByRole('heading',{name:'Seu NAS está pronto.'}).waitFor({timeout:45000});
 await page.screenshot({path:'test-results/completed-pt.png',fullPage:true});
 await page.getByRole('button',{name:'English',exact:true}).click();
 await page.getByRole('heading',{name:'Your NAS is ready.'}).waitFor();
 await page.screenshot({path:'test-results/completed-en.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
 await page.waitForTimeout(500);
 await page.screenshot({path:'test-results/mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('Browser flow passed: login, PT/EN, selection, storage, installation, reload recovery, mobile.');
 }finally{await browser?.close();server?.kill();rmSync(state,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1)});
