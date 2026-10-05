'use strict';
const assert=require('node:assert/strict');
module.exports.run=async({open,navigate,until})=>{
  const view=await open('data'),{page,state,json}=view;
  await page.getByText('33개 파일',{exact:true}).waitFor();
  await navigate(page,'data/study/species');
  await page.getByText('31개 파일',{exact:true}).waitFor();
  const fileRequests=()=>state.requests.filter(r=>r.endpoint==='/catalog/files').length;
  const before=fileRequests();
  state.hold.add('GET /catalog/files');
  await navigate(page,'data');
  await page.getByText('33개 파일',{exact:true}).waitFor();
  assert.equal(fileRequests(),before,'Returning to a recent selection must not wait for another request');
  // A new login generation must not inherit private catalog responses.
  await page.evaluate(()=>window.testAuth.emit(null));await page.locator('#login').waitFor();
  await page.evaluate(()=>window.testAuth.emit('alice'));
  await until(()=>state.held.some(r=>r.endpoint==='/catalog/files'));
  assert.equal(await page.locator('tr[data-catalog-file]').count(),0);
  const held=state.held.splice(0).find(r=>r.endpoint==='/catalog/files');
  const params=new URL(held.route.request().url()).searchParams;
  await json(held.route,require('./web_catalog.cjs').files(params));
  await page.getByText('33개 파일',{exact:true}).waitFor();
  state.hold.clear();
  // A burst of navigation shares the ongoing profile check.
  state.hold.add('GET /profile');
  await navigate(page,'data/study');
  await until(()=>state.held.some(r=>r.endpoint==='/profile'));
  await navigate(page,'data/study/species');
  await navigate(page,'data');
  assert.equal(state.held.filter(r=>r.endpoint==='/profile').length,1);
  await json(state.held.shift().route,{error:{code:'WEB_ACCOUNT_NOT_LINKED'}},403);
  await page.locator('#onboarding-form').waitFor();
  assert.equal(await page.locator('tr[data-catalog-file]').count(),0);
  assert.equal(await page.evaluate(()=>localStorage.getItem('cowork.profile.v1')),null);
  await view.context.close();
  return {recent_catalog_navigation_reuses_data:true,private_cache_clears_on_login_change:true,parallel_profile_checks_deduplicated:true,permission_loss_clears_private_ui:true};
};
