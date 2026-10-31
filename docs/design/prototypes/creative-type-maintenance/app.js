/* Design prototype only: local demo data; no API requests or model calls. */
(() => {
  'use strict';
  const KEY = 'novelos.creative-types.design.v1';
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $ = selector => document.querySelector(selector);
  const stages = ['brainstorm', 'story_design', 'chapter_plan', 'draft', 'review'];
  const stageNames = {brainstorm:'头脑风暴',story_design:'完整骨架',chapter_plan:'章节规划',draft:'正文写作',review:'评议'};
  const dimensions = {marriage:'婚姻与亲密关系',family:'家庭与代际',growth:'成长与自我实现',world:'类型与世界',conflict:'冲突与成长',relationship:'人物与关系',tone:'情绪与文风',audience:'读者定位'};
  const importance = {essential:'优先讨论',recommended:'建议采用',optional:'可选灵感'};
  const stateNames = {enabled:'已启用',draft:'草稿',disabled:'已停用'};
  const groups = {reader_promises:'读者期待',character_dynamics:'人物与关系',voice_guidance:'文风偏好',pitfalls:'避免的问题',diagnostics:'设计检查'};
  const roleNames = {unspecified:'尚未指定作用',primary:'主导故事',supporting:'辅助情节',atmosphere:'氛围与表达'};
  const blank = () => ({schema:'creative-type-profile.v1',profile_id:uid('ct_type'),version:1,name:'',aliases:[],dimension:'world',summary:'',applicability:{readers:[],markets:[],languages:[],lengths:[]},reader_promises:[],mechanisms:[],character_dynamics:[],voice_guidance:[],pitfalls:[],diagnostics:[],combinations:[],publication_mapping:{status:'unmapped',catalog_version:'novel-types.2026-10',primary_genre_ids:[],facet_ids:[]}});
  function uid(prefix) { return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,7)}`; }
  function seed() { return window.CREATIVE_TYPE_DEMO.map(({profile,status,origin,updated}) => ({saved:clone(profile),active:status==='enabled'?clone(profile):null,status,origin,updated,history:status==='enabled'?[clone(profile)]:[]})); }
  let entries;
  try { entries = JSON.parse(localStorage.getItem(KEY)); } catch { /* File previews may deny storage. */ }
  if (!Array.isArray(entries) || !entries.length || !entries.every(e => e.saved?.schema==='creative-type-profile.v1' && Array.isArray(e.history))) entries = seed();
  // Add new demo references by stable ID; never overwrite the reader's saved edits or versions.
  const existingIds = new Set(entries.map(e=>e.saved.profile_id));
  entries.push(...seed().filter(e=>!existingIds.has(e.saved.profile_id)));
  let current = entries.find(e=>e.saved.profile_id==='ct_marriage_betrayal')||entries[0], draft = clone(current.saved), tab = 'core', dirty = false, previewMode = 'active', previewStage = 'brainstorm', renderId = 0;
  const selected = new Map([['ct_marriage_betrayal','primary'],['ct_female_awakening','supporting']]);
  const mappingOptions = [...new Set(window.CREATIVE_TYPE_DEMO.flatMap(e => [...e.profile.publication_mapping.primary_genre_ids,...e.profile.publication_mapping.facet_ids]))];
  const primaryIds = new Set(window.CREATIVE_TYPE_DEMO.flatMap(e => e.profile.publication_mapping.primary_genre_ids));
  const mappingLabels = {supernatural:'超自然',fantasy:'奇幻',werewolf:'狼人',family_drama:'家庭情感',domestic_betrayal:'家庭背叛',mystery:'悬疑',suspenseful:'紧张悬疑',revenge:'复仇',romance:'言情',womens_fiction:'女性小说',marriage_crisis:'婚姻危机',female_growth:'女性成长',second_chance:'破镜重圆',billionaire:'富豪',family_domestic:'家庭生活',single_parent:'单亲'};
  function persist() { try { localStorage.setItem(KEY,JSON.stringify(entries)); return true; } catch { toast('浏览器不允许本地保存。本次修改仍在内存中，关闭页面会丢失。'); return false; } }
  let toastTimer;
  function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,5000); }
  function options(map,value) { return Object.entries(map).map(([key,label])=>`<option value="${esc(key)}" ${key===value?'selected':''}>${esc(label)}</option>`).join(''); }
  function field(label,path,value,{wide=false,area=false,lines=false,hint=''}={}) { return `<label class="field ${wide?'wide':''}"><span class="field-label">${label}</span>${area?`<textarea rows="2" data-field="${path}" ${lines?'data-lines="true"':''}>${esc(Array.isArray(value)?value.join('\n'):value)}</textarea>`:`<input data-field="${path}" value="${esc(Array.isArray(value)?value.join('，'):value)}" ${lines?'data-comma="true"':''}>`}${hint?`<span class="field-hint">${hint}</span>`:''}</label>`; }
  function head(number,title,subtitle) { return `<div class="section-head"><div><span class="section-index">${number} / 类型写作说明</span><h3>${title}</h3><p>${subtitle}</p></div></div>`; }
  function stagesEditor(path,values) { return `<details class="stage-editor"><summary>适用阶段 · ${values.map(s=>stageNames[s]).join('、') || '请至少选择一个'}</summary><div class="mapping-options">${stages.map(stage=>`<label><input type="checkbox" data-stages="${path}" value="${stage}" ${values.includes(stage)?'checked':''}>${stageNames[stage]}</label>`).join('')}</div><p class="small-note">阶段是资料用途设计；当前生产接入仍需按 M1.1 / M2 分批实现。</p></details>`; }
  function guidance(key,description) { return `<section class="block"><h3>${groups[key]}</h3><p class="block-intro">${description}</p><div class="guidance-rows">${draft[key].map((item,i)=>`<div class="guidance-entry"><div class="guidance-row"><span class="row-index">${String(i+1).padStart(2,'0')}</span><textarea aria-label="${groups[key]} ${i+1}" rows="2" data-field="${key}.${i}.text">${esc(item.text)}</textarea><select aria-label="${groups[key]} ${i+1} 重要性" data-field="${key}.${i}.importance">${options(importance,item.importance)}</select><button class="remove" data-action="remove-row" data-key="${key}" data-index="${i}" aria-label="删除${groups[key]} ${i+1}">×</button></div>${stagesEditor(`${key}.${i}.stages`,item.stages)}</div>`).join('')}</div><button class="add-row" data-action="add-row" data-key="${key}">＋ 添加${groups[key]}</button></section>`; }
  function renderList() {
    const term=$('#search').value.trim().toLowerCase(), filter=$('#status-filter').value, dimension=$('#dimension-filter').value;
    const shown=entries.filter(e=>(filter==='all'||e.status===filter)&&(dimension==='all'||e.saved.dimension===dimension)&&[e.saved.name,...e.saved.aliases,e.saved.summary].join(' ').toLowerCase().includes(term));
    $('#library-count').textContent=`${shown.length} 个类型`;
    $('#type-list').innerHTML=Object.entries(dimensions).map(([key,label])=>{const list=shown.filter(e=>e.saved.dimension===key);return list.length?`<section class="library-group" aria-label="${label}"><h3 class="group-title">${label}<span>${list.length}</span></h3>${list.map(e=>`<button class="type-item ${e===current?'selected':''}" data-action="select" data-id="${e.saved.profile_id}" ${e===current?'aria-current="true"':''}><span class="row"><span class="type-name">${esc(e.saved.name||'未命名类型')}</span><span class="state ${e.status}">${stateNames[e.status]}</span></span><p>${esc(e.saved.summary||'添加阅读体验和故事说明。')}</p><span class="list-meta"><span>${dimensions[e.saved.dimension]}</span><span>${e.active?'v'+e.active.version:'尚未启用'} · ${e.origin==='builtin'?'内置起点':'自定义'}</span></span></button>`).join('')}</section>`:'';}).join('') || '<p class="empty">没有匹配的类型。<br>试试其他关键词，或新建一个类型。</p>';
  }
  function renderStatus() {
    $('#type-title').textContent=draft.name||'未命名类型'; $('#type-mark').textContent=(draft.name||'新').slice(0,1);
    $('#type-state').innerHTML=`<span class="state ${current.status}">${stateNames[current.status]}</span>`;
    $('#type-subtitle').textContent=`${dimensions[draft.dimension]} · ${current.active?'启用内容 v'+current.active.version:'尚无启用内容'} · ${current.origin==='builtin'?'内置资料，可自由修改':'你的创作资料'}`;
    $('#disable-button').hidden=current.status!=='enabled';
    $('#save-state').textContent=dirty?'有未保存的修改':current.status==='enabled'?'已保存在本地演示':'草稿已保存在本地演示';
    const changed=current.active && JSON.stringify(draft)!==JSON.stringify(current.active);
    $('#save-detail').textContent=current.status==='disabled'?'已停用；历史讨论仍保留原快照。':changed?'编辑草稿不会影响已启用版本，或已有讨论。':current.active?'新讨论使用启用版本；已有讨论保持原快照。':'保存并启用后，才会出现在新讨论的类型选择中。';
    $('#save-draft').disabled=!dirty;
  }
  function render() {
    renderList(); renderStatus(); renderId++;
    document.querySelectorAll('[data-tab]').forEach(button=>{const active=button.dataset.tab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
    $('#panel').setAttribute('aria-labelledby',`tab-${tab}`);
    const views={core:coreView,mechanisms:mechanismsView,voice:voiceView,checks:checksView,preview:previewView};
    $('#panel').innerHTML=`<div class="inner">${views[tab]()}</div>`;
    if (tab==='preview') fillPreview(renderId);
  }
  function coreView() { return head('01','让读者知道，为什么要读这类故事','先定义期待的体验，再让模型为每本书设计不同的实现方式。') + `<div class="fields">${field('类型名称','name',draft.name)}<label class="field"><span class="field-label">创作维度</span><select data-field="dimension">${options(dimensions,draft.dimension)}</select></label>${field('一句话说明','summary',draft.summary,{wide:true,area:true,hint:'说明故事吸引力；不预先指定人物姓名或固定结局。'})}${field('别名','aliases',draft.aliases,{wide:true,lines:true,hint:'用于查找与旧数据识别，以逗号分隔。不会据此模糊匹配其他类型。'})}</div>`+guidance('reader_promises','读者希望经历什么？这些说明会指导本书提案，不自动成为剧情事实。')+`<section class="block"><h3>适用范围 <small class="small-note">留空即不限定</small></h3><p class="block-intro">类型资料可以适配不同市场；具体作品的读者设定仍由作者决定。</p><div class="fields">${field('目标读者','applicability.readers',draft.applicability.readers,{lines:true})}${field('发行市场','applicability.markets',draft.applicability.markets,{lines:true})}${field('作品语言','applicability.languages',draft.applicability.languages,{lines:true})}${field('篇幅或形式','applicability.lengths',draft.applicability.lengths,{lines:true})}</div></section><div class="hint-panel"><strong>资料是创作起点。</strong> 模型会结合本书人物提出具体方案，由作者推进取舍。不会把所有常见设定强行写进每本书。</div>`; }
  function mechanismsView() {
    const labels={trigger:'触发条件',scope:'作用范围',limits:'能力或规则的限制',exceptions:'允许的例外',costs:'必须承担的代价',choices:'迫使人物做出的选择',payoffs:'可以形成的回报'};
    return head('02','让类型真正改变人物的选择','规则 → 限制 → 代价 → 选择 → 后果与回报。不是只添加几个类型关键词。')+draft.mechanisms.map((m,i)=>`<details class="mechanism-card" ${i===0?'open':''}><summary><div><strong>${esc(m.name||'新故事机制')}</strong><p>${esc(m.principle||'这条机制为什么能推动故事？')}</p></div><span class="small-tag">${importance[m.importance]} ⌄</span></summary><div class="mechanism-content"><div class="fields">${field('机制名称',`mechanisms.${i}.name`,m.name)}<label class="field"><span class="field-label">重要性</span><select data-field="mechanisms.${i}.importance">${options(importance,m.importance)}</select></label>${field('机制的核心作用',`mechanisms.${i}.principle`,m.principle,{wide:true,area:true})}${Object.entries(labels).map(([key,label])=>field(label,`mechanisms.${i}.${key}`,m[key],{area:true,lines:true,wide:key==='payoffs',hint:'每行一项；留空表示由本书设计补充。'})).join('')}</div>${stagesEditor(`mechanisms.${i}.stages`,m.stages)}<button class="button ghost" data-action="remove-row" data-key="mechanisms" data-index="${i}">移除此机制</button></div></details>`).join('')+`<button class="add-row" data-action="add-mechanism">＋ 添加故事机制</button><div class="hint-panel"><strong>每本书仍有自己的规则。</strong> 这里维护可选的机制方法，不要求所有狼人小说都使用命定伴侣或族群等级。模型应主动提出差异化方案。</div>`;
  }
  function voiceView() { return head('03','人物如何行动，文字如何表达','维护人物动力与表达偏好，让题材有辨识度，也给作者留下空间。')+guidance('character_dynamics','角色之间有哪些可以持续产生选择与变化的力量？避免把性别或身份当成固定性格。')+guidance('voice_guidance','写什么、如何写；用可观察的表达偏好，替代“必须高级、必须精彩”。'); }
  function checksView() {
    return head('04','守住类型辨识度，也允许混合创新','检查提供讨论方向；不会因为文学意见不同就阻断创作。')+guidance('pitfalls','指出常见失效方式，模型可以据此提出更好的替代方案。')+guidance('diagnostics','通过具体问题复核因果、人物选择和回报，避免机械打分。')+`<section class="block"><h3>与其他类型组合</h3><p class="block-intro">只有本书同时选择对应类型时，才应用以下融合说明。</p>${draft.combinations.map((c,i)=>`<div class="mechanism-card"><div class="mechanism-content"><div class="fields"><label class="field wide"><span class="field-label">合作类型</span><select data-field="combinations.${i}.with_profile_id">${entries.filter(e=>e!==current).map(e=>`<option value="${esc(e.saved.profile_id)}" ${c.with_profile_id===e.saved.profile_id?'selected':''}>${esc(e.saved.name||'未命名类型')}</option>`).join('')}</select></label>${field('可以强化什么',`combinations.${i}.opportunities`,c.opportunities,{area:true,lines:true})}${field('需要协调什么',`combinations.${i}.tensions`,c.tensions,{area:true,lines:true})}</div><button class="button ghost" data-action="remove-row" data-key="combinations" data-index="${i}">移除组合说明</button></div></div>`).join('')}<button class="add-row" data-action="add-combination">＋ 添加组合说明</button></section><section class="block"><h3>发行分类映射</h3><p class="block-intro">只选后台已有分类，作为导出时的候选；不会改变创作说明。</p><div class="mapping-row"><span class="small-note">${draft.publication_mapping.catalog_version}</span><span class="state ${draft.publication_mapping.status==='mapped'?'enabled':'draft'}">${draft.publication_mapping.status==='mapped'?'已关联候选':'待映射'}</span></div><div class="mapping-options">${mappingOptions.map(id=>`<label><input type="checkbox" data-mapping="${id}" ${[...draft.publication_mapping.primary_genre_ids,...draft.publication_mapping.facet_ids].includes(id)?'checked':''}>${esc(mappingLabels[id]||id)}</label>`).join('')}</div><p class="field-hint">原型展示部分字典选项。正式页面读取完整字典，不能自行新增发布 ID。</p><div class="hint-panel amber">自定义类型可先用于头脑风暴。未完成合法分类选择的作品不能生成正式发布包；多个类型的映射也不会直接叠加为最终分类。</div></section>`;
  }
  function previewView() { return head('05','在开始创作前，知道模型会读到什么','这里展示资料预览；已运行讨论应在该讨论内查看真实请求快照。')+`<div class="preview-controls"><div class="read-mode"><button data-action="preview-active" class="${previewMode==='active'?'selected':''}" aria-pressed="${previewMode==='active'}">当前启用版本</button><button data-action="preview-draft" class="${previewMode==='draft'?'selected':''}" aria-pressed="${previewMode==='draft'}">预览当前草稿</button></div><select id="preview-stage" aria-label="预览适用阶段">${options(stageNames,previewStage)}</select></div><div id="preview-content" aria-live="polite"><p class="empty">正在编译本地预览…</p></div><section class="block"><h3>启用历史</h3><p class="block-intro">恢复版本会生成一份草稿；不会改写历史或替换已有讨论。</p>${[...current.history].reverse().map(p=>`<div class="version-row"><div>v${p.version} · ${esc(p.name)}<small>${p.version===current.active?.version?'当前保留版本':'历史内容，可作为新草稿起点'}</small></div><button class="button secondary" data-action="restore" data-version="${p.version}">恢复为草稿</button></div>`).join('')||'<p class="small-note">尚未启用任何版本。</p>'}</section>`; }
  const canonical = value => value && typeof value==='object' ? Array.isArray(value)?value.map(canonical):Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
  async function hash(value) { const bytes=new TextEncoder().encode(JSON.stringify(canonical(value))); if (!globalThis.crypto?.subtle) throw new Error('当前打开方式不支持哈希计算，请使用 localhost 预览。'); return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join(''); }
  async function compile(profiles,stage,roles=new Map()) {
    const ids=new Set(profiles.map(p=>p.profile_id)), manifest=[];
    const output=await Promise.all(profiles.map(async p=>{
      const content={name:p.name,dimension:p.dimension,summary:p.summary,applicability:clone(p.applicability)};
      [...Object.keys(groups),'mechanisms'].forEach(key=>{content[key]=p[key].filter(item=>{const included=item.stages.includes(stage);manifest.push({profile_id:p.profile_id,item_id:item.id,included,reason:included?'stage_applies':'other_stage'});return included;}).map(clone);});
      content.combinations=p.combinations.filter(c=>ids.has(c.with_profile_id)).map(clone);
      return {profile_id:p.profile_id,version:p.version,source_sha256:await hash(p),role:roles.get(p.profile_id)||'unspecified',content};
    }));
    return {schema:'genre-context.v1',stage,authority:'type_guidance_not_book_facts',profiles:output,projection_manifest:manifest,provenance:{selection_revision:1,compiler_version:'genre-context-compiler.v1'},preview_only:true};
  }
  async function fillPreview(id) {
    const p=previewMode==='draft'?clone(draft):current.status==='enabled'?clone(current.active):null;
    if (!p) { $('#preview-content').innerHTML='<div class="hint-panel amber">此类型尚未启用，或已停用。可以预览草稿，但它不会进入新讨论。</div>'; return; }
    try {
      const context=await compile([p],previewStage), json=JSON.stringify(context,null,2), bytes=new TextEncoder().encode(JSON.stringify(canonical(context))).length;
      if (id!==renderId || tab!=='preview') return;
      const c=context.profiles[0].content;
      $('#preview-content').innerHTML=`<div class="hint-panel ${previewMode==='draft'?'amber':''}">${previewMode==='draft'?'草稿预览：尚未启用、未发送给模型。':'新讨论可用版本 v'+p.version+'。已有讨论不会自动采用。'} <strong>${stageNames[previewStage]}</strong> · ${bytes.toLocaleString()} 字节（不是 token）</div><article class="preview-paper"><h4>${esc(c.name)}</h4><p class="small-note">${esc(c.summary)}</p>${Object.entries(groups).map(([key,label])=>c[key].length?`<div class="reading-row"><strong>${label}</strong><ul>${c[key].map(item=>`<li>${esc(item.text)} <span class="small-note">· ${importance[item.importance]}</span></li>`).join('')}</ul></div>`:'').join('')}${c.mechanisms.map(m=>`<div class="reading-row"><strong>故事机制 · ${esc(m.name)}</strong>${esc(m.principle)}<ul>${m.costs.map(cost=>`<li>代价：${esc(cost)}</li>`).join('')}${m.choices.map(choice=>`<li>选择：${esc(choice)}</li>`).join('')}</ul></div>`).join('')}</article><details class="technical"><summary>查看编译后的模型数据</summary><p class="field-hint">确定性阶段投影，无管理信息和 H5 映射；未发生模型调用。</p><pre>${esc(json)}</pre><button class="button secondary" data-action="copy-context">复制结构数据</button></details><details class="technical"><summary>完整资料与来源</summary><p class="field-hint">完整内容 SHA-256：${context.profiles[0].source_sha256}</p><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`;
    } catch(error) { if(id===renderId && $('#preview-content')) $('#preview-content').innerHTML=`<div class="hint-panel amber">${esc(error.message)}</div>`; }
  }
  function pathSet(path,value) { const parts=path.split('.'); const key=parts.pop();let object=draft;parts.forEach(k=>object=object[k]);object[key]=value;dirty=true;renderStatus(); }
  function pathGet(path) { return path.split('.').reduce((o,k)=>o[k],draft); }
  function canLeave() { return !dirty || window.confirm('当前草稿尚未保存。放弃本次修改并继续？取消后可点击“保存草稿”。'); }
  function choose(entry) { if(!canLeave())return;current=entry;draft=clone(current.saved);dirty=false;tab='core';previewMode='active';document.body.classList.add('detail');render();$('#panel').scrollTop=0; }
  function validate() {
    if(!draft.name.trim())return '请填写类型名称。';
    if(!draft.summary.trim())return '请填写一句话说明。';
    if(![...Object.keys(groups),'mechanisms'].some(key=>draft[key].length))return '请至少添加一条创作说明或故事机制。';
    for(const key of Object.keys(groups))for(const item of draft[key])if(!item.text.trim()||!item.stages.length)return `${groups[key]}需要内容和至少一个适用阶段。`;
    for(const m of draft.mechanisms)if(!m.name.trim()||!m.principle.trim()||!m.stages.length)return '每个机制需要名称、核心作用和至少一个适用阶段。';
    return null;
  }
  function save(activate=false) {
    if(activate){const error=validate();if(error){toast(error);return;}draft.version=(current.active?.version||0)+1;current.active=clone(draft);current.history.push(clone(draft));current.status='enabled';}
    current.saved=clone(draft);current.updated=new Date().toISOString();dirty=false;
    const stored=persist();render();if(stored)toast(activate?`已在原型中启用 v${draft.version}。旧讨论不会自动改变。`:'草稿已保存。当前启用版本保持原样。');
  }
  function create(copy=false) {
    if(!canLeave())return;const p=copy?clone(draft):blank();p.profile_id=uid('ct_type');p.version=1;if(copy)p.name+= ' · 副本';
    current={saved:p,active:null,status:'draft',origin:'custom',updated:new Date().toISOString(),history:[]};entries.unshift(current);draft=clone(p);dirty=false;tab='core';$('#status-filter').value='all';$('#dimension-filter').value='all';$('#search').value='';persist();document.body.classList.add('detail');render();$('#panel input').focus();
  }
  function renderDialog() {
    const enabled=entries.filter(e=>e.status==='enabled');
    for(const id of selected.keys())if(!enabled.some(e=>e.active.profile_id===id))selected.delete(id);
    const chosen=enabled.filter(e=>selected.has(e.active.profile_id));
    const choiceGroups=Object.entries(dimensions).map(([key,label])=>{const list=enabled.filter(e=>e.active.dimension===key);return list.length?`<section class="choice-group"><h3>${label}</h3><div class="choice-chips">${list.map(e=>`<button data-action="toggle-choice" data-id="${e.active.profile_id}" aria-pressed="${selected.has(e.active.profile_id)}">${esc(e.active.name)}</button>`).join('')}</div></section>`:'';}).join('');
    $('#brainstorm-content').innerHTML=`<div class="choice-groups">${choiceGroups||'<p class="empty">请先启用一个类型。</p>'}</div><p class="small-note">女频主题是可修改的创作起点，不限定读者的性别或偏好。选择顺序不代表主次，也不会强制加入出轨或复合。</p><div class="choice-list">${chosen.map(e=>`<div class="choice-item"><header><b>${esc(e.active.name)} <span class="small-note">v${e.active.version}</span></b><select data-role="${e.active.profile_id}" aria-label="${esc(e.active.name)}在本书的作用">${options(roleNames,selected.get(e.active.profile_id))}</select></header><p>${esc(e.active.summary)}</p>${e.active.publication_mapping.status==='unmapped'?'<p>发行分类待映射；不影响头脑风暴。</p>':''}</div>`).join('')}</div><div class="hint-panel"><strong>${chosen.length} 个已选类型。</strong> 正式讨论将冻结这些版本及完整资料。模型先结合你的构想提出方案，不会因选择类型而自动生成骨架。</div><details class="technical"><summary>这些类型合在一起，模型会读到什么？</summary><pre id="dialog-context">展开时将显示本地编译结果。</pre></details>`;
    $('[data-action="demo-start"]').disabled=!chosen.length;
    const captured=JSON.stringify([...selected]);
    compile(chosen.map(e=>e.active),'brainstorm',new Map(selected)).then(c=>{if(captured===JSON.stringify([...selected])&&$('#dialog-context'))$('#dialog-context').textContent=JSON.stringify(c,null,2);}).catch(e=>toast(e.message));
  }
  document.addEventListener('input',event=>{
    const target=event.target;
    if(target.id==='search'){renderList();return;}
    if(target.dataset.field){let value=target.value;if(target.dataset.lines)value=value.split('\n').map(s=>s.trim()).filter(Boolean);if(target.dataset.comma)value=value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);pathSet(target.dataset.field,value);}
  });
  document.addEventListener('change',event=>{
    const target=event.target;
    if(target.id==='status-filter'||target.id==='dimension-filter')renderList();
    if(target.id==='preview-stage'){previewStage=target.value;render();}
    if(target.dataset.role){selected.set(target.dataset.role,target.value);renderDialog();}
    if(target.dataset.stages){const list=pathGet(target.dataset.stages);pathSet(target.dataset.stages,target.checked?[...list,target.value]:list.filter(s=>s!==target.value));const detail=target.closest('details');detail.querySelector('summary').textContent='适用阶段 · '+(pathGet(target.dataset.stages).map(s=>stageNames[s]).join('、')||'请至少选择一个');}
    if(target.dataset.mapping){const id=target.dataset.mapping,key=primaryIds.has(id)?'primary_genre_ids':'facet_ids',mapping=draft.publication_mapping;mapping[key]=target.checked?[...mapping[key],id]:mapping[key].filter(item=>item!==id);mapping.status=mapping.primary_genre_ids.length+mapping.facet_ids.length?'mapped':'unmapped';const badge=$('#panel .mapping-row .state');if(badge){badge.textContent=mapping.status==='mapped'?'已关联候选':'待映射';badge.className=`state ${mapping.status==='mapped'?'enabled':'draft'}`;}dirty=true;renderStatus();}
  });
  document.addEventListener('click',async event=>{
    const element=event.target.closest('button');if(!element)return;
    if(element.dataset.tab){tab=element.dataset.tab;render();$('#panel').scrollTop=0;return;}
    const {action,key,index,id}=element.dataset;
    if(action==='select')choose(entries.find(e=>e.saved.profile_id===id));
    if(action==='new')create(); if(action==='duplicate')create(true);
    if(action==='save')save(); if(action==='activate')save(true);
    if(action==='back')document.body.classList.remove('detail');
    if(action==='disable'){if(!canLeave())return;current.status='disabled';draft=clone(current.saved);dirty=false;persist();render();toast('已停用。新讨论不再展示该类型，历史版本仍保留。');}
    if(action==='reset'&&confirm('重置本页演示数据？不会影响真实系统。')){try{localStorage.removeItem(KEY);}catch{}entries=seed();current=entries[0];draft=clone(current.saved);dirty=false;tab='core';$('#search').value='';$('#status-filter').value='all';$('#dimension-filter').value='all';selected.clear();selected.set('ct_marriage_betrayal','primary');selected.set('ct_female_awakening','supporting');render();toast('演示资料已重置。');}
    if(action==='add-row'){draft[key].push({id:uid('guide'),text:'',importance:'recommended',stages:[...stages]});dirty=true;render();$(`[data-field="${key}.${draft[key].length-1}.text"]`).focus();}
    if(action==='remove-row'){draft[key].splice(Number(index),1);dirty=true;render();}
    if(action==='add-mechanism'){draft.mechanisms.push({id:uid('mechanism'),name:'',principle:'',importance:'recommended',stages:[...stages],trigger:[],scope:[],limits:[],exceptions:[],costs:[],choices:[],payoffs:[]});dirty=true;render();const last=$('#panel').querySelectorAll('.mechanism-card');last[last.length-1].open=true;last[last.length-1].querySelector('input').focus();}
    if(action==='add-combination'){const other=entries.find(e=>e!==current);if(other){draft.combinations.push({with_profile_id:other.saved.profile_id,opportunities:[],tensions:[]});dirty=true;render();}}
    if(action==='preview-active'||action==='preview-draft'){previewMode=action==='preview-active'?'active':'draft';render();}
    if(action==='restore'){if(!canLeave())return;draft=clone(current.history.find(p=>p.version===Number(element.dataset.version)));dirty=true;previewMode='draft';render();toast('历史内容已复制为草稿。保存并启用时将生成新版本。');}
    if(action==='brainstorm'){renderDialog();$('#brainstorm-dialog').showModal();}
    if(action==='close-dialog')$('#brainstorm-dialog').close();
    if(action==='toggle-choice'){selected.has(id)?selected.delete(id):selected.set(id,'unspecified');renderDialog();}
    if(action==='demo-start'){toast('选择流程已演示完成。原型不会创建任务或调用模型。');}
    if(action==='copy-context'){try{await navigator.clipboard.writeText($('#preview-content .technical pre').textContent);toast('已复制预览数据。');}catch{toast('当前浏览器不允许自动复制，请展开数据后手动复制。');}}
  });
  $('.tabs').addEventListener('keydown',event=>{const keys=['ArrowLeft','ArrowRight','Home','End'];if(!keys.includes(event.key))return;event.preventDefault();const buttons=[...document.querySelectorAll('[data-tab]')],i=buttons.indexOf(document.activeElement);const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;tab=buttons[next].dataset.tab;render();buttons[next].focus();});
  addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  $('#dimension-filter').innerHTML='<option value="all">全部维度</option>'+options(dimensions,'');
  render();
})();
