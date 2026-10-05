'use strict';
const assert=require('node:assert/strict'),path=require('node:path');
const fixtures=require('./web_react_fixtures.cjs');

module.exports=async function checkLanguages({open,navigate,layout,output,until}) {
  const cases=[];
  async function englishOnly(page,label) {
    const untranslated=await page.evaluate(allowed=>{
      const findings=[],walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()) {
        const node=walker.currentNode,parent=node.parentElement;
        if(!parent||parent.closest('#language,script,style')||!parent.checkVisibility())continue;
        let value=node.textContent;
        for(const item of allowed)value=value.replaceAll(item,'');
        if(/[가-힣]/.test(value))findings.push(value.trim());
      }
      for(const node of document.querySelectorAll('[placeholder],[aria-label],[title]')) {
        if(!node.checkVisibility()||node.closest('#language'))continue;
        for(const attr of ['placeholder','aria-label','title'])if(/[가-힣]/.test(node.getAttribute(attr)||''))findings.push(node.getAttribute(attr));
      }
      return findings;
    },[fixtures.environment.name,fixtures.job.name,'메인 연구 컨테이너']);
    assert.deepEqual(untranslated,[],label+' untranslated UI');cases.push(label);
  }
  const main=await open('servers','alice',{language:'en'}),{page,state}=main;
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.title(),'Server management · Cowork');
  for(const width of [1440,390,320]) {
    await page.setViewportSize({width,height:1050});
    for(const hash of ['servers','servers/227','jobs','environments','tokens','notifications','guide','how-it-works','admin']) {
      await navigate(page,hash);
      await page.locator('main [role="status"][aria-label="Loading"]').first().waitFor({state:'hidden'});
      if(hash==='guide') {
        await page.selectOption('#guide-node','227');
        await page.locator('#guide-result').waitFor();
        await page.locator('details').evaluateAll(nodes=>nodes.forEach(node=>node.open=true));
        assert.match(await page.locator('#guide-first').innerText(),/Use cowork to run/);
        assert.equal(await page.locator('#guide-first').getByText(/CPU 1개/).count(),0);
      }
      if(hash==='admin') {
        for(const tab of ['New account approvals','User permissions','Server settings','Change history']) {
          await page.getByRole('tab',{name:tab,exact:true}).click();
          await page.locator('[role="tabpanel"] [role="status"][aria-label="Loading"]').waitFor({state:'hidden'});
          await englishOnly(page,'admin '+tab+' '+width);await layout(page,'en-admin-'+tab,width);
        }
      }
      await englishOnly(page,hash+' '+width);await layout(page,'en-'+hash,width);
      const box=await page.locator('#language').boundingBox();assert(box.x>width/2&&box.x+box.width<=width-8);
      if(width===390&&['servers','guide'].includes(hash))await page.screenshot({path:path.join(output,'english-'+hash+'.png'),fullPage:true});
    }
    if(width===390) {
      await page.getByRole('button',{name:'Open menu'}).click();
      await page.getByRole('dialog').waitFor();await englishOnly(page,'mobile menu');
      await page.getByRole('dialog').getByRole('link',{name:'My jobs',exact:true}).click();
      await page.getByRole('dialog').waitFor({state:'hidden'});
    }
  }
  // Switching language preserves forms, issued tokens and exact command bytes without reauthentication.
  await page.setViewportSize({width:1440,height:1050});
  await navigate(page,'guide');await page.selectOption('#guide-node','227');
  await page.fill('#guide-root',"/tmp/연구 폴더/o'hara");
  const command=await page.locator('#guide-install').innerText();
  await page.locator('#guide-create-token').click();await page.locator('#download-panel').waitFor();
  await page.evaluate(()=>{navigator.clipboard.writeText=async text=>{window.copied=text;};});
  const before=state.requests.filter(r=>r.endpoint==='/profile').length;
  await page.selectOption('#language','ko');
  assert.equal(await page.locator('#guide-install').innerText(),command);
  await page.getByText('개인 토큰이 준비됐습니다',{exact:true}).waitFor();
  await page.selectOption('#language','en');await page.getByText('Your personal token is ready',{exact:true}).waitFor();
  assert.equal(await page.locator('#guide-root').inputValue(),"/tmp/연구 폴더/o'hara");
  await page.locator('#copy-token').click();assert.equal(await page.evaluate(()=>window.copied),'fixture_only_new_secret');
  assert(!(await page.content()).includes('fixture_only_new_secret'));
  assert.equal(state.requests.filter(r=>r.endpoint==='/profile').length,before);
  assert.equal(state.mutations.filter(r=>r.endpoint==='/tokens').length,1);
  await page.fill('#guide-root','relative/path');await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(),/absolute path/);
  await page.selectOption('#language','ko');assert.match(await page.getByRole('alert').innerText(),/절대 경로/);
  await page.selectOption('#language','en');assert.match(await page.getByRole('alert').innerText(),/absolute path/);
  await navigate(page,'environments');await page.fill('#ssh-command','ssh -p 11010 alice@203.255.11.227');
  await page.locator('#ssh-parse').click();await page.locator('#ssh-submit:enabled').waitFor();
  await page.selectOption('#language','ko');await page.selectOption('#language','en');
  assert.equal(await page.locator('#ssh-command').inputValue(),'ssh -p 11010 alice@203.255.11.227');
  assert.equal(await page.locator('#ssh-submit').isEnabled(),true);
  state.fail['/containers']={status:403,code:'FORBIDDEN_NODE'};await page.locator('#ssh-submit').click();await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(),/permission to use this server/);
  await page.selectOption('#language','ko');assert.match(await page.getByRole('alert').innerText(),/사용 권한/);
  await page.selectOption('#language','en');await page.getByRole('alert').waitFor();await englishOnly(page,'SSH error');
  await navigate(page,'tokens');await page.locator('#dismiss-download').click();
  await page.locator('#token-list button').first().click();await englishOnly(page,'revoke confirmation');
  await page.getByRole('button',{name:'Go back',exact:true}).click();
  // A Korean job name is user data and must not be translated.
  await navigate(page,'jobs');await page.getByText(fixtures.job.name,{exact:true}).waitFor();
  assert.equal(await page.locator('tbody').getByText('Running',{exact:true}).count(),1);
  assert(!/[가-힣]/.test(await page.locator('tbody td').last().innerText()));
  await page.reload();await until(()=>page.evaluate(()=>window.testAuth?.ready));
  await page.evaluate(()=>window.testAuth.emit(null));
  await page.getByRole('button',{name:'Sign in with Google',exact:true}).waitFor();
  assert.equal(await page.locator('#language').inputValue(),'en');assert(new URL(page.url()).searchParams.get('lang')==='en');
  await page.evaluate(()=>window.testAuth.emit('alice'));await page.locator('#portal').waitFor();
  await page.locator('#logout').click();await page.getByRole('button',{name:'Sign in with Google',exact:true}).waitFor();
  assert.equal(await page.locator('#language').inputValue(),'en');
  await main.context.close();
  const signup=await open('guide','carol',{language:'en'});
  await signup.page.fill('#onboarding-name','carol');await signup.page.fill('#onboarding-uid','1201');await signup.page.fill('#onboarding-gid','1200');
  await signup.page.fill('#onboarding-webhook','https://discord.com/api/webhooks/123/FAKE_BROWSER_ONLY');
  await signup.page.selectOption('#language','ko');await signup.page.selectOption('#language','en');
  assert.equal(await signup.page.locator('#onboarding-name').inputValue(),'carol');
  await englishOnly(signup.page,'onboarding form');
  await signup.page.locator('#onboarding-submit').click();await signup.page.locator('#onboarding-refresh').waitFor();await englishOnly(signup.page,'account approval pending');
  assert.equal(signup.state.mutations[0].body.account_name,'carol');await signup.context.close();
  for(const options of [{savedLanguage:'en'},{savedLanguage:'en',language:'ko'},{language:'invalid'},{blockStorage:true,language:'en'}]) {
    const view=await open('servers',false,options),p=view.page;
    const expected=options.language==='ko'||options.language==='invalid'?'ko':'en';
    assert.equal(await p.locator('#language').inputValue(),expected);
    await p.selectOption('#language',expected==='en'?'ko':'en');
    assert.equal(await p.locator('html').getAttribute('lang'),expected==='en'?'ko':'en');
    await view.context.close();
  }
  return {english_views:cases.length,language_persistence:true,forms_and_tokens_preserved:true,commands_preserved:true,no_extra_auth_requests:true};
};
