const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'remote-download-ui-'));
const source = path.resolve(__dirname);
fs.writeFileSync(path.join(folder, 'server-config.json'), JSON.stringify({host:'127.0.0.1',port:18765,adminToken:'browser-test-secret-long'}));
const server = spawn(process.env.TEST_PYTHON || 'python3', ['-u', path.join(source, 'server.py')], {cwd:folder, stdio:'ignore'});
let browser;

(async () => {
  const base = 'http://127.0.0.1:18765';
  for (let attempt=0;attempt<60;attempt++) {
    try { await fetch(base); break; } catch (error) { await new Promise(resolve=>setTimeout(resolve,100)); }
  }
  browser = await chromium.launch({headless:true, ...(process.env.TEST_BROWSER ? {executablePath:process.env.TEST_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [];
  const queries = [];
  page.on('pageerror', error=>errors.push(String(error)));
  page.on('request', request=>{ if(request.url().includes('/inventory'))queries.push(request.url()); });
  await page.goto(base);
  await page.locator('#token').fill('browser-test-secret-long');
  await page.getByRole('button',{name:'进入后台'}).click();
  await page.locator('#app').waitFor({state:'visible'});
  await page.getByRole('button',{name:'添加网吧',exact:true}).click();
  await page.locator('#new-name').fill('测试网吧');
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('button',{name:'添加并下载配置'}).click();
  const download = await downloadPromise;
  const configPath = path.join(folder,'agent-config.json');
  await download.saveAs(configPath);
  const config = JSON.parse(fs.readFileSync(configPath,'utf8'));

  async function api(route, data, secret=config.agentToken) {
    const response=await fetch(base+route,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+secret,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
    if(!response.ok)throw new Error(await response.text());
    return response.json();
  }
  await api('/api/agents/register',{cafeId:config.cafeId});
  await api('/api/agents/inventory',{cafeId:config.cafeId,complete:true,games:[{gameId:5131,name:'Roblox',status:'not_installed',sizeBytes:1048576},{gameId:8044,name:'CSGO',status:'installed',localPath:'F:\\CSGO'}],disks:[{path:'F:\\',totalBytes:214748364800,freeBytes:107374182400,downloadDisk:true}]});
  await page.locator('#back').click();
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#cafes').textContent.includes('在线'));
  if(queries.length)throw new Error('Homepage loaded full game inventory');
  await page.getByRole('button',{name:'进入网吧',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#disk-note').textContent.includes('100.0 GB'));
  if(await page.locator('.game-row').count())throw new Error('Full game list visible before searching');
  await page.locator('#game-search').fill('Rob');
  await page.locator('.game-row').filter({hasText:'Roblox'}).waitFor();
  if(await page.locator('.game-row').count()!==1)throw new Error('Search returns unrelated games');
  if(!queries.every(url=>url.includes('query=Rob')))throw new Error('Unfiltered inventory query');

  await page.evaluate(()=>{
    window.resultDisappearances=0;
    window.resultObserver=new MutationObserver(()=>{
      if(!document.querySelector('#game-results').textContent.includes('Roblox'))window.resultDisappearances++;
    });
    window.resultObserver.observe(document.querySelector('#game-results'),{childList:true,subtree:true});
  });
  await page.route('**/api/cafes/*/inventory?*',async route=>{
    await new Promise(resolve=>setTimeout(resolve,600));
    await route.continue();
  });
  await page.waitForTimeout(6500);
  if(await page.evaluate(()=>window.resultDisappearances)!==0)throw new Error('Game results disappeared during polling');
  await page.unroute('**/api/cafes/*/inventory?*');
  const staleButton=await page.getByRole('button',{name:'下发下载',exact:true}).elementHandle();
  const second=await api('/api/cafes',{name:'第二家网吧',server:base},'browser-test-secret-long');
  await page.locator('#back').click();
  await page.locator('#refresh').click();
  await page.locator('[data-cafe-id="'+second.id+'"]').waitFor();
  await page.locator('[data-cafe-id="'+second.id+'"]').getByRole('button',{name:'进入网吧'}).click();
  await staleButton.evaluate(button=>button.click());
  if((await api('/api/state',null,'browser-test-secret-long')).tasks.length)throw new Error('Detached search button sent task to wrong cafe');
  await page.locator('#back').click();
  await page.locator('[data-cafe-id="'+config.cafeId+'"]').getByRole('button',{name:'进入网吧'}).click();
  await page.locator('#game-search').fill('5131');
  await page.locator('.game-row').filter({hasText:'Roblox'}).waitFor();
  await page.getByRole('button',{name:'下发下载',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('等待下发'));
  const task=(await api('/api/state',null,'browser-test-secret-long')).tasks[0];
  if(task.cafeId!==config.cafeId)throw new Error('Wrong target cafe');
  await api('/api/tasks/next/'+config.cafeId,null,config.agentToken);
  await api('/api/tasks/'+task.id+'/status',{status:'accepted'},config.agentToken);
  await api('/api/tasks/'+task.id+'/telemetry',{downloadedBytes:524288,totalBytes:1048576,speedBytesPerSecond:131072,sampledAt:Date.now()/1000,source:'filesystem'},config.agentToken);
  await page.evaluate(()=>refreshState());
  await page.waitForFunction(()=>document.querySelector('#tasks').textContent.includes('进度：50%')&&document.querySelector('#tasks').textContent.includes('剩余时间：4秒'));
  page.once('dialog',dialog=>dialog.accept('更名后的网吧'));
  await page.locator('#rename').click();
  await page.waitForFunction(()=>document.querySelector('#detail-name').textContent==='更名后的网吧');
  await page.locator('#game-search').fill('');
  if(await page.locator('.game-row').count())throw new Error('Results remain after clearing search');
  await page.waitForFunction(()=>!polling);
  let releaseOldResponse;
  let observeOldRequest;
  const held=new Promise(resolve=>releaseOldResponse=resolve);
  const observed=new Promise(resolve=>observeOldRequest=resolve);
  let intercept=true;
  await page.route('**/api/state',async route=>{
    if(!intercept){await route.continue();return;}
    intercept=false;
    observeOldRequest();
    await held;
    await route.fulfill({contentType:'application/json',body:JSON.stringify({cafes:[{id:'stale-session',name:'旧会话数据',online:true}],tasks:[]})});
  });
  const oldRefresh=page.evaluate(()=>refreshState());
  await observed;
  await page.locator('#logout').click();
  await page.locator('#token').fill('browser-test-secret-long');
  await page.getByRole('button',{name:'进入后台'}).click();
  await page.locator('#dashboard').waitFor({state:'visible',timeout:3000});
  if(await page.locator('#detail').isVisible())throw new Error('Old cafe detail survived logout/login');
  releaseOldResponse();
  await oldRefresh;
  if((await page.locator('#cafes').innerText()).includes('旧会话数据'))throw new Error('Old response crossed login session');
  await page.unroute('**/api/state');
  await page.setViewportSize({width:390,height:844});
  if(process.env.TEST_SCREENSHOT)await page.screenshot({path:process.env.TEST_SCREENSHOT,fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth))throw new Error('Mobile horizontal overflow');
  if(errors.length)throw new Error(errors.join('\n'));
  console.log('PASS two-level navigation, filtered search, no poll flicker, cafe isolation, task, rename and mobile layout');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  server.kill();
  fs.rmSync(folder,{recursive:true,force:true});
});
