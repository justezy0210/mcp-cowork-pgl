'use strict';
const assert = require('node:assert/strict'), path = require('node:path');
const dataset = {id:'reads',name:'HiFi',file_count:28,representative_count:1,status:'confirmed',pending_scope:false};
const data = {
  version:1,updated_at:'2026-10-03T10:00:00Z',unassigned_files:2,
  projects:[{id:'study',name:'Test project',species:[{id:'species',name:'Test species'}]}],
  samples:[{id:'sample',name:'Sample alpha',project_id:'study',species_id:'species',species:'Test species',identity_status:'confirmed',datasets:[dataset]}],
  files:Array.from({length:28},(_,i)=>({id:'file'+i,sample_id:'sample',dataset_id:'reads',name:i===0?'combined.fastq.gz':`source_${String(i).padStart(2,'0')}.fastq.gz`,path:i===0?'/private/test/combined.fastq.gz':`/private/test/source_${String(i).padStart(2,'0')}.fastq.gz`,bytes:1200000000+i,roles:i===0?['representative','merged']:['source'],representative_candidate:false,match_status:i===0?'confirmed':'inferred',read_part:'',tissue:'',replicate:'',quality_group:'',older_version:false,reports:[],inputs:i===0?[{path:'/private/test/source_01.fastq.gz',status:'inferred'}]:[]})),
};
data.projects[0].species.push({id:'species-two',name:'Second species'});
data.projects.push({id:'other-study',name:'Other project',species:[{id:'other-species',name:'Other species'}]});
data.samples[0].datasets.push({...dataset,id:'rna',name:'RNA-seq',file_count:1},{...dataset,id:'pending',name:'ONT',file_count:0,status:'provisional'});
for(const [id,project,species] of [['beta','study','species'],['gamma','study','species-two'],['delta','other-study','other-species']]) {
  data.samples.push({id,name:'Sample '+id,project_id:project,species_id:species,species:data.projects.find(p=>p.id===project).species.find(s=>s.id===species).name,identity_status:'confirmed',datasets:[{...dataset,id:id+'-reads',file_count:1}]});
  data.files.push({...data.files[1],id:id+'-file',name:id+'.fastq.gz',path:'/private/test/'+id+'.fastq.gz',sample_id:id,dataset_id:id+'-reads',roles:['representative']});
}
data.files.push({...data.files[1],id:'rna-file',name:'rna.fastq.gz',path:'/private/test/rna.fastq.gz',dataset_id:'rna'});
data.samples[0].datasets.find(d=>d.id==='rna').file_count=4;
Object.assign(data.files.find(f=>f.id==='rna-file'),{tissue:'stem',replicate:'1'});
for(const [tissue,replicate,condition] of [['stem','2','control'],['flower','1','treated'],['flower','2','treated']]) {
  const name=`${tissue}_${condition}_rep${replicate}_R1.fastq.gz`;
  data.files.push({...data.files.find(f=>f.id==='rna-file'),id:name,name,path:'/private/test/'+name,tissue,replicate});
}
data.files[1].roles=['merged'];
data.files[1].inputs=[{path:data.files[2].path,status:'confirmed'}];
data.files[0].stats={status:'pending',reads:null,bases:null,computed_at:null};
data.files[1].stats={status:'completed',reads:1234567,bases:9876543210,q30_percent:96.25,computed_at:'2026-10-03T13:00:00Z'};
data.files[2].stats={status:'completed',reads:0,bases:0,q30_percent:0,computed_at:'2026-10-03T13:00:00Z'};
data.files.find(f=>f.id==='delta-file').stats={status:'completed',reads:5,bases:100,q30_percent:0,computed_at:'2026-10-03T13:00:00Z'};
data.files.find(f=>f.id==='beta-file').stats={status:'failed',reads:null,bases:null,computed_at:null};
for(let i=0;i<27;i++) data.files.push({...data.files[2],id:'retained-'+i,name:`retained_${String(i).padStart(2,'0')}.fastq.gz`,path:`/private/test/fail/retained_${String(i).padStart(2,'0')}.fastq.gz`,roles:['source'],quality_group:'fail',display_group:'retained_original',inputs:[]});
dataset.file_count+=27;
module.exports.data=data;
data.samples[0].datasets.find(d=>d.id==='rna').availability={status:'present',groups:[
  {tissue:'stem',condition:'',condition_description:'',replicate_count:2,replicate_status:'inferred'},
  {tissue:'flower',condition:'',condition_description:'',replicate_count:2,replicate_status:'confirmed'},
  {tissue:'',condition:'Up',condition_description:'floating individuals',replicate_count:3,replicate_status:'confirmed'},
  {tissue:'',condition:'Down',condition_description:'sinking individuals',replicate_count:3,replicate_status:'confirmed'},
]};
module.exports.files=params=>{
  const needle=(params.get('q')||'').toLowerCase().trim(),grouped=params.get('grouped')==='true';
  const samples=data.samples.filter(s=>(!params.get('project')||s.project_id===params.get('project'))&&(!params.get('species')||s.species_id===params.get('species'))&&(!params.get('sample')||s.id===params.get('sample')));
  const scoped=data.files.filter(r=>samples.some(s=>s.id===r.sample_id)&&(!params.get('dataset')||r.dataset_id===params.get('dataset')));
  const child=(r,input)=>scoped.find(f=>f.path===input.path&&f.sample_id===r.sample_id&&f.dataset_id===r.dataset_id);
  const nested=new Set(scoped.flatMap(r=>r.inputs.map(input=>child(r,input)?.id).filter(Boolean)));
  const expand=(r,seen=new Set())=>({...r,inputs:r.inputs.filter(i=>!seen.has(child(r,i)?.id)).map(input=>({...input,file:child(r,input)?expand(child(r,input),new Set([...seen,r.id])):null}))});
  const matches=(r,seen=new Set())=>{
    if(seen.has(r.id))return false;seen.add(r.id);
    const sample=samples.find(s=>s.id===r.sample_id),project=data.projects.find(p=>p.id===sample.project_id),dataset=sample.datasets.find(d=>d.id===r.dataset_id);
    return `${r.path} ${sample.name} ${sample.species} ${project.name} ${dataset.name}`.toLowerCase().includes(needle)||(grouped&&r.inputs.some(i=>i.path.toLowerCase().includes(needle)||(child(r,i)&&matches(child(r,i),seen))));
  };
  let rows=scoped.filter(r=>(!grouped||!nested.has(r.id))&&(!params.get('role')||params.get('role')==='all'||r.roles.includes(params.get('role')))&&(!needle||matches(r)));
  const retained=rows.filter(r=>r.display_group==='retained_original'),originalGroups=[];
  for(const r of retained){let group=originalGroups.find(g=>g.sample_id===r.sample_id&&g.dataset_id===r.dataset_id);if(!group){group={sample_id:r.sample_id,dataset_id:r.dataset_id,count:0};originalGroups.push(group);}group.count++;}
  if(params.get('view')==='library') rows=rows.filter(r=>r.display_group!=='retained_original');
  if(params.get('view')==='originals') rows=retained;
  rows.sort((a,b)=>Number(b.roles.includes('representative'))-Number(a.roles.includes('representative'))||Number(b.roles.includes('merged'))-Number(a.roles.includes('merged'))||a.path.localeCompare(b.path));
  const sort=params.get('sort_by'),descending=params.get('sort_order')==='desc';
  if(sort&&sort!=='default') {
    const value=row=>{
      const sample=data.samples.find(s=>s.id===row.sample_id);
      if(sort==='file')return row.name.toLowerCase();
      if(sort==='sample'||sort==='species')return sample[sort==='sample'?'name':'species'].toLowerCase();
      if(sort==='project')return data.projects.find(p=>p.id===sample.project_id).name.toLowerCase();
      if(sort==='data_type')return sample.datasets.find(d=>d.id===row.dataset_id).name.toLowerCase();
      return row.stats?.status==='completed'?(row.stats[sort==='q30'?'q30_percent':sort]??null):null;
    };
    rows.sort((a,b)=>a.path.localeCompare(b.path)||a.id.localeCompare(b.id));
    rows.sort((a,b)=>{const x=value(a),y=value(b);return x===null?Number(y!==null):y===null?-1:(x<y?-1:x>y?1:0)*(descending?-1:1);});
  }
  const page=Number(params.get('page')||1),size=Number(params.get('page_size')||25);
  return {items:rows.slice((page-1)*size,page*size).map(r=>grouped?expand(r):r),total:rows.length,page,page_size:size,...(params.get('view')==='library'?{original_groups:originalGroups}:{})};
};
module.exports.run=async ({open,navigate,layout,output,until})=>{
  const view=await open('data'),{page,state}=view;
  await page.getByRole('heading',{name:'데이터 관리',exact:true}).waitFor();
  await page.getByText('33개 파일',{exact:true}).waitFor();
  assert.equal(await page.locator('tr[data-catalog-file]').count(),25);
  assert.equal(await page.inputValue('[aria-label="파일 역할 필터"]'),'all');
  assert.deepEqual(await page.locator('thead th').allTextContents(),['파일','샘플 / 종','프로젝트','데이터 종류','Read 수','Bases 수','Q30']);
  assert.equal(await page.locator('tr[data-catalog-file="file0"]').getByText('미계산',{exact:true}).count(),2);
  assert.equal(await page.locator('tr[data-catalog-file="beta-file"]').getByText('계산 실패',{exact:true}).count(),2);

  assert.equal(await page.locator('tr[data-catalog-file="delta-file"]').getByText('0%',{exact:true}).count(),1);
  assert.equal(await page.locator('tr[data-catalog-file="file0"]').getByLabel('Q30 미확인',{exact:true}).count(),1);

  const originals=page.getByRole('region',{name:'원본 보관함',exact:true});
  await originals.getByRole('button',{name:/Sample alpha.*HiFi.*원본 27개/}).waitFor();
  assert.equal(await page.locator('[data-catalog-original]').count(),0);
  assert.equal(state.requests.filter(r=>r.endpoint==='/catalog/files'&&new URL(r.route.request().url()).searchParams.get('view')==='originals').length,0);
  await originals.getByRole('button',{name:/Sample alpha.*HiFi.*원본 27개/}).click();
  await until(async()=>await page.locator('[data-catalog-original]').count()===25);
  assert.equal(await page.locator('tr[data-catalog-file="retained-0"]').count(),0);
  await originals.getByRole('button',{name:'다음',exact:true}).click();
  await until(async()=>await page.locator('[data-catalog-original]').count()===2);
  await page.evaluate(()=>{navigator.clipboard.writeText=async value=>{window.catalogCopied=value;};});
  await originals.getByRole('button',{name:'경로 복사: retained_26.fastq.gz',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.catalogCopied),'/private/test/fail/retained_26.fastq.gz');
  await originals.getByRole('button',{name:'retained_26.fastq.gz',exact:true}).click();
  await page.getByRole('dialog').getByText('/private/test/fail/retained_26.fastq.gz',{exact:true}).waitFor();
  await page.getByRole('dialog').getByText('등록된 병합 입력 정보가 없습니다.',{exact:true}).waitFor();
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.fill('[aria-label="파일 검색"]','retained_26');
  await page.getByText('0개 파일',{exact:true}).waitFor();
  await originals.getByRole('button',{name:/원본 1개/}).click();
  await until(async()=>await page.locator('[data-catalog-original]').count()===1);
  await page.fill('[aria-label="파일 검색"]','');
  await page.getByText('33개 파일',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-catalog-original]').count(),0);

  assert.equal(await page.getByRole('button',{name:'source_01.fastq.gz',exact:true}).count(),0);
  await page.getByRole('button',{name:'분할 파일 1개',exact:true}).click();
  await page.getByRole('button',{name:'source_01.fastq.gz',exact:true}).waitFor();
  await page.locator('tr[data-catalog-inputs]').getByText('1,234,567',{exact:true}).waitFor();
  await page.locator('tr[data-catalog-inputs]').getByText('9,876,543,210',{exact:true}).waitFor();
  await page.locator('tr[data-catalog-inputs]').getByText('96.25%',{exact:true}).waitFor();
  assert.equal(await page.locator('tr[data-catalog-inputs] > td').getAttribute('colspan'),'7');

  assert.equal(await page.getByRole('button',{name:'source_02.fastq.gz',exact:true}).count(),0);
  await page.locator('details summary').click();
  await page.getByRole('button',{name:'source_02.fastq.gz',exact:true}).waitFor();
  assert.equal(await page.locator('details[open]').getByText('0',{exact:true}).count(),2);
  assert.equal(await page.locator('details[open]').getByLabel('Q30 미확인',{exact:true}).count(),1);

  await page.evaluate(()=>{navigator.clipboard.writeText=async value=>{window.catalogCopied=value;};});
  await page.getByRole('button',{name:'경로 복사: source_02.fastq.gz',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.catalogCopied),'/private/test/source_02.fastq.gz');
  await page.getByRole('button',{name:'source_02.fastq.gz',exact:true}).click();
  await page.getByRole('dialog').getByText('/private/test/source_02.fastq.gz',{exact:true}).waitFor();
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'분할 파일 1개',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'source_01.fastq.gz',exact:true}).count(),0);
  await page.fill('[aria-label="파일 검색"]','source_02');
  await page.getByText('1개 파일',{exact:true}).waitFor();
  await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).waitFor();
  assert.equal(await page.locator('tr[data-catalog-file]').count(),1);
  await page.fill('[aria-label="파일 검색"]','');
  const all=async count=>{assert.equal(await page.inputValue('[aria-label="파일 역할 필터"]'),'all');await page.getByText(count+'개 파일',{exact:true}).waitFor();};
  await all(33);
  await page.getByRole('button',{name:'delta.fastq.gz',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'source_01.fastq.gz',exact:true}).count(),0);
  const tree=page.locator('aside');
  const librarySearch=tree.getByRole('searchbox',{name:'Data library 검색'});
  const previousHash=await page.evaluate(()=>location.hash),previousRequests=state.requests.length;
  await librarySearch.fill('  SECOND SPECIES  ');
  await tree.getByRole('link',{name:'Sample gamma',exact:true}).waitFor();
  assert.equal(await tree.getByRole('link',{name:'Test species',exact:true}).count(),0);
  assert.equal(await tree.getByRole('link',{name:'Other project',exact:true}).count(),0);
  assert.equal(await page.evaluate(()=>location.hash),previousHash);
  assert.equal(state.requests.length,previousRequests);
  await librarySearch.fill('test species ALPHA');
  await tree.getByRole('link',{name:'Sample alpha',exact:true}).waitFor();
  assert.equal(await tree.getByRole('link',{name:'Sample beta',exact:true}).count(),0);
  await tree.getByRole('link',{name:'Sample alpha',exact:true}).click();await all(30);
  assert.equal(await tree.getByRole('link',{name:'HiFi',exact:true}).count(),0);
  await tree.getByRole('link',{name:'Sample alpha',exact:true}).click();
  await tree.getByRole('link',{name:'HiFi',exact:true}).waitFor();
  await librarySearch.fill('not-a-library-entry');
  await tree.getByRole('status').getByText('일치하는 항목이 없습니다.',{exact:true}).waitFor();
  await tree.getByRole('button',{name:'목록 검색 지우기',exact:true}).click();
  await tree.getByRole('link',{name:'Other project',exact:true}).waitFor();
  assert.equal(await tree.getByRole('link',{name:'Sample alpha',exact:true}).count(),0);
  await navigate(page,'data');await all(33);
  await tree.getByRole('link',{name:'Test project',exact:true}).click();await all(32);
  assert.equal(await page.getByRole('button',{name:'delta.fastq.gz',exact:true}).count(),0);
  await tree.getByRole('link',{name:'Test species',exact:true}).click();await all(31);
  assert.equal(await page.getByRole('button',{name:'gamma.fastq.gz',exact:true}).count(),0);
  await tree.getByRole('link',{name:'Sample alpha',exact:true}).click();await all(30);
  assert.equal(await page.getByRole('button',{name:'beta.fastq.gz',exact:true}).count(),0);
  await tree.getByRole('link',{name:'HiFi',exact:true}).click();await all(26);
  assert.equal(await page.locator('tr[data-catalog-file]').count(),25);
  await page.getByRole('button',{name:'다음',exact:true}).click();
  await until(async()=>await page.locator('tr[data-catalog-file]').count()===1);
  await tree.getByRole('link',{name:'Other project',exact:true}).click();
  await all(1);
  await page.getByRole('button',{name:'delta.fastq.gz',exact:true}).waitFor();
  assert(await page.getByRole('button',{name:'이전',exact:true}).isDisabled());
  await navigate(page,'data/study/species/sample/reads');
  await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).waitFor();
  await page.selectOption('[aria-label="파일 역할 필터"]','representative');
  await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).click();
  await page.getByRole('dialog').getByText('/private/test/source_01.fastq.gz',{exact:true}).waitFor();
  await page.getByRole('button',{name:'경로 복사',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.catalogCopied),'/private/test/combined.fastq.gz');
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
  await page.fill('[aria-label="파일 검색"]','not-present');
  await page.getByText('조건에 맞는 파일이 없습니다.',{exact:true}).waitFor();
  await page.fill('[aria-label="파일 검색"]','');
  await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).waitFor();
  await navigate(page,'data/study/species/sample/pending');
  await page.getByText('파일 연결 예정',{exact:true}).waitFor();
  await navigate(page,'data/study/species/sample/rna');
  await all(4);
  assert.equal(await page.locator('tr[data-catalog-file]').count(),4);
  for(const file of data.files.filter(f=>f.dataset_id==='rna')) await page.getByRole('button',{name:file.name,exact:true}).waitFor();
  assert.equal(await page.locator('tr[data-catalog-inputs]').count(),0);
  assert.equal(await page.locator('tbody button[aria-expanded]').count(),0);
  const routes=['data','data/study','data/study/species','data/study/species/sample','data/study/species/sample/reads'];
  for(const language of ['ko','en']) {
    await page.selectOption('#language',language);
    for(const width of [1440,768,390,320]) {
      await page.setViewportSize({width,height:1000});
      for(const route of routes) {
        await navigate(page,route);
        await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).waitFor();
        assert.equal(await page.locator('table').count(),1);
        await page.getByRole('button',{name:language==='ko'?'분할 파일 1개':'1 input files',exact:true}).click();
        await page.getByRole('button',{name:'source_01.fastq.gz',exact:true}).waitFor();
        await layout(page,'catalog-'+language+'-'+route,width);
        if(language==='en')assert(!/[가-힣]/.test(await page.locator('main').innerText()),route+' untranslated');
      }
      if([1440,390].includes(width))await page.screenshot({path:path.join(output,`catalog-files-${language}-${width}.png`),fullPage:true});
    }
  }
  await navigate(page,'data');await page.selectOption('[aria-label="Filter by file role"]','all');await page.fill('[aria-label="Search files"]','Other project');
  await page.getByText('1 files',{exact:true}).waitFor();
  await page.getByRole('button',{name:'delta.fastq.gz',exact:true}).waitFor();
  assert.equal(await page.locator('tbody tr').count(),1);
  await page.getByRole('button',{name:'Open menu'}).click();
  const menu=page.getByRole('dialog');
  await menu.getByRole('searchbox',{name:'Search Data library'}).fill('other-species');
  await menu.getByRole('link',{name:'Sample delta',exact:true}).waitFor();
  assert.equal(await menu.getByRole('link',{name:'Test project',exact:true}).count(),0);
  await menu.getByRole('button',{name:'Clear library search'}).click();
  await page.getByRole('dialog').getByRole('link',{name:'Test project',exact:true}).click();
  await page.getByRole('dialog').waitFor({state:'hidden'});
  await navigate(page,'data/study/species/missing');
  await page.getByRole('alert').getByText('This project or sample could not be found.',{exact:true}).waitFor();
  await navigate(page,'data/study/species/sample/reads');await page.getByRole('button',{name:'combined.fastq.gz',exact:true}).waitFor();
  await page.locator('#logout').click();await page.locator('#login').waitFor();
  assert(!(await page.locator('body').innerText()).includes('combined.fastq.gz'));
  assert.equal(state.mutations.length,0);await view.context.close();
  const denied=await open('data','bob');await denied.page.getByText('데이터 관리 열람 권한이 필요합니다.',{exact:true}).waitFor();
  assert.equal(denied.state.requests.filter(r=>r.endpoint.startsWith('/catalog')).length,0);
  await denied.context.close();
  return {retained_originals_collapsed:true,retained_originals_lazy_paging:true,retained_originals_search_and_copy:true,library_search:true,library_search_mobile:true,sequence_count_columns:true,sequence_counts_exact:true,missing_counts_not_zero:true,all_files_default:true,independent_rna_libraries:true,collapsed_inputs:true,nested_inputs:true,input_search:true,input_copy:true,all_files_table:true,hierarchy_filters:true,scope_change_resets_paging:true,empty_dataset:true,search_and_paging:true,role_filter:true,path_copy:true,merge_inputs:true,restricted_access:true,logout_clears_data:true,responsive_views:40};
};
