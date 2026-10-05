'use strict';
const assert=require('node:assert/strict'),path=require('node:path');

module.exports=async function checkHowItWorks({open,navigate,layout,output,until}) {
  // The introduction is readable even before Firebase resolves a session.
  const guest=await open('how-it-works',false,{language:'en'}),{page,state}=guest;
  await page.getByRole('heading',{name:'How Cowork Works',exact:true}).waitFor();
  assert.equal(await page.title(),'How Cowork Works');
  await until(()=>page.locator('#workflow-image').evaluate(img=>img.complete&&img.naturalWidth===1672));
  assert.equal(state.requests.length,0,'Public introduction must not request protected account data');
  assert.equal(await page.locator('#portal').count(),0);
  assert.match(await page.locator('#workflow-image').getAttribute('alt'),/A person requests a task from an AI agent/);
  const imageURL=await page.locator('#workflow-image').evaluate(img=>img.src);
  const popup=page.waitForEvent('popup');
  await page.getByRole('link',{name:'View full-size diagram',exact:true}).click();
  const original=await popup;
  await original.waitForLoadState('domcontentloaded');
  assert.equal(original.url(),imageURL);
  assert.equal(await original.evaluate(()=>window.opener),null);
  await original.close();
  for(const width of [1440,768,390,320]) {
    await page.setViewportSize({width,height:1050});
    await layout(page,'public-how-it-works',width);
    if([1440,390].includes(width))await page.screenshot({path:path.join(output,`how-it-works-public-${width}.png`),fullPage:true});
  }
  await page.evaluate(()=>window.testAuth.emit(null));
  await page.getByRole('link',{name:'Sign in or register',exact:true}).click();
  await page.locator('#login').waitFor();
  await page.getByRole('link',{name:'How Cowork Works',exact:true}).click();
  await page.locator('#workflow-image').waitFor();
  await page.selectOption('#language','ko');
  await page.getByRole('heading',{name:'Cowork 작동 원리',exact:true}).waitFor();
  assert.match(await page.locator('#workflow-image').getAttribute('alt'),/사람이 AI에게/);
  await page.reload();
  await page.getByRole('heading',{name:'Cowork 작동 원리',exact:true}).waitFor();
  assert.equal(await page.locator('#language').inputValue(),'ko');
  await guest.context.close();

  // Approved users reach the same page from desktop and mobile navigation.
  const member=await open('servers','bob',{language:'en'});
  await member.page.getByRole('navigation').getByRole('link',{name:'How Cowork Works',exact:true}).click();
  await member.page.locator('#workflow-image').waitFor();
  assert.equal(await member.page.locator('#portal').count(),1);
  await member.page.screenshot({path:path.join(output,'how-it-works-member-1440.png'),fullPage:true});
  await navigate(member.page,'servers');
  await member.page.setViewportSize({width:320,height:1050});
  await member.page.getByRole('button',{name:'Open menu'}).click();
  await member.page.getByRole('dialog').getByRole('link',{name:'How Cowork Works',exact:true}).click();
  await member.page.getByRole('dialog').waitFor({state:'hidden'});
  await member.page.locator('#workflow-image').waitFor();
  await member.page.locator('#logout').click();
  await until(()=>member.page.locator('#portal').count().then(count=>count===0));
  await member.page.getByRole('heading',{name:'How Cowork Works',exact:true}).waitFor();
  assert.equal(member.state.mutations.length,0);
  await member.context.close();

  const onboarding=await open('servers','carol',{language:'en'});
  await navigate(onboarding.page,'how-it-works');
  await onboarding.page.locator('#workflow-image').waitFor();
  await onboarding.page.getByRole('link',{name:'Sign in or register',exact:true}).click();
  await onboarding.page.locator('#onboarding-form').waitFor();
  assert.equal(onboarding.state.mutations.length,0);
  await onboarding.context.close();
  return {public_before_auth:true,full_size_image:true,bilingual:true,desktop_and_mobile_navigation:true,logout_preserves_public_page:true,onboarding_preserved:true};
};
