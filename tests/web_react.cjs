'use strict';
// Exercise the production bundle with synthetic Firebase/API responses. No production writes.
const { chromium } = require(process.env.COWORK_PLAYWRIGHT_PACKAGE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fixtures = require('./web_react_fixtures.cjs');
const root = path.resolve(__dirname, '..'), source = path.join(root, 'src/cowork_hub/web/dist');
const output = process.env.COWORK_BROWSER_OUTPUT || path.join(root, '.local/verification/react-migration/browser');
const errors = [], cspErrors = [], checks = [];
const sdk = `let listener;window.testAuth={popupCalls:0,emit(name){return listener(name?{uid:'firebase-'+name,email:name+'@example.test',getIdToken:async()=>'synthetic-'+name}:null)}};
export const initializeAuth=()=>({}),browserLocalPersistence={},browserPopupRedirectResolver={};
export function onAuthStateChanged(auth,callback){listener=callback;window.testAuth.ready=true;}
export async function signOut(){window.testAuth.emit(null)}
export async function signInWithPopup(){window.testAuth.popupCalls++}
export class GoogleAuthProvider{setCustomParameters(){}}`;
const until = async condition => { const deadline = Date.now() + 8000; while(!(await condition())) { if(Date.now() > deadline) throw Error('Expected API request did not arrive'); await new Promise(resolve => setTimeout(resolve, 10)); } };

(async () => {
  await fs.mkdir(output, { recursive: true });
  let reviewFont = null;
  try { reviewFont = await fs.readFile(process.env.COWORK_TEST_FONT || path.join(root,'.local/verification/mcp-guide/NotoSansCJKkr-Regular.otf')); }
  catch (error) { if(process.env.COWORK_TEST_FONT) throw error; }
  const headers = JSON.parse(spawnSync(path.join(root,'.venv/bin/python'), ['-c', 'import json; from cowork_hub.web_api import security_headers; print(json.dumps(security_headers()))'], { cwd:root, encoding:'utf8' }).stdout);
  const server = http.createServer(async (request,response) => {
    const name = new URL(request.url, 'http://localhost').pathname.slice(1) || 'index.html';
    for(const [key,value] of Object.entries(headers)) response.setHeader(key,value);
    if(name === 'config.json') { response.setHeader('Content-Type','application/json'); response.end(JSON.stringify({enabled:true,firebase:{projectId:'fixture-project'},api_base_url:''}));return; }
    if(name === 'review-font.otf' && reviewFont) { response.setHeader('Content-Type','font/otf');response.end(reviewFont);return; }
    if(!['index.html','favicon.svg'].includes(name) && !/^assets\/[\w.-]+\.(js|css|png)$/.test(name)) { response.writeHead(404);response.end();return; }
    response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : name.endsWith('.svg') ? 'image/svg+xml' : name.endsWith('.png') ? 'image/png' : 'text/html');
    let body = await fs.readFile(path.join(source,name));
    if(name.endsWith('.css') && reviewFont) body = Buffer.concat([body,Buffer.from("\n@font-face{font-family:Review;src:url('/review-font.otf')}body,input,button,select{font-family:Review,sans-serif}")]);
    response.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  let browser;
  try {
    browser = await chromium.launch({ headless:true, args:['--no-sandbox'] });
    async function open(hash = 'servers', name = 'alice', options = {}) {
      const context = await browser.newContext({ viewport:{width:1440,height:1050}, acceptDownloads:true });
      if(options.savedLanguage) await context.addInitScript(value=>localStorage.setItem('cowork.language.v1',value),options.savedLanguage);
      if(options.blockStorage) await context.addInitScript(()=>{Storage.prototype.getItem=()=>{throw Error('unavailable')};Storage.prototype.setItem=()=>{throw Error('unavailable')};});
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const state = { nodes:structuredClone(fixtures.nodes), profiles:{}, enrollment:{state:'NEW'}, enrollments:[structuredClone(fixtures.enrollment)], users:['alice','researcher'].map(id => ({id,enabled:true,uid:1201,gid:1200,allowed_nodes:['226','227'],notification:{configured:true,channel_id:'123'}})), tokens:[{id:'existing',name:'main-container-connection-with-a-long-name',created_at:1790654400,last_used_at:null,revoked_at:null}], mutations:[], requests:[], held:[], hold:new Set(), fail:{}, unlinked:new Set(['carol']) };
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if(/Content Security Policy|Refused to (apply|execute|load)/i.test(message.text())) cspErrors.push(message.text()); });
      await page.route('https://www.gstatic.com/firebasejs/**', route => route.fulfill({contentType:'text/javascript',body:route.request().url().endsWith('firebase-app.js')?'export const initializeApp=()=>({});':sdk}));
      const json = (route,body,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      await page.route('**/v1/web/**', async route => {
        const url=new URL(route.request().url()), endpoint=url.pathname.slice('/v1/web'.length), method=route.request().method();
        const user=route.request().headers().authorization?.replace('Bearer synthetic-','');
        const request={endpoint,method,route,user,body:method==='POST'?route.request().postDataJSON():null};
        state.requests.push(request);
        if(method!=='GET') state.mutations.push(request);
        if(state.hold.has(method+' '+endpoint)) { state.held.push(request);return; }
        if(state.fail[endpoint]) {const failure=state.fail[endpoint];delete state.fail[endpoint];return json(route,{error:{code:failure.code}},failure.status);}
        let body;
        if(endpoint==='/profile') { if(state.unlinked.has(user)) return json(route,{error:{code:'WEB_ACCOUNT_NOT_LINKED'}},403); body=state.profiles[user]||{...fixtures.profile,user_id:user,is_admin:user==='alice'}; }
        else if(endpoint==='/catalog') {const {files,...overview}=require('./web_catalog.cjs').data;body=overview;}
        else if(endpoint==='/catalog/files') body=require('./web_catalog.cjs').files(url.searchParams);
        else if(endpoint==='/onboarding') {if(method==='POST') state.enrollment={state:'PENDING',...request.body,channel_id:'fixture-channel',webhook_url:undefined};body=state.enrollment;}
        else if(endpoint==='/admin/servers'||endpoint==='/servers') body=state.nodes;
        else if(endpoint==='/admin/enrollments') body={...fixtures.pageData(state.enrollments),total:state.enrollments.length};
        else if(endpoint.endsWith('/review')) {state.enrollments=[];body={status:'ok'};}
        else if(endpoint==='/admin/users') body={...fixtures.pageData(state.users),notification_registration_available:true};
        else if(endpoint.endsWith('/grants')) {const id=endpoint.split('/')[3];state.users.find(u=>u.id===id).allowed_nodes=request.body.allowed_nodes;body={status:'ok'};}
        else if(/^\/admin\/users\/[^/]+\/notifications$/.test(endpoint)) {const id=endpoint.split('/')[3];state.users.find(u=>u.id===id).notification.channel_id='789';body={configured:true,channel_id:'789'};}
        else if(/^\/admin\/servers\/[^/]+$/.test(endpoint)) {Object.assign(state.nodes.find(n=>n.id===endpoint.split('/').at(-1)),request.body);body={status:'ok'};}
        else if(endpoint==='/admin/audit') body=fixtures.pageData([{created_at:1790654400,actor:'alice',action:'grants.updated',target:'researcher',before_value:{allowed_nodes:['226']},after_value:{allowed_nodes:['226','227']}}]);
        else if(endpoint==='/environments') body=fixtures.pageData([fixtures.environment]);
        else if(endpoint==='/connectors') body=[{id:'main',name:'메인 연구 컨테이너',online:true}];
        else if(endpoint==='/registrations') body=fixtures.pageData([{id:'registration',target:{node_id:'227',user:'alice',host:'203.255.11.227',port:11010,workdir:fixtures.environment.workdir},state:'PENDING',created_at:1790654400}]);
        else if(endpoint==='/ssh/parse') body={user:'alice',host:'203.255.11.227',port:11010};
        else if(endpoint==='/containers') body={state:'PENDING'};
        else if(endpoint==='/jobs'||/^\/servers\/\d+\/jobs$/.test(endpoint)) body=fixtures.pageData([fixtures.job]);
        else if(endpoint==='/notifications') body={configured:true,channel_id:'123'};
        else if(endpoint==='/tokens') {if(method==='POST') {const token={id:'new-token',name:request.body.name,created_at:1790654400,last_used_at:null,revoked_at:null};state.tokens.unshift(token);body={...token,token:'fixture_only_new_secret'};}else body=state.tokens;}
        else if(endpoint.startsWith('/tokens/')&&method==='DELETE') {state.tokens.find(t=>t.id===endpoint.split('/').at(-1)).revoked_at=1790654600;body={revoked:true};}
        else {errors.push('Unexpected endpoint '+method+' '+endpoint);return json(route,{},404);}
        if(body?.items && url.searchParams.has('page')) body={...body,page:Number(url.searchParams.get('page'))};
        await json(route,body);
      });
      await page.goto(origin+'/' + (options.language ? '?lang='+encodeURIComponent(options.language) : '') + '#'+hash);
      await until(() => page.evaluate(()=>window.testAuth?.ready));
      if(name!==false) {await page.evaluate(name=>window.testAuth.emit(name),name);await page.locator(name==='carol'?'#onboarding-form':'#portal').waitFor();}
      return {page,state,json,context};
    }
    async function navigate(page,hash) {await page.evaluate(hash=>{location.hash=hash;},hash);await until(() => page.evaluate(hash=>location.hash==='#'+hash,hash));await page.locator('main h1').waitFor();await page.evaluate(()=>document.fonts.ready);}
    async function layout(page,name,width) {
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const result=await page.evaluate(()=>{
        const controls=[...document.querySelectorAll('[data-slot="button"]')].filter(b=>b.checkVisibility()&&!b.closest('[data-slot="table-container"]')).map(b=>({label:b.textContent.trim(),r:b.getBoundingClientRect()}));
        const touching=[];
        for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){const a=controls[i],b=controls[j],dx=Math.max(a.r.left-b.r.right,b.r.left-a.r.right,0),dy=Math.max(a.r.top-b.r.bottom,b.r.top-a.r.bottom,0);if(dx<7.5&&dy<7.5)touching.push([a.label,b.label]);}
        const misplacedTabs=[];
        for(const list of document.querySelectorAll('[role="tablist"]')) {
          if(!list.checkVisibility()) continue;
          const bounds=list.getBoundingClientRect(), tabs=[...list.querySelectorAll('[role="tab"]')];
          for(const tab of tabs) {const r=tab.getBoundingClientRect();if(r.top<bounds.top-1||r.bottom>bounds.bottom+1||r.left<bounds.left-1||r.right>bounds.right+1)misplacedTabs.push(tab.textContent.trim());}
          const panel=list.parentElement.querySelector('[role="tabpanel"]:not([hidden])');
          if(panel&&panel.getBoundingClientRect().top<bounds.bottom+15) misplacedTabs.push('panel overlaps tab area');
        }
        return {overflow:document.documentElement.scrollWidth>innerWidth,touching,small:controls.filter(b=>b.r.height<39.5).map(b=>b.label),misplacedTabs};
      });
      assert.equal(result.overflow,false,name+' overflow '+width);assert.deepEqual(result.touching,[],name+' touching '+width);assert.deepEqual(result.small,[],name+' small '+width);assert.deepEqual(result.misplacedTabs,[],name+' tab bounds '+width);
      checks.push({name,width,...result});
    }
    if(process.env.COWORK_CATALOG_ONLY==='1') {
      const loading=await require('./web_loading.cjs').run({open,navigate,until});
      const catalog=await require('./web_catalog.cjs').run({open,navigate,layout,output,until});
      const availability=await require('./web_catalog_availability.cjs').run({open,navigate,layout,output,until});
      const sorting=await require('./web_catalog_sorting.cjs').run({open,navigate,layout,until});
      assert.deepEqual(errors,[]);assert.deepEqual(cspErrors,[]);
      const report={catalog,availability,sorting,loading,responsive_checks:checks,page_errors:errors,csp_errors:cspErrors,production_mutations:0};
      await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
      console.log(JSON.stringify({...report,responsive_checks:checks.length}));return;
    }
    const main=await open('admin'), {page,state}=main;
    await page.getByRole('button',{name:'계정 승인',exact:true}).waitFor();
    for(const width of [1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:1050});
      await navigate(page,'admin');
      for(const tab of ['새 계정 승인','사용자 권한','서버 설정','변경 이력']) {
        await page.getByRole('tab',{name:tab,exact:true}).click();
        await page.locator('[role="tabpanel"] [role="status"][aria-label="불러오는 중"]').waitFor({state:'hidden'});
        await layout(page,'admin-'+tab,width);
        if([1440,390].includes(width)&&['새 계정 승인','사용자 권한'].includes(tab)) await page.screenshot({path:path.join(output,`admin-${tab}-${width}.png`),fullPage:true});
      }
      for(const hash of ['servers','servers/227','jobs','environments','tokens','notifications','guide','how-it-works']) {
        await navigate(page,hash);
        await page.locator('main [role="status"][aria-label="불러오는 중"]').first().waitFor({state:'hidden'});
        if(hash==='guide') {await page.selectOption('#guide-node','227');await page.locator('#guide-result').waitFor();}
        if(hash==='tokens') {await page.locator('#create').click();await page.locator('#download-panel').waitFor();await layout(page,'tokens-download',width);await page.locator('#dismiss-download').click();}
        await layout(page,hash,width);
        if([1440,390].includes(width)&&['servers','environments','tokens','guide'].includes(hash)) await page.screenshot({path:path.join(output,`${hash}-${width}.png`),fullPage:true});
      }
      if(width===390) {await page.getByRole('button',{name:'메뉴 열기'}).click();await page.getByRole('dialog').waitFor();assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).overflow),'hidden');await page.keyboard.press('Tab');assert(await page.getByRole('dialog').evaluate(element=>element.contains(document.activeElement)));await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});assert.notEqual(await page.evaluate(()=>getComputedStyle(document.documentElement).overflow),'hidden');}
    }
    console.log('Responsive pages checked:',checks.length);
    await page.setViewportSize({width:1440,height:1050});
    await navigate(page,'admin');await page.getByRole('tab',{name:'새 계정 승인',exact:true}).click();
    await page.getByRole('checkbox',{name:'227',exact:true}).check();await page.getByRole('button',{name:'계정 승인',exact:true}).click();
    await page.getByText('계정 등록 요청을 처리했습니다.',{exact:true}).waitFor();
    assert.deepEqual(state.mutations.find(r=>r.endpoint.endsWith('/review')).body,{decision:'APPROVED',allowed_nodes:['227']});
    await page.getByRole('tab',{name:'사용자 권한',exact:true}).click();
    let user=page.locator('article').filter({has:page.getByRole('heading',{name:'alice',exact:true})});
    await user.getByRole('checkbox',{name:'229',exact:true}).check();await user.getByRole('button',{name:'권한 저장',exact:true}).click();
    await page.getByRole('alertdialog').getByRole('button',{name:'권한 저장',exact:true}).click();await page.getByRole('alertdialog').waitFor({state:'hidden'});
    assert.deepEqual(state.mutations.find(r=>r.endpoint.endsWith('/grants')).body,{allowed_nodes:['226','227','229'],expected_nodes:['226','227']});
    const hook='https://discord.com/api/webhooks/123/FAKE_BROWSER_ONLY';
    state.fail['/admin/users/alice/notifications']={status:409,code:'DESTINATION_GUILD_NOT_ALLOWED'};
    await page.locator('#notification-alice').fill(hook);await user.getByRole('button',{name:'채널 연결',exact:true}).click();
    await user.getByRole('alert').waitFor();assert.equal(await page.locator('#notification-alice').inputValue(),'');assert.match(await user.innerText(),/연결된 채널: 123/);
    await page.locator('#notification-alice').fill(hook);await user.getByRole('button',{name:'채널 연결',exact:true}).click();await user.getByText('연결된 채널: 789',{exact:true}).waitFor();
    assert.equal(await page.getByRole('tab',{name:'컨테이너 승인',exact:true}).count(),0);
    assert(!state.requests.some(r=>r.endpoint==='/admin/environments/pending'));
    await page.getByRole('tab',{name:'서버 설정',exact:true}).click();await page.locator('#budget-227-cpus').fill('24');await page.locator('form').filter({has:page.locator('#budget-227-cpus')}).getByRole('button',{name:'서버 설정 저장'}).click();await page.getByRole('alertdialog').getByRole('button',{name:'서버 설정 저장'}).click();await page.getByRole('alertdialog').waitFor({state:'hidden'});
    assert.equal(state.mutations.find(r=>r.endpoint==='/admin/servers/227').body.expected.cpus,32);
    assert.equal(state.nodes.find(n=>n.id==='227').cpus,24);
    await navigate(page,'tokens');await page.locator('#create').click();await page.locator('#download-panel').waitFor();
    await page.evaluate(()=>{navigator.clipboard.writeText=async text=>{window.copied=text;};});await page.locator('#copy-token').click();assert.equal(await page.evaluate(()=>window.copied),'fixture_only_new_secret');assert(!/fixture_only_new_secret/.test(await page.locator('body').innerHTML()));
    const download=page.waitForEvent('download');await page.locator('#download').click();const file=await download;assert.equal(file.suggestedFilename(),'user.token');assert.equal(await fs.readFile(await file.path(),'utf8'),'fixture_only_new_secret\n');
    await page.locator('#dismiss-download').click();await page.locator('#token-list button').first().click();await page.getByRole('alertdialog').getByRole('button',{name:'토큰 폐기',exact:true}).click();await page.getByRole('alertdialog').waitFor({state:'hidden'});assert(state.mutations.some(r=>r.method==='DELETE'));
    await navigate(page,'environments');await page.locator('#ssh-command').fill('ssh -p 11010 alice@203.255.11.227');await page.locator('#ssh-parse').click();await page.locator('#ssh-submit:enabled').waitFor();await page.locator('#ssh-command').fill('ssh -p 11010 alice@203.255.11.227 id');assert.equal(await page.locator('#ssh-submit').isDisabled(),true);
    await page.locator('#ssh-command').fill('ssh -p 11010 alice@203.255.11.227');await page.locator('#ssh-parse').click();await page.locator('#ssh-submit:enabled').waitFor();await page.selectOption('#ssh-node','227');state.fail['/containers']={status:503,code:'HUB_UNAVAILABLE'};await page.locator('#ssh-submit').click();await page.getByRole('alert').waitFor();await page.locator('#ssh-submit').click();await until(()=>state.mutations.filter(r=>r.endpoint==='/containers').length===2);
    const connections=state.mutations.filter(r=>r.endpoint==='/containers');assert.equal(connections[0].body.request_key,connections[1].body.request_key);assert.equal(connections[1].body.node_id,'227');assert.equal('workdir' in connections[1].body,false);
    await navigate(page,'guide');await page.selectOption('#guide-node','227');
    for(const client of ['codex','claude','cli']) {await page.selectOption('#guide-client',client);const command=await page.locator('#guide-install').innerText();assert(command.includes(`--client '${client}'`));for(const shell of ['bash','zsh']) assert.equal(spawnSync(shell,['-n'],{input:command,encoding:'utf8'}).status,0);}
    const tricky="/tmp/연구 폴더/o'hara$(printf INJECTED)`printf INJECTED`;data";await page.fill('#guide-root',tricky);const command=await page.locator('#guide-install').innerText();const shell=spawnSync('bash',[],{input:'curl() { :; }\npython3() { printf "%s\\0" "$@"; }\n'+command,encoding:'utf8'});assert.equal(shell.status,0);assert.deepEqual(shell.stdout.split('\0').slice(1),['--root',tricky,'--node','227','--client','cli','--hub-url','http://192.168.10.41:8080','']);
    for(const invalid of ['/','relative/path','/tmp/../other']) {await page.fill('#guide-root',invalid);assert.equal(await page.locator('#guide-result').count(),0);}
    await page.fill('#guide-root','/tmp/cowork-update');
    await page.selectOption('#guide-mode','update');
    const updateCommand=await page.locator('#guide-install').innerText();
    assert(updateCommand.includes('--update'));assert(!/--node|--client|--hub-url/.test(updateCommand));
    assert.equal(await page.locator('#guide-node, #guide-client, #guide-create-token').count(),0);
    for(const width of [1440,390,320]) {await page.setViewportSize({width,height:1050});await layout(page,'guide-update',width);}
    await page.selectOption('#guide-mode','install');await page.selectOption('#guide-client','codex');await page.selectOption('#guide-node','227');
    const issuedBefore=state.mutations.filter(r=>r.method==='POST'&&r.endpoint==='/tokens').length;
    await page.locator('#guide-create-token').click();await page.locator('#download-panel').waitFor();
    assert.equal(state.mutations.filter(r=>r.method==='POST'&&r.endpoint==='/tokens').length,issuedBefore+1);
    await page.locator('#copy-token').click();assert.equal(await page.evaluate(()=>window.copied),'fixture_only_new_secret');
    assert(!(await page.content()).includes('fixture_only_new_secret'));
    for(const width of [1440,390,320]) {await page.setViewportSize({width,height:1050});await layout(page,'guide-token',width);}
    await navigate(page,'tokens');await page.locator('#download-panel').waitFor();assert(await page.locator('#create').isDisabled());
    await navigate(page,'guide');await page.selectOption('#guide-node','227');await page.locator('#download-panel').waitFor();
    assert.equal(await page.locator('#guide-create-token').count(),0);await page.locator('#dismiss-download').click();
    await main.context.close();
    console.log('Approval, grants, channel, container, token and guide flows checked.');

    // Cached same-account views survive transient outages, but never permission loss or account changes.
    const cached=await open('guide'), p=cached.page;
    await p.locator('#guide-root').waitFor();cached.state.hold.add('GET /profile');await p.reload();await until(() => p.evaluate(()=>window.testAuth?.ready));await p.evaluate(()=>window.testAuth.emit('alice'));await p.locator('#guide-root').waitFor();await until(()=>cached.state.held.length===1);
    await p.fill('#guide-root','/tmp/in-progress-install');await cached.json(cached.state.held.shift().route,{error:{code:'HUB_UNAVAILABLE'}},503);await p.getByRole('alert').waitFor();assert.equal(await p.locator('#guide-root').inputValue(),'/tmp/in-progress-install');assert.equal(await p.locator('#portal').isVisible(),true);
    await p.evaluate(()=>{location.hash='servers';});await until(()=>cached.state.held.length===1);cached.state.unlinked.add('alice');await cached.json(cached.state.held.shift().route,{error:{code:'WEB_ACCOUNT_NOT_LINKED'}},403);await p.locator('#onboarding-form').waitFor();assert.equal(await p.locator('#portal').count(),0);assert.equal(await p.evaluate(()=>localStorage.getItem('cowork.profile.v1')),null);
    cached.state.hold.clear();await p.evaluate(()=>window.testAuth.emit('bob'));await p.locator('#portal').waitFor();assert.equal(await p.getByRole('link',{name:'관리자',exact:true}).count(),0);assert(!/alice/.test(await p.locator('body').innerText()));await cached.context.close();

    const signup=await open('guide','carol');await signup.page.locator('#onboarding-name').fill('carol');await signup.page.locator('#onboarding-uid').fill('1201');await signup.page.locator('#onboarding-gid').fill('1200');await signup.page.locator('#onboarding-webhook').fill(hook);await signup.page.locator('#onboarding-submit').click();await signup.page.locator('#onboarding-refresh').waitFor();assert(!/FAKE_BROWSER_ONLY/.test(await signup.page.locator('body').innerHTML()));assert.equal(signup.state.mutations[0].body.account_name,'carol');await signup.context.close();

    // Late list/issuance responses cannot resurrect an older token list or a logged-out account.
    const race=await open('guide');race.state.hold.add('GET /tokens');await navigate(race.page,'tokens');await until(()=>race.state.held.length===1);await race.page.locator('#create').click();await race.page.locator('#download-panel').waitFor();await until(()=>race.state.held.length===2);const [older,newer]=race.state.held.splice(0);await race.json(newer.route,race.state.tokens);await race.page.locator('#token-list').getByText('alice-MCP',{exact:true}).waitFor();await race.json(older.route,[]);assert.match(await race.page.locator('#token-list').innerText(),/alice-MCP/);
    race.state.hold.add('POST /tokens');await race.page.locator('#dismiss-download').click();await race.page.locator('#create').click();await until(()=>race.state.held.length===1);await race.page.locator('#logout').click();await race.page.locator('#login').waitFor();await race.json(race.state.held.shift().route,{id:'late',name:'late',token:'never_visible'});assert.equal(await race.page.locator('#portal').count(),0);assert.equal(await race.page.evaluate(()=>localStorage.getItem('cowork.profile.v1')),null);assert(!/never_visible/.test(await race.page.locator('body').innerHTML()));await race.context.close();
    // Malformed, expired, other-account and other-hub caches must not expose a portal.
    const invalid = await open('guide',false);
    const validCache = {uid:'firebase-alice',scope:JSON.stringify(['fixture-project',origin+'/']),savedAt:Date.now(),profile:fixtures.profile};
    for(const value of ['broken',JSON.stringify({...validCache,savedAt:0}),JSON.stringify({...validCache,uid:'firebase-bob'}),JSON.stringify({...validCache,scope:'another-hub'})]) {
      invalid.state.hold.add('GET /profile');await invalid.page.evaluate(value=>localStorage.setItem('cowork.profile.v1',value),value);
      await invalid.page.evaluate(()=>{void window.testAuth.emit('alice');});await until(()=>invalid.state.held.length===1);
      assert.equal(await invalid.page.locator('#portal').count(),0);
      await invalid.json(invalid.state.held.shift().route,fixtures.profile);await invalid.page.locator('#portal').waitFor();
    }
    invalid.state.hold.clear();invalid.state.fail['/profile']={status:401,code:'UNAUTHENTICATED'};
    await invalid.page.evaluate(()=>{void window.testAuth.emit('alice');});await invalid.page.locator('#login').waitFor();assert.equal(await invalid.page.locator('#portal').count(),0);assert.equal(await invalid.page.evaluate(()=>localStorage.getItem('cowork.profile.v1')),null);await invalid.context.close();
    const storage = await open('guide',false);
    await storage.page.evaluate(()=>{Storage.prototype.getItem=()=>{throw Error('unavailable')};Storage.prototype.setItem=()=>{throw Error('unavailable')};void window.testAuth.emit('bob');});await storage.page.locator('#guide-root').waitFor();assert.equal(await storage.page.locator('#guide-root').inputValue(),'/10Gdata/bob/cowork');await storage.context.close();
    const languages=await require('./web_languages.cjs')({open,navigate,layout,output,until});
    const howItWorks=await require('./web_how_it_works.cjs')({open,navigate,layout,output,until});
    const catalog=await require('./web_catalog.cjs').run({open,navigate,layout,output,until});
    const availability=await require('./web_catalog_availability.cjs').run({open,navigate,layout,output,until});
    const sorting=await require('./web_catalog_sorting.cjs').run({open,navigate,layout,until});
    assert.deepEqual(errors,[]);assert.deepEqual(cspErrors,[]);
    const report={languages,howItWorks,catalog,availability,sorting,responsive_checks:checks,auth_cache:true,account_isolation:true,late_response_protection:true,onboarding:true,admin_mutations:true,token_copy_download_revoke:true,ssh_idempotency:true,guide_shell_quoting:true,page_errors:errors,csp_errors:cspErrors,production_mutations:0};
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({...report,responsive_checks:checks.length}));
  } finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
