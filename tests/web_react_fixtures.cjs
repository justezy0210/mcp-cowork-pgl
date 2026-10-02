'use strict';
const nodes = ['224','226','227','228','229'].map(id => ({id,cpus:32,memory_mib:1393157,enabled:true,
  reserved_cpus:8,reserved_memory_mib:65536,job_counts:{RUNNING:2,QUEUED:3,DISPATCHING:0,UNKNOWN:0},
  recent_report:true,last_seen:1790654400,gpus:[{id:'fixture-gpu-' + id,model:'NVIDIA GeForce GTX 1080 Ti',reserved:false}]}));
const profile = {user_id:'alice',is_admin:true,allowed_nodes:nodes.map(n => n.id),identity:{configured:true,uid:1101,gid:1100},notification:{configured:true,channel_id:'123456789012345678'}};
const enrollment = {id:'fixture-enrollment',account_name:'researcher',email:'researcher.long.account.name@example.test',uid:1201,gid:1200,channel_id:'123456789012345678'};
const environment = {id:'fixture-env',name:'연구 컨테이너',user_id:'researcher',node_id:'227',ssh_target:'researcher@203.255.11.227:11010',workdir:'/10Gdata/researcher/projects/example-analysis',uid:1201,gid:1200,allowed:true,status:'READY'};
const job = {id:'fixture-job',number:1,name:'RNA-seq 연구 데이터 분석',user_id:'researcher',node_id:'227',state:'RUNNING',cpus:8,memory_mib:65536,gpu_count:0,created_at:1790654400,started_at:1790654410};
const pageData = items => ({items,total:42,page:1,page_size:20});

module.exports = {nodes,profile,enrollment,environment,job,pageData};
