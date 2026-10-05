'use strict';
const assert=require('node:assert/strict');
module.exports.run=async({open,navigate,layout,until})=>{
  const {page,state,context}=await open('data');
  await page.locator('[data-catalog-file="file0"]').waitFor();
  const first=()=>page.locator('tr[data-catalog-file]').first().getAttribute('data-catalog-file');
  const click=async(key,order)=>{
    const previous=state.requests.filter(r=>r.endpoint==='/catalog/files').length;
    await page.locator(`button[data-sort="${key}"]`).click();
    await until(async()=>await page.locator(`button[data-sort="${key}"]`).count()===1 && await page.locator(`button[data-sort="${key}"]`).evaluate(e=>e.closest('th').getAttribute('aria-sort'))===(order==='asc'?'ascending':'descending'));
    const request=state.requests.filter(r=>r.endpoint==='/catalog/files').at(-1),params=new URL(request.route.request().url()).searchParams;
    // A previously viewed sort can be served from the private per-user cache.
    if(state.requests.filter(r=>r.endpoint==='/catalog/files').length>previous){assert.equal(params.get('sort_by'),key);assert.equal(params.get('sort_order'),order);assert.equal(params.get('page'),'1');}
  };
  await page.getByRole('button',{name:'다음',exact:true}).click();
  await until(async()=>await page.locator('tr[data-catalog-file]').count()===8);
  await click('file','asc');
  assert.equal(await page.locator('tr[data-catalog-file]').count(),25);
  assert.equal(await first(),'beta-file');
  await click('file','desc');assert.equal(await first(),'stem_control_rep2_R1.fastq.gz');
  for(const key of ['sample','species','project','data_type']){await click(key,'asc');await click(key,'desc');}
  for(const key of ['reads','bases','q30']){await click(key,'desc');assert.equal(await first(),'delta-file');await click(key,'asc');assert.equal(await first(),'delta-file');}
  // Keyboard activation remains on the sorting control after the request.
  await page.locator('[data-sort="reads"]').focus();await page.keyboard.press('Enter');
  await page.locator('th[aria-sort="descending"] [data-sort="reads"]').waitFor();
  assert.equal(await page.evaluate(()=>document.activeElement?.getAttribute('data-sort')),'reads');
  await page.fill('[aria-label="파일 검색"]','Sample delta');
  await page.getByText('1개 파일',{exact:true}).waitFor();
  assert.equal(await first(),'delta-file');
  assert.equal(await page.locator('th[aria-sort="descending"] [data-sort="reads"]').count(),1);
  await page.fill('[aria-label="파일 검색"]','');await page.getByText('33개 파일',{exact:true}).waitFor();
  for(const language of ['ko','en']){
    await page.selectOption('#language',language);
    await page.setViewportSize({width:390,height:1000});
    await layout(page,'catalog-sort-'+language,390);
    assert.equal(await page.locator('thead button[data-sort]').count(),8);
  }
  await navigate(page,'data/study/species/sample/reads');
  await page.locator('[data-catalog-file="file0"]').waitFor();
  assert.equal(await page.locator('th[aria-sort]').count(),0);
  await context.close();
  return {all_columns:true,server_sort_parameters:true,page_reset:true,filter_preserves_sort:true,keyboard_focus:true,scope_reset:true};
};
