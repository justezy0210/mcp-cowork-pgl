'use strict';
const assert=require('node:assert/strict'),path=require('node:path');

module.exports.run=async({open,navigate,layout,output,until})=>{
  const {page,state,context}=await open('data/availability');
  await page.getByRole('heading',{name:'데이터 보유 현황',exact:true}).waitFor();
  assert.equal(await page.locator('[data-catalog-availability] table').count(),0);
  await page.getByText('프로젝트를 선택하면 데이터 보유 현황이 표시됩니다.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('searchbox',{name:'현황에서 샘플 검색'}).count(),0);
  assert.equal(await page.inputValue('#availability-project'),'');
  assert.equal(state.requests.filter(r=>r.endpoint==='/catalog/files').length,0);
  assert.equal(await page.locator('aside a[href="#data/availability"][aria-current="page"]').count(),1);
  assert.equal(await page.locator('[role="tablist"]').count(),0);
  await page.selectOption('#availability-project','study');
  await page.locator('[data-availability-sample="sample"]').waitFor();
  assert.equal(await page.locator('[data-catalog-availability] table').count(),1);
  const alpha=page.locator('[data-availability-sample="sample"]');
  await alpha.locator('[data-availability-tissue="stem"]').getByText('×2*',{exact:true}).waitFor();
  await alpha.locator('[data-availability-tissue="flower"]').getByText('×2',{exact:true}).waitFor();
  await alpha.locator('[data-availability-condition="Down"]').getByText('×3',{exact:true}).waitFor();
  await page.getByRole('columnheader',{name:'떠 있는 개체',exact:true}).waitFor();
  assert.match(await alpha.locator('[data-availability-type="ONT"] a').getAttribute('aria-label'),/확인 필요/);
  assert.equal(await page.locator('[data-availability-sample="beta"] [data-availability-tissue="stem"] a').count(),0);
  await page.locator('[data-availability-sample="beta"] [data-availability-tissue="stem"]').getByText('미등록',{exact:true}).waitFor({state:'attached'});
  await page.getByRole('searchbox',{name:'현황에서 샘플 검색'}).fill('second species');
  assert.equal(await page.locator('[data-availability-sample]').count(),1);
  await page.getByRole('searchbox',{name:'현황에서 샘플 검색'}).fill('not-found');
  await page.getByText('일치하는 항목이 없습니다.',{exact:true}).waitFor();
  await page.getByRole('searchbox',{name:'현황에서 샘플 검색'}).fill('');
  assert.equal(await page.locator('[data-availability-sample]').count(),3);
  assert.equal(await page.locator('[data-catalog-availability] table').count(),1);
  await page.locator('aside').getByRole('link',{name:'Test species',exact:true}).click();
  await until(()=>page.evaluate(()=>location.hash==='#data/availability/study/species'));
  assert.equal(await page.locator('[data-availability-sample]').count(),2);
  assert.equal(await page.inputValue('#availability-project'),'study');
  await page.selectOption('#availability-project','other-study');
  await until(()=>page.evaluate(()=>location.hash==='#data/availability/other-study'));
  await page.locator('[data-availability-sample="delta"]').waitFor();
  assert.equal(await page.locator('[data-availability-sample]').count(),1);
  await page.selectOption('#availability-project','');
  await until(()=>page.evaluate(()=>location.hash==='#data/availability'));
  assert.equal(await page.locator('[data-catalog-availability] table').count(),0);
  await page.getByText('프로젝트를 선택하면 데이터 보유 현황이 표시됩니다.',{exact:true}).waitFor();
  await page.locator('aside').getByRole('link',{name:'Test project',exact:true}).click();
  await page.locator('[data-availability-sample="sample"]').waitFor();
  assert.equal(state.requests.filter(r=>r.endpoint==='/catalog/files').length,0);
  await alpha.locator('[data-availability-tissue="stem"] a').click();
  await page.getByRole('button',{name:'rna.fastq.gz',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>location.hash),'#data/study/species/sample/rna');
  assert.equal(await page.locator('[data-catalog-availability]').count(),0);
  await page.goBack();
  await page.locator('[data-catalog-availability]').waitFor();
  for(const language of ['ko','en']) {
    await page.selectOption('#language',language);
    for(const width of [1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:1050});
      if([1440,390].includes(width)) {
        await navigate(page,'data/availability');
        await page.getByText(language==='ko'?'프로젝트를 선택하면 데이터 보유 현황이 표시됩니다.':'Data availability will appear after you select a project.',{exact:true}).waitFor();
        assert.equal(await page.locator('[data-catalog-availability] table').count(),0);
        await layout(page,'catalog-availability-select-'+language,width);
        await page.screenshot({path:path.join(output,`catalog-availability-select-${language}-${width}.png`),fullPage:true});
      }
      await navigate(page,'data/availability/study');
      await page.locator('[data-availability-sample="sample"]').waitFor();
      await layout(page,'catalog-availability-'+language,width);
      if(language==='en')assert.equal(/[가-힣]/.test(await page.locator('main').innerText()),false);
      if([1440,390].includes(width))await page.screenshot({path:path.join(output,`catalog-availability-${language}-${width}.png`),fullPage:true});
      if(width===390) {
        await page.getByRole('button',{name:language==='ko'?'메뉴 열기':'Open menu',exact:true}).click();
        await page.getByRole('dialog').getByRole('link',{name:language==='ko'?'데이터 보유 현황':'Data availability',exact:true}).click();
        await page.getByRole('dialog').waitFor({state:'hidden'});
        assert.equal(await page.evaluate(()=>location.hash),'#data/availability');
        assert.equal(await page.locator('[data-catalog-availability] table').count(),0);
      }
    }
  }
  await context.close();
  const denied=await open('data/availability','bob');
  await denied.page.getByText('데이터 관리 열람 권한이 필요합니다.',{exact:true}).waitFor();
  assert.equal(denied.state.requests.filter(r=>r.endpoint.startsWith('/catalog')).length,0);
  await denied.context.close();
  return {separate_library_navigation:true,project_selector:true,project_selection_required:true,overview_without_file_requests:true,project_species_filters:true,replicate_labels:true,conditions:true,unregistered_not_absent:true,cell_to_files:true,back_navigation:true,restricted_access:true,responsive_views:14};
};
