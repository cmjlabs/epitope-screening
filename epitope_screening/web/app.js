'use strict';
const $ = id => document.getElementById(id);
const all = selector => [...document.querySelectorAll(selector)];
let state = null, selected = null, currentTask = null, loaded = false, polling = false;
let settings = {}, prediction = {}, toastTimer, submitting = false;
const uploads = new WeakMap();
const labels = {running:'运行中',cancelling:'正在取消',complete:'已完成',failed:'未完成',cancelled:'已取消',interrupted:'可恢复'};
let token = new URLSearchParams(location.hash.slice(1)).get('token');
if (token) {sessionStorage.setItem('epitope-session',token);history.replaceState(null,'',location.pathname);}
else token = sessionStorage.getItem('epitope-session') || '';
function notice(text) {$('notice').textContent=text;$('notice').hidden=!text;}
function toast(text) {clearTimeout(toastTimer);$('toast').textContent=text;$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
function view(name) {all('.view').forEach(el=>el.hidden=el.id!=='view-'+name);all('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===name));$('page-name').textContent={analysis:'新建分析',history:'任务记录',setup:'首次配置',guide:'使用说明'}[name];}
all('.nav').forEach(button=>button.addEventListener('click',()=>view(button.dataset.view)));
async function api(path,body) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-App-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if (!response.ok) throw new Error(data.error || '本机程序未完成请求。');
  return data;
}
function upload(kind,file) {
  if (!file) return Promise.reject(new Error('请选择 '+{fasta:'FASTA',mhci:'MHC-I 表',mhcii:'MHC-II 表',tc1:'TC1 工具包',tc2:'TC2 工具包',cutoff:'cutoff 表'}[kind]+' 文件。'));
  const cached=uploads.get(file);
  if(cached?.kind===kind)return Promise.resolve(cached.id);
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('POST','/api/upload?'+new URLSearchParams({kind,name:file.name}));
    xhr.setRequestHeader('X-App-Token',token);
    xhr.upload.addEventListener('progress',event=>{if(event.lengthComputable)notice('正在将文件交给本机程序：'+file.name+' · '+Math.round(event.loaded/event.total*100)+'%');});
    xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status!==200)throw new Error(data.error);uploads.set(file,{kind,id:data.id});notice('');resolve(data.id);}catch(error){reject(error);}};
    xhr.onerror=()=>reject(new Error('文件传输未完成，请确认本机程序仍在运行后重试。'));
    xhr.send(file);
  });
}
function mode(){return document.querySelector('input[name=mode]:checked').value;}
function applySettings(data){settings=structuredClone(data);all('[data-setting]').forEach(input=>{const value=data[input.dataset.setting];if(value!==undefined){if(input.type==='checkbox')input.checked=value;else input.value=value;}});}
function applyPrediction(data){prediction=structuredClone(data);$('alleles-i').value=data.alleles_i.join(', ');$('alleles-ii').value=data.alleles_ii.join(', ');all('[data-prediction]').forEach(input=>input.value=data[input.dataset.prediction]);}
function getSettings(){const result=structuredClone(settings);all('[data-setting]').forEach(input=>{const name=input.dataset.setting;result[name]=input.type==='checkbox'?input.checked:input.tagName==='SELECT'?input.value:Number(input.value);});return result;}
function getPrediction(){const result=structuredClone(prediction);result.alleles_i=$('alleles-i').value.split(',').map(s=>s.trim()).filter(Boolean);result.alleles_ii=$('alleles-ii').value.split(',').map(s=>s.trim()).filter(Boolean);all('[data-prediction]').forEach(input=>result[input.dataset.prediction]=Number(input.value));return result;}
function updateMode(){const value=mode();all('.online-only').forEach(el=>el.hidden=value!=='online');all('.offline-only').forEach(el=>el.hidden=value!=='offline');all('.predict-only').forEach(el=>el.hidden=value==='offline');const ready=state?.setup.ready;const active=state?.tasks.some(t=>['running','cancelling'].includes(t.status));
  $('start').disabled=!loaded||submitting||active||state?.setup.busy||(value==='local'&&!ready);
  $('demo').disabled=!loaded||submitting||active||state?.setup.busy;
  const note=$('mode-note');note.replaceChildren();
  if(value==='local'&&!ready){note.append('本地环境尚未配置。');const button=document.createElement('button');button.type='button';button.textContent='前往首次配置 →';button.addEventListener('click',()=>view('setup'));note.append(button);}
  else note.textContent={local:'本地环境已就绪。预测、筛选和报告均在这台电脑完成。',online:'预测在 IEDB 官方服务器完成，本机负责筛选和报告；速度受网络与官方排队影响。',offline:'只读取你提供的预测表，不提交预测任务。'}[value];
  $('run-hint').textContent=value==='local'&&!ready?'请先完成一次本地配置。':value==='online'?'开始前请确认上方的序列发送说明。':'结果会自动保存在本机。';
}
all('input[name=mode]').forEach(input=>input.addEventListener('change',updateMode));
$('fasta-file').addEventListener('change',()=>{$('fasta-file-name').textContent=$('fasta-file').files[0]?.name || '未选择文件';if($('fasta-file').files[0])$('fasta-text').value='';});
$('load-example').addEventListener('click',()=>{if(!state)return;$('fasta-text').value=state.public_fasta;$('fasta-file').value='';$('fasta-file-name').textContent='已载入公开 LCMV 示例';$('preset').value='small';applyPrediction(state.small_prediction);toast('已载入公开示例，并选择每类一个 HLA 的快速试跑。');});
$('preset').addEventListener('change',()=>{if(!state)return;if($('preset').value==='full')applyPrediction(state.defaults.prediction);if($('preset').value==='small')applyPrediction(state.small_prediction);if($('preset').value==='custom')$('advanced').open=true;});
['alleles-i','alleles-ii'].forEach(id=>$(id).addEventListener('input',()=>$('preset').value='custom'));
$('analysis-form').addEventListener('submit',async event=>{event.preventDefault();if(submitting)return;submitting=true;updateMode();try{
  const payload={mode:mode(),name:$('task-name').value,settings:getSettings(),prediction:getPrediction(),allow_sequence_upload:$('online-consent').checked};
  const text=$('fasta-text').value.trim();if(text)payload.fasta_text=text;else payload.fasta=await upload('fasta',$('fasta-file').files[0]);
  if(payload.mode==='offline'){payload.mhci=await upload('mhci',$('mhci-file').files[0]);payload.mhcii=await upload('mhcii',$('mhcii-file').files[0]);}
  if($('cutoff-file').files[0])payload.cutoff=await upload('cutoff',$('cutoff-file').files[0]);
  const task=await api('/api/start',payload);selected=task.id;renderTask(task);notice('');await refresh();
}catch(error){notice(error.message);toast(error.message);}finally{submitting=false;updateMode();}});
$('demo').addEventListener('click',async()=>{try{const task=await api('/api/demo',{});selected=task.id;renderTask(task);view('analysis');await refresh();}catch(error){toast(error.message);}});
function badge(element,status){element.textContent=labels[status] || status;element.className='badge '+(['running','cancelling'].includes(status)?'running':status==='complete'?'complete':status==='failed'?'failed':'neutral');}
function renderTask(task){currentTask=task;selected=task.id;badge($('task-status'),task.status);$('task-title').textContent=task.name;$('task-message').textContent=task.message;$('task-progress').value=task.progress || 0;$('task-percent').textContent=(task.progress || 0)+'%';
  const seconds=['running','cancelling'].includes(task.status)?Date.now()/1000-task.started:task.elapsed_seconds || 0;$('task-time').textContent='耗时 '+(seconds>=60?Math.floor(seconds/60)+'分 '+Math.floor(seconds%60)+'秒':Math.floor(seconds)+'秒');
  const complete=task.status==='complete';$('task-metrics').hidden=!complete;$('result-actions').hidden=!complete;$('cancel').hidden=!['running','cancelling'].includes(task.status);$('resume').hidden=task.mode==='offline'||['running','cancelling'].includes(task.status);$('cancel').disabled=task.status==='cancelling';
  $('task-logs').textContent=(task.logs || []).join('\n') || '正在准备任务…';
  $('empty-result').hidden=!complete||task.summary?.selected!==0;
  if(complete){$('window-count').textContent=task.summary.windows.toLocaleString();$('selected-count').textContent=task.summary.selected.toLocaleString();renderPreview(task.summary.preview);}
  else $('preview-card').hidden=true;
}
function renderPreview(rows){$('preview-card').hidden=!rows.length;if(!rows.length)return;const columns=[['peptide','多肽序列'],['window_start','起点'],['window_end','终点'],['mhc1_coverage','MHC-I 覆盖率'],['mhc2_coverage','MHC-II 覆盖率']].filter(([key])=>key in rows[0]);const table=$('preview-table');table.replaceChildren();const head=document.createElement('thead'),tr=document.createElement('tr');columns.forEach(([,label])=>{const th=document.createElement('th');th.textContent=label;tr.append(th);});head.append(tr);table.append(head);const body=document.createElement('tbody');rows.forEach(row=>{const tr=document.createElement('tr');columns.forEach(([key])=>{const td=document.createElement('td');td.textContent=row[key]==null?'—':key.includes('coverage')?(row[key]*100).toFixed(1)+'%':String(row[key]);tr.append(td);});body.append(tr);});table.append(body);}
async function selectTask(id){try{selected=id;renderTask(await api('/api/task?id='+id));view('analysis');$('task-panel').scrollIntoView({behavior:'smooth',block:'center'});}catch(error){toast(error.message);}}
function renderHistory(tasks){const list=$('history-list');list.replaceChildren();if(!tasks.length){const p=document.createElement('p');p.className='empty-state';p.textContent='还没有分析任务。可以先运行离线演示。';list.append(p);return;}
 tasks.forEach(task=>{const row=document.createElement('div');row.className='history-item';const main=document.createElement('div');main.className='history-main';const title=document.createElement('strong');title.textContent=task.name;const info=document.createElement('small');info.textContent=new Date(task.created).toLocaleString()+' · '+({local:'本地预测',online:'IEDB 在线',offline:task.demo?'人工数据演示':'导入表'}[task.mode]);main.append(title,info);const status=document.createElement('span');badge(status,task.status);const button=document.createElement('button');button.className='secondary compact';button.textContent='打开任务';button.addEventListener('click',()=>selectTask(task.id));row.append(main,status,button);list.append(row);});}
$('cancel').addEventListener('click',async()=>{if(!selected)return;try{renderTask(await api('/api/cancel',{id:selected}));}catch(error){toast(error.message);}});
$('resume').addEventListener('click',async()=>{if(!selected)return;try{renderTask(await api('/api/resume',{id:selected}));await refresh();}catch(error){toast(error.message);}});
async function download(kind){if(!currentTask)return;const suffix=kind==='excel'?'results.xlsx':'selected_windows.csv';const file=(currentTask.files || []).filter(name=>name.endsWith(suffix)).sort().at(-1);if(!file){toast('没有找到对应报告。');return;}try{const response=await fetch('/api/file?'+new URLSearchParams({id:currentTask.id,name:file}),{headers:{'X-App-Token':token}});if(!response.ok)throw new Error((await response.json()).error);const blob=await response.blob();saveBlob(blob,'Epitope-'+currentTask.id.slice(0,8)+'-'+suffix);}catch(error){toast(error.message);}}
function saveBlob(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
$('download-excel').addEventListener('click',()=>download('excel'));$('download-csv').addEventListener('click',()=>download('csv'));
function renderSetup(value){$('setup-badge').textContent=value.ready?'已就绪':value.busy?'配置中':'待配置';$('setup-badge').className='badge '+(value.ready?'complete':value.busy?'running':'neutral');$('setup-dot').hidden=value.ready;$('setup-stage').textContent=value.error || value.stage;$('setup-percent').textContent=value.progress+'%';$('setup-progress').value=value.progress;$('setup-logs').textContent=value.logs.join('\n') || '点击“下载并配置官方工具”开始。';$('data-dir').textContent=value.data_directory;
 const checks=$('system-checks');checks.replaceChildren();const entries=[['运行平台',value.supported?'Linux x86_64':'当前系统不支持自动本地配置',value.supported],['磁盘空间',(value.free_bytes/1024**3).toFixed(1)+' GB 可用',value.free_bytes>=12*1024**3],['系统组件',value.missing_system.length?'待安装：'+value.missing_system.join('、'):'已具备',!value.missing_system.length],['预测运行环境',value.ready?'已配置':'待配置',value.ready]];entries.forEach(([label,text,ok])=>{const row=document.createElement('div');row.className='check-item';const icon=document.createElement('span');icon.textContent=ok?'✓':'○';icon.className=ok?'good':'pending';const name=document.createElement('span');name.textContent=label;const status=document.createElement('b');status.textContent=text;status.className=ok?'good':'pending';row.append(icon,name,status);checks.append(row);});
 ['setup-download','setup-import','setup-check'].forEach(id=>$(id).disabled=value.busy||!value.supported||submitting||state.tasks.some(t=>['running','cancelling'].includes(t.status)));
}
async function setup(action){if(submitting)return;submitting=true;updateMode();try{const data={action,license_accepted:$('license-consent').checked,system_accepted:$('system-consent').checked};if(action!=='check'&&!data.license_accepted)throw new Error('请先阅读官方许可，并勾选确认你有权使用工具。');if(action==='import'){data.tc1=await upload('tc1',$('tc1-file').files[0]);data.tc2=await upload('tc2',$('tc2-file').files[0]);}await api('/api/setup',data);toast(action==='check'?'正在检查已有环境。':'配置已开始，请留意系统授权窗口。');await refresh();}catch(error){toast(error.message);notice(error.message);}finally{submitting=false;updateMode();}}
$('setup-download').addEventListener('click',()=>setup('download'));$('setup-import').addEventListener('click',()=>setup('import'));$('setup-check').addEventListener('click',()=>setup('check'));
$('copy-path').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(state.data_directory);toast('已复制本机数据位置。');}catch{toast('请选中上方路径后复制。');}});
function exportJSON(value,name){saveBlob(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}),name);}
$('settings-export').addEventListener('click',()=>exportJSON(getSettings(),'screening-settings.json'));$('prediction-export').addEventListener('click',()=>exportJSON(getPrediction(),'prediction-settings.json'));
$('settings-import').addEventListener('change',async()=>{try{applySettings(JSON.parse(await $('settings-import').files[0].text()));toast('筛选参数已载入，开始前会再次校验。');}catch{toast('无法读取筛选参数 JSON。');}});
$('prediction-import').addEventListener('change',async()=>{try{applyPrediction(JSON.parse(await $('prediction-import').files[0].text()));$('preset').value='custom';toast('预测参数已载入。');}catch{toast('无法读取预测参数 JSON，请勿与筛选参数混用。');}});
$('quit').addEventListener('click',async()=>{if(!confirm('确认退出本机程序？已保存的任务和报告会保留。'))return;try{await api('/api/quit',{});loaded=false;notice('本机程序已退出。下次双击 Start-Web 即可重新打开。');$('connection').textContent='已退出';updateMode();}catch(error){toast(error.message);}});
async function refresh(){if(polling)return;polling=true;try{state=await api('/api/state');if(!loaded){loaded=true;const population=$('population');population.replaceChildren();state.populations.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name;population.append(option);});applySettings(state.defaults.settings);applyPrediction(state.defaults.prediction);if(!state.setup.supported)document.querySelector('input[name=mode][value=online]').checked=true;}
 $('version').textContent='v'+state.version+' · 本机服务';$('connection').className='connection ready';$('connection').replaceChildren();const dot=document.createElement('i');$('connection').append(dot,'本机已连接');renderHistory(state.tasks);renderSetup(state.setup);updateMode();
 if(!selected&&state.tasks.length)selected=state.tasks[0].id;
 if(selected)renderTask(await api('/api/task?id='+selected));
}catch(error){$('connection').className='connection';$('connection').textContent='未连接';if(!loaded)notice(error.message || '请先双击 Start-Web 启动本机程序。');}finally{polling=false;}}
if(location.protocol==='file:'){notice('这是页面预览。请先双击 Start-Web；浏览器会自动打开可运行的操作页面。');$('connection').textContent='页面预览';$('demo').disabled=true;$('start').disabled=true;view('guide');}
else{refresh();setInterval(()=>{if(loaded)refresh();},2000);}
