/* Isolated design prototype. No application API, authentication or model calls. */
const STORAGE_KEY = 'novel-os-cover-studio-design-v2';
const concepts = [
  {id:1,name:'爱与边界',label:'关系三角',recipe:'电影摄影 · 深蓝 / 琥珀',colors:['#162d39','#b79c72','#754a51'],desc:'她站到人生前景，丈夫与女儿仍在身后。亲近的距离，无法替她作出的选择。',plan:'女主成为视觉中心。丈夫与成年女儿分列后景，以视线和距离构成家庭牵引；同一空间、统一光线。',advice:'主角清楚，三人关系在缩略图中仍可辨。可试着减弱后景窗光，让第一眼更集中。',font:'classic'},
  {id:2,name:'这一次，她先离席',label:'戏剧场景',recipe:'商业摄影 · 奶油 / 玫瑰',colors:['#e6d5ba','#a07470','#25354e'],desc:'习惯为家人留下的她，这次转向门外。留下的两个人，让离开有了重量。',plan:'用起身与坐姿形成主次：女主转向门外，家人目光留在她身上。场景服务故事承诺，不逐幕复刻。',advice:'人物反应形成故事张力。桌面细节稍多，可在精修时降低花束与餐具的亮度。',font:'modern'},
  {id:3,name:'牵挂在身后',label:'空间叙事',recipe:'情绪摄影 · 湖蓝 / 微金',colors:['#193d49','#aebec3','#d8bb80'],desc:'前景是她要去的生活，后景是她仍然爱的人。用空间讲述选择与牵挂。',plan:'近景女主与中景家人组成一幅连续画面。湖面、家门与光线连接两端，不切割成头像拼图。',advice:'新生活与家庭的空间反差清楚。可在小尺寸下比较配角辨识度，再决定是否拉近。',font:'elegant'},
  {id:4,name:'把未来还给自己',label:'明亮合成',recipe:'商业精修 · 青绿 / 珊瑚',colors:['#1d5661','#da8a78','#ecd0a2'],desc:'一张有主次的关系海报，以明亮色彩表达重新开始，也保留家庭的情感牵连。',plan:'放大女主神态，以两侧配角和向远方延伸的光线形成统一主视觉。允许象征性合成。',advice:'明亮的色彩与清晰的人物层级更偏向自我重启。它比前两案弱化了冲突感，可按目标读者选择。',font:'classic'}
];
const defaults = () => ({schema:2,tab:'gallery',scenario:'ready',selected:null,selectedVersion:null,selectedType:null,types:{},notes:{},refinements:{},recovered:[],history:[],batch:1});
let state;
try { const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)); state=saved?.schema===2?{...defaults(),...saved}:defaults(); } catch { state=defaults(); }
let activeCandidate=1, typeDraft={}, toastTimer, refinePrompt='', previousFocus=null;
const dialog=document.getElementById('editor-dialog');
const $=(query,root=document)=>root.querySelector(query);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icon=name=>`<i data-icon="${name}" aria-hidden="true"></i>`;
const button=(label,action,id='',kind='')=>`<button class="button ${kind}" data-action="${action}" ${id?`data-id="${id}"`:''}>${label}</button>`;
function icons(root=document){root.querySelectorAll('i[data-icon]').forEach(el=>el.style.setProperty('--icon',`url("assets/icons/${el.dataset.icon}.svg")`));}
function save(){try{localStorage.setItem(STORAGE_KEY,JSON.stringify(state));}catch{toast('浏览器未允许本地保存；本次页面内仍可操作。');}}
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),3400);}
function originalType(id){return {font:concepts[id-1].font,color:id===4?'#f6db9c':'#fff4d9',scale:100,bottom:6.5};}
function getType(id){return state.types[id]||originalType(id);}
function isDisplayedSelected(id){return state.selected===id&&JSON.stringify(state.selectedType||originalType(id))===JSON.stringify(getType(id));}
function typeMarkup(id,type=getType(id)){
  return `<div class="cover-type font-${esc(type.font)}" style="--title-color:${esc(type.color)};--title-scale:${Number(type.scale)/100};bottom:${Number(type.bottom)}%"><span class="line-top">After</span><span class="line-years">Thirty Years,</span><span class="line-choose">I Choose</span><span class="line-me">Me</span></div><div class="cover-author">E L E A N O R &nbsp; H A L E</div>`;
}
function artwork(id,{badge='',preview=false,type,extraClass=''}={}){
  return `<div class="artwork variant-${id} ${extraClass}" data-art="${id}"><img src="assets/cover-${id}.png" alt="${esc(concepts[id-1].name)}：原创成熟女性家庭小说封面画稿" ${preview?'':'loading="eager"'}>${typeMarkup(id,type)}${badge?`<span class="badge ${isDisplayedSelected(id)?'green':''}">${badge}</span>`:''}${preview?'':`<button class="zoom-button" data-action="preview" data-id="${id}" aria-label="放大候选 ${id}">${icon('eye')}</button>`}</div>`;
}
function availability(id){
  if(state.recovered.includes(id))return 'ready';
  if(state.scenario==='all_failed')return 'failed';
  if(state.scenario==='failed'&&id===2)return 'failed';
  if(state.scenario==='partial'&&id>2)return id===3?'generating':'queued';
  return 'ready';
}
function record(text){state.history.unshift({text,time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})});save();}
function render(){
  const hash=location.hash.slice(1); if(['plan','gallery'].includes(hash))state.tab=hash;
  $('#scenario').value=state.scenario;
  document.querySelectorAll('[data-tab]').forEach(el=>{const active=el.dataset.tab===state.tab;el.setAttribute('aria-selected',String(active));el.tabIndex=active?0:-1;});
  $('#panel-plan').hidden=state.tab!=='plan';$('#panel-gallery').hidden=state.tab!=='gallery';
  const ready=concepts.filter(c=>availability(c.id)==='ready').length;$('#gallery-count').textContent=`${ready}/4`;
  renderGallery();renderPlan();icons();save();
}
function renderGallery(){
  const count=concepts.filter(c=>availability(c.id)==='ready').length;
  const progress=state.scenario==='partial'&&count<4?`<div class="progress-strip"><div class="progress-icon"><div class="loader" style="width:17px;height:17px"></div></div><div class="progress-content"><strong>${count}/4 张已保存 · 候选 03 生成中</strong><p>已完成的封面可以直接使用。离开或刷新页面，任务状态会保留。 · 演示状态</p></div><span class="badge violet">后台运行中</span></div>`:count<4?`<div class="progress-strip failed"><div class="progress-icon">${icon('circle-alert')}</div><div class="progress-content"><strong>${count===0?'本批 4 张均生成失败':`${count} 张已完成 · ${4-count} 张生成失败`}</strong><p>${count===0?'方案和失败记录已保存，可按原方案重新生成。':'已完成图片保留。你可以单独重试失败图片，也可以选择已有封面。'}</p></div><span class="badge amber">可手动重试</span></div>`:'';
  $('#panel-gallery').innerHTML=`${progress}<div class="gallery-heading"><div><h2>找到属于这个故事的封面 <span class="count-label">${count} / 4</span></h2><p>${state.selected?`已选用候选 ${String(state.selected).padStart(2,'0')} · ${esc(state.selectedVersion)} · 新的尝试不会覆盖这张封面`:'从一眼心动，到值得细看的故事。先选择，再按你的想法精修。'}</p></div><div class="heading-actions"><button class="text-button" data-action="versions">第 ${state.batch} 批候选 ${icon('chevron-right')}</button>${button(icon('plus')+'重新生成 4 张','generate','','small')}</div></div><div class="gallery">${concepts.map(renderCandidate).join('')}</div><p class="gallery-note">${icon('circle-check')}完整图片可直接选用，质量建议仅供参考；不用填写接受理由。</p>`;
}
function renderCandidate(c){
  const status=availability(c.id),selected=isDisplayedSelected(c.id),refine=state.refinements[c.id];
  if(status!=='ready')return `<article class="candidate">${`<div class="artwork empty-art"><div>${status==='failed'?icon('circle-alert'):status==='generating'?'<div class="loader"></div>':icon('image')}<strong>${status==='failed'?'这张暂未生成成功':status==='generating'?'正在生成这张封面':'等待生成'}</strong><p>${status==='failed'?'已保留美术方向，无需重新规划':status==='generating'?'图片保存后即可查看与选择':'同一批次中的其他图片正在处理'}</p>${status==='failed'?button('重试这张','retry-image',c.id,'small'):'<span class="badge">演示中</span>'}</div></div>`}<div class="candidate-copy"><div class="candidate-overline">候选 / 0${c.id}<span>${c.label}</span></div><h3>${c.name}</h3><p class="candidate-desc">${c.desc}</p><button class="text-button" data-action="direction" data-id="${c.id}">查看已保存方向 ${icon('chevron-right')}</button></div></article>`;
  return `<article class="candidate ${selected?'is-selected':''}">${artwork(c.id,{badge:selected?icon('circle-check')+'已选用':'候选 0'+c.id})}<div class="candidate-copy"><div class="candidate-overline">${c.label}<span>${state.types[c.id]?'排版 V2':'原始版本'}</span></div><h3>${c.name}</h3><p class="candidate-desc">${c.desc}</p><div class="candidate-actions">${button(icon(selected?'circle-check':'plus')+(selected?'当前封面':'使用这张'),'select',c.id,selected?'chosen':'primary')}${button(icon('sparkles')+'精修这张','refine',c.id)}</div><div class="candidate-secondary"><button class="text-button" data-action="typography" data-id="${c.id}">${icon('pen-line')}调整标题</button><button class="text-button" data-action="candidate-history" data-id="${c.id}">${refine?'查看精修版本':'版本记录'} ${icon('chevron-right')}</button></div>${refine?.status==='failed'?`<div class="retry-panel" style="margin-top:13px"><strong>精修未完成 · 原图已保留</strong><p>可保留上次要求重新尝试。</p>${button('重试精修','refine',c.id,'small')}</div>`:''}<details class="advice ${c.id===1?'good':''}"><summary>${c.id===1?'人物关系与主次清晰':'查看构图与排版建议'}</summary><p>${c.advice}</p><p>演示建议 · 不影响选择与交付</p></details></div></article>`;
}
function composition(id){
  const positions={1:[[70,53,24],[37,39,16],[101,41,16]],2:[[70,46,21],[36,72,16],[107,54,16]],3:[[95,55,26],[32,68,13],[53,64,13]],4:[[70,47,25],[34,39,17],[108,47,17]]}[id];
  return `<svg viewBox="0 0 140 165" role="img" aria-label="${concepts[id-1].label}构图示意"><rect x="13" y="3" width="114" height="159" rx="4" fill="#fbfcff" stroke="#c9d2e7"/><path d="M18 105 Q70 ${id===3?25:78} 123 105" fill="none" stroke="#dde2ef" stroke-width="1"/>${[...positions].reverse().map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="${p[2]*.49}" fill="${i===2?'#8c88d4':'#c8d0e7'}"/><path d="M${p[0]-p[2]} ${p[1]+p[2]*1.6} Q${p[0]-p[2]} ${p[1]+p[2]*.6} ${p[0]} ${p[1]+p[2]*.65} Q${p[0]+p[2]} ${p[1]+p[2]*.6} ${p[0]+p[2]} ${p[1]+p[2]*1.6} Z" fill="${i===2?'#aaa6e6':'#dbe1ef'}"/>`).join('')}<rect x="28" y="115" width="84" height="3" rx="1.5" fill="#dddff0"/><rect x="23" y="123" width="94" height="6" rx="1.5" fill="#9f9bbd"/><rect x="37" y="135" width="65" height="8" rx="1.5" fill="#817c9f"/><circle cx="${positions[0][0]}" cy="${positions[0][1]}" r="${positions[0][2]*.7}" fill="none" stroke="#817bdd" stroke-dasharray="2 3"/></svg>`;
}
function renderPlan(){
  $('#panel-plan').innerHTML=`<div class="story-brief"><div><div class="brief-label">这本书的封面，要让读者先感受到什么</div><p class="brief-statement">她依然爱着家人。<br>只是这一次，<strong>她要把人生留给自己。</strong></p><div class="brief-tags"><span class="badge">30 年婚姻</span><span class="badge">家庭牵引</span><span class="badge">成熟女性的自主选择</span></div></div><div class="cast-relations"><div class="cast-item"><div class="cast-avatar">E</div><strong>Evelyn · 女主</strong><small>58 岁 / 重新选择</small></div><span class="relation-line"></span><div class="cast-item"><div class="cast-avatar">D</div><strong>Daniel · 丈夫</strong><small>61 岁 / 习惯依赖</small></div><div class="cast-item"><div class="cast-avatar">S</div><strong>Sophie · 女儿</strong><small>30 岁 / 情感牵连</small></div></div></div><div class="section-heading"><div><h2>同一个故事，四种视觉表达</h2><p>角色关系和故事核心不变，构图、光线与宣传场景可以艺术化设计。</p></div><button class="text-button" data-action="source">查看设计偏好 ${icon('chevron-right')}</button></div><div class="direction-grid">${concepts.map(c=>`<article class="direction-card"><div class="composition">${composition(c.id)}<span class="composition-label">构图示意 · 非生成图片</span></div><div class="direction-body"><div class="direction-number">DIRECTION / 0${c.id}</div><h3>${c.name}</h3><p>${esc(state.notes[c.id]||c.plan)}</p><div class="recipe"><span>${c.recipe}</span><span class="swatches">${c.colors.map(color=>`<b style="background:${color}"></b>`).join('')}</span></div><button class="text-button" data-action="direction" data-id="${c.id}">${icon('pencil')}调整这个方向</button></div></article>`).join('')}</div><div class="plan-footer"><div><p>以人物关系承载故事，以构图和标题形成吸引力。</p><small>完整骨架已用于联合设计。生成后逐张展示，可直接选择或继续精修。</small></div>${button(icon('sparkles')+'按此方案生成 4 张','generate','','primary')}</div>`;
}
function changeTab(tab){state.tab=tab;location.hash=tab;save();render();}
function openModal(title,subtitle,content,{width=1000,bodyClass=''}={}){
  previousFocus=document.activeElement;
  dialog.style.width=`${width}px`;$('#dialog-body').innerHTML=`<header class="dialog-header"><div><h2 id="dialog-title">${title}</h2><p>${subtitle}</p></div><button class="close-button" data-action="close" aria-label="关闭对话框">×</button></header><div class="${bodyClass}">${content}</div>`;
  dialog.setAttribute('aria-labelledby','dialog-title');icons(dialog);if(!dialog.open)dialog.showModal();
}
function closeModal(){dialog.close();if(previousFocus?.isConnected)previousFocus.focus();}
function openRefine(id){
  activeCandidate=id;const previous=state.refinements[id];refinePrompt=previous?.prompt||'保留三位人物的年龄与身份，让女主的神态更坚定。稍微弱化背景，突出她与家人的情感距离。';
  const failed=previous?.status==='failed';
  openModal(`精修候选 0${id}`,`保留喜欢的部分，再向你的想法靠近一点。`, `<div class="dialog-layout"><div class="preview-pane">${artwork(id,{preview:true})}<div class="preview-caption"><span class="badge">参考画面 · 当前预览</span>原图与已选封面会一直保留</div></div><div class="controls-pane">${failed?`<div class="retry-panel"><strong>上次精修未完成</strong><p>演示错误：参考图读取失败。优化要求已保留，可重新尝试。</p></div>`:''}<div><label class="field-label" for="refine-prompt">你想优化哪里？</label><textarea id="refine-prompt">${esc(refinePrompt)}</textarea><div class="prompt-chips"><button data-prompt="让女主的神态更坚定，保留人物年龄。">神态更坚定</button><button data-prompt="弱化环境细节，让三个人物关系更突出。">关系更突出</button><button data-prompt="光线更明亮，保留当前构图和人物。">光线更明亮</button></div><p class="field-hint">描述你希望改变的部分。只调整文字时，可以使用「标题排版」。</p></div><div><span class="field-label">希望保持</span><p class="field-hint">人物身份、年龄与核心关系始终保持。</p><label class="check-row"><input id="keep-composition" type="checkbox" ${previous?.keepComposition===false?'':'checked'}>整体构图与美术风格</label></div><div class="soft-info">以这张原图作为参考，生成一个独立的新版本。结果不会自动替换当前封面。</div><div class="controls-bottom"><label class="check-row"><input id="simulate-refine-failure" type="checkbox">演示：模拟本次精修失败</label>${button(icon('sparkles')+(failed?'按原要求重试 1 张':'生成 1 张精修版'),'run-refine','','primary')}<small>设计稿演示 · 不会调用图片模型或产生费用</small></div></div></div>`);
}
function runRefine(){
  const prompt=$('#refine-prompt').value.trim();if(!prompt){$('#refine-prompt').focus();toast('请先写下希望优化的内容。');return;}
  const fail=$('#simulate-refine-failure').checked,id=activeCandidate;
  state.refinements[id]={status:fail?'failed':'demo_saved',prompt,keepComposition:$('#keep-composition').checked};record(`候选 0${id}：${fail?'精修失败，保留原图和要求':'已记录精修演示版本'}`);save();render();
  if(fail){openRefine(id);toast('已演示失败状态：原图可用，上次提示可直接重试。');return;}
  openModal('精修版本已保存 · 交互演示','此设计稿使用原图展示版本交互，没有实际生成第二张图片。',`<div class="dialog-layout"><div class="preview-pane">${artwork(id,{preview:true})}<div class="preview-caption"><span class="badge violet">演示版本 · V2</span>图片沿用原图，仅展示交互</div></div><div class="controls-pane"><div><span class="field-label">本次优化要求</span><p class="field-hint" style="font-size:12px">${esc(prompt)}</p></div><div class="soft-info green">原图、其他候选与当前选择都已保留。正式版会在这里展示真实精修结果与原图对比。</div><div class="version-thumb">${artwork(id,{preview:true,type:originalType(id)})}<div><strong>V1 · 原始封面</strong><p>随时可以返回</p></div></div><div class="controls-bottom">${button('选用此演示版本','select-demo-version',id,'primary')}${button('继续调整要求','refine',id)}<small>原型只验证交互，不代表精修质量验收</small></div></div></div>`);
}
function openTypography(id){
  activeCandidate=id;typeDraft={...getType(id)};
  openModal('标题排版',`候选 0${id} · 准确保留书名，单独调整文字的视觉层级。`,`<div class="dialog-layout"><div class="preview-pane"><div id="type-preview" style="width:100%;display:contents">${artwork(id,{preview:true,type:typeDraft})}</div><div class="preview-caption">${button('120 × 180 缩略图','thumbnail','','small')}<span>实时本地预览</span></div></div><div class="controls-pane"><div><span class="field-label">完整书名 · 顺序保持</span><div class="type-line-preview"><span>After Thirty Years,</span><strong>I Choose Me</strong></div></div><div><span class="field-label">标题气质</span><div class="segmented" id="font-options"><button data-font="classic" class="${typeDraft.font==='classic'?'active':''}">经典衬线</button><button data-font="modern" class="${typeDraft.font==='modern'?'active':''}">醒目现代</button><button data-font="elegant" class="${typeDraft.font==='elegant'?'active':''}">柔和斜体</button></div></div><div><label class="field-label" for="title-size">整体字号</label><div class="slider-row"><input id="title-size" type="range" min="85" max="108" value="${typeDraft.scale}"><output id="size-output">${typeDraft.scale}%</output></div></div><div><label class="field-label" for="title-position">距底部留白</label><div class="slider-row"><input id="title-position" type="range" min="4" max="12" step="0.5" value="${typeDraft.bottom}"><output id="position-output">${typeDraft.bottom}%</output></div></div><div><span class="field-label">标题颜色</span><div class="color-options">${[['#fff4d9','暖白'],['#f6db9c','香槟金'],['#ffffff','纯白'],['#cae5e6','浅湖蓝']].map(([color,label])=>`<button data-color="${color}" style="background:${color}" class="${typeDraft.color===color?'active':''}" aria-label="${label}" title="${label}"></button>`).join('')}</div></div><div class="soft-info">只改变标题，不重新生成画面。保存为新的排版版本，选用后才更新作品封面。</div><div class="controls-bottom">${button('保存排版新版本','save-type',id,'primary')}<small>本地排版 · 0 次图片模型调用</small></div></div></div>`);
}
function updateType(){
  const p=$('#type-preview .artwork');if(!p)return;
  p.querySelectorAll('.cover-type,.cover-author').forEach(el=>el.remove());p.insertAdjacentHTML('beforeend',typeMarkup(activeCandidate,typeDraft));
  $('#size-output').textContent=`${typeDraft.scale}%`;$('#position-output').textContent=`${typeDraft.bottom}%`;
  dialog.querySelectorAll('[data-font]').forEach(b=>b.classList.toggle('active',b.dataset.font===typeDraft.font));dialog.querySelectorAll('[data-color]').forEach(b=>b.classList.toggle('active',b.dataset.color===typeDraft.color));
}
function selectCover(id,version){
  if(availability(id)!=='ready'){toast('这张图片尚未保存完成。');return;}
  state.selected=id;state.selectedType={...getType(id)};state.selectedVersion=version||(state.types[id]?'排版 V2':'原图 V1');record(`选用候选 0${id} · ${state.selectedVersion}`);save();render();toast(`已选用候选 0${id}，无需等待质量建议或填写理由。`);
}
function showSource(){openModal('故事来源与设计偏好','设计稿使用虚构示例，以下内容不是正式小说的骨架或事实。',`<div class="drawer-body"><span class="badge violet">虚构示例书稿</span><h3>After Thirty Years, I Choose Me</h3><div class="story-source"><p>Evelyn，58 岁，在三十年的婚姻中，习惯优先照顾丈夫 Daniel（61 岁）和成年女儿 Sophie（30 岁）。当家人再次替她决定未来，她开始明确自己的边界，选择想过的生活。她的自主并不意味着对家人的爱已经消失。</p></div><dl class="info-list"><dt>目标读者</dt><dd>北美市场 · 40–60 岁女性</dd><dt>核心关系</dt><dd>夫妻 / 母女；配角表现牵挂与压力，不暗示情敌关系。</dd><dt>美术偏好</dt><dd>商业故事海报，允许人物造型、光线、站位与象征性场景的艺术化。</dd><dt>保持不变</dt><dd>角色年龄、家庭身份、故事核心与作者明确要求。</dd><dt>读取范围</dt><dd>正式功能将读取本项目完整骨架。默认不扫描章节正文。</dd></dl><div class="soft-info">本次四案专门探索家庭关系构图。正式框架不设人数配额；独处型故事也可以设计单人或环境海报。</div><div class="modal-actions">${button('我知道了','close','','primary')}</div></div>`,{width:660});}
function showDirection(id){activeCandidate=id;const c=concepts[id-1];openModal(`方向 0${id} · ${c.name}`,'局部调整这一个方向，其他方向和已有候选会保留。',`<div class="dialog-layout" style="min-height:470px"><div class="preview-pane"><div class="composition" style="width:100%;height:280px;background:transparent">${composition(id)}</div><div class="preview-caption">构图示意 · 不提前生成图片</div><div class="comparison-legend"><span><b></b>女主</span><span><b style="background:#c8d0e7"></b>关键配角</span><span>下方保留标题层级</span></div></div><div class="controls-pane"><div class="direction-edit-meta"><span class="badge">${c.label}</span><span class="badge">${c.recipe}</span></div><div><label class="field-label" for="direction-note">构图与表达要求</label><textarea id="direction-note" style="min-height:180px">${esc(state.notes[id]||c.plan)}</textarea></div><div class="soft-info">可以调整宣传场景、视线、光线和人物主次。不需要逐项复现某一章发生的动作。</div><div class="controls-bottom">${button('保存这个方向','save-direction',id,'primary')}<small>这里只保存设计稿内容，不会重新调用模型</small></div></div></div>`,{width:900});}
function showCost(){openModal('费用与模型','以下数字仅用于展示统计方式，不是账户账单或本次设计稿的实际费用。',`<div class="drawer-body"><span class="badge violet">示例数据 · 人民币估算</span><table class="cost-table"><thead><tr><th>创作环节</th><th>已报告 Tokens</th><th>估算费用</th></tr></thead><tbody><tr><td>头脑风暴</td><td>82,450</td><td>¥ 4.62</td></tr><tr><td>小说正文</td><td>1,206,780</td><td>¥ 78.20</td></tr><tr><td>封面规划与建议</td><td>23,680</td><td>¥ 1.84</td></tr><tr><td>封面图片 · 4 次</td><td>未返回</td><td>¥ 5.76</td></tr></tbody><tfoot><tr><td>总计</td><td>1,312,910*</td><td>¥ 90.42</td></tr></tfoot></table><p>* 已报告用量合计，不含缺失的图片 tokens。缺失不是 0；输入、缓存和输出明细在正式用量记录中单独保留。</p><h3>当前封面模型</h3><dl class="info-list"><dt>配置的模型</dt><dd>自动选择图片模型</dd><dt>实际返回模型</dt><dd>本示例未提供模型回执，不显示推测的模型名称。</dd><dt>计价方式</dt><dd>可配置模型单价 × 用量 × 汇率；图片按已配置每张单价估算。</dd><dt>示例汇率</dt><dd>1 USD = 7.20 CNY（固定示例，非当前汇率）</dd></dl><div class="soft-info">费用与模型信息按每次调用保留。失败调用已报告的消耗也计入，不因重试清零。</div></div>`,{width:650});}
function showHistory(id){
  if(id){activeCandidate=id;const r=state.refinements[id];openModal(`候选 0${id} · 版本记录`,'原图、精修尝试和排版版本分别保存。',`<div class="drawer-body"><div class="version-thumb">${artwork(id,{preview:true,type:originalType(id)})}<div><strong>原图 V1</strong><p>画稿和原标题已保留</p></div>${button('查看','preview-original',id,'small')}</div>${state.types[id]?`<div class="history-row"><span class="history-index">V2</span><div><strong>标题排版新版本</strong><p>仅调整文字 · 图片模型调用 0 次</p></div><span class="badge violet">已保存</span></div>`:''}${r?`<div class="history-row"><span class="history-index">R1</span><div><strong>${r.status==='failed'?'原图精修失败':'精修版本演示'}</strong><p>${esc(r.prompt)}</p></div><span class="badge ${r.status==='failed'?'red':'violet'}">${r.status==='failed'?'可重试':'演示'}</span></div>`:'<p style="margin:20px 0">还没有精修记录。可以从喜欢的画面开始，继续打磨。</p>'}<div class="modal-actions">${button('继续精修','refine',id,'primary')}</div></div>`,{width:650});return;}
  openModal('版本历史','切换查看历史，不会重新生成或覆盖当前选择。',`<div class="drawer-body">${state.history.length?state.history.slice(0,8).map((h,i)=>`<div class="history-row"><span class="history-index">${String(i+1).padStart(2,'0')}</span><div><strong>${esc(h.text)}</strong><p>设计稿本机记录 · ${esc(h.time)}</p></div></div>`).join(''):'<p>尚无操作记录。试着选择一张封面、调整标题或精修图片。</p>'}<div class="history-row"><span class="history-index">01</span><div><strong>第 1 批 · 四个原创封面示例</strong><p>以同一虚构故事演示四种视觉表达</p></div><span class="badge green">已保存</span></div></div>`,{width:670});
}
function showDelivery(){const id=state.selected;if(!id){toast('先选择一张喜欢的封面，再查看交付内容。');return;}openModal('封面交付包','交付绑定你选用的具体版本，不会自动切换到最新精修尝试。',`<div class="drawer-body"><div class="delivery-preview">${artwork(id,{preview:true,type:state.selectedType||originalType(id)})}<div><h3>候选 0${id} · ${esc(state.selectedVersion)}</h3><ul class="delivery-list"><li>完整封面 PNG / JPG</li><li>无字画稿</li><li>标题排版配方</li><li>来源、版本与模型记录</li></ul></div></div><div class="soft-info">正式功能会生成真正的 ZIP 并校验文件清单与哈希。本设计稿可下载演示清单，不会伪装成正式 ZIP。</div><div class="modal-actions">${button('下载演示清单 JSON','download-manifest','','primary')}</div></div>`,{width:660});}
function preview(id){activeCandidate=id;openModal(`候选 0${id} · ${concepts[id-1].name}`,'完整封面与 120 × 180 缩略图，放在一起比较。',`<div class="full-preview"><div>${artwork(id,{preview:true})}</div><aside><h3>${concepts[id-1].label}</h3><p>${concepts[id-1].desc}</p>${button(state.selected===id?'当前封面':'使用这张','select',id,'primary')}${button('精修这张','refine',id)}${button('标题排版','typography',id)}<div class="tiny-cover">${artwork(id,{preview:true})}</div><div class="preview-caption">120 × 180 缩略图</div></aside></div>`,{width:840});}
function showGenerate(){
  if(state.generationEnd||(state.scenario==='partial'&&state.recovered.length<2)){toast('当前批次正在生成；可以先选择已完成图片。');return;}
  state.batch+=1;state.scenario='partial';state.recovered=[];state.generationEnd=Date.now()+6500;
  record(`第 ${state.batch} 批生成演示已开始`);changeTab('gallery');
  toast('已开始生成演示；刷新或切换 Tab 不会重复提交。');
}
document.addEventListener('click',event=>{
  const t=event.target.closest('button,a');if(!t)return;
  if(t.dataset.tab){changeTab(t.dataset.tab);return;}
  if(t.dataset.prompt){$('#refine-prompt').value=t.dataset.prompt;return;}
  if(t.dataset.font){typeDraft.font=t.dataset.font;updateType();return;}
  if(t.dataset.color){typeDraft.color=t.dataset.color;updateType();return;}
  const action=t.dataset.action,id=Number(t.dataset.id)||activeCandidate;
  switch(action){
    case 'close':closeModal();break;
    case 'home':changeTab('gallery');break;
    case 'library':case 'brainstorm':case 'overview':toast('本设计稿聚焦封面工作室。正式版会返回相应页面。');break;
    case 'source':showSource();break;
    case 'select':selectCover(id);break;
    case 'refine':openRefine(id);break;
    case 'run-refine':runRefine();break;
    case 'select-demo-version':selectCover(id,'精修 V2（演示）');closeModal();break;
    case 'typography':openTypography(id);break;
    case 'thumbnail':$('#type-preview .artwork').classList.toggle('thumb-mode');t.textContent=$('#type-preview .artwork').classList.contains('thumb-mode')?'返回完整尺寸':'120 × 180 缩略图';break;
    case 'save-type':state.types[id]={...typeDraft};record(`候选 0${id}：保存排版 V2，未自动选用`);render();closeModal();toast('排版新版本已保存。点击「使用这张」才会更新当前封面。');break;
    case 'history':case 'versions':showHistory();break;
    case 'candidate-history':showHistory(id);break;
    case 'cost':showCost();break;
    case 'direction':showDirection(id);break;
    case 'save-direction':{const value=$('#direction-note').value.trim();if(!value){toast('请保留明确的构图要求。');return;}state.notes[id]=value;record(`已调整方向 0${id}`);render();closeModal();toast('方向已保存，现有图片和其他方向保持。');break;}
    case 'preview':preview(id);break;
    case 'preview-original':openModal(`候选 0${id} · 原图 V1`,'原始画稿和原标题均保留，不受新排版版本影响。',`<div class="preview-pane">${artwork(id,{preview:true,type:originalType(id)})}${button('返回版本记录','candidate-history',id,'small')}</div>`,{width:580});break;
    case 'delivery':showDelivery();break;
    case 'generate':showGenerate();break;
    case 'simulate-generate':state.batch+=1;state.scenario='partial';state.recovered=[];state.generationEnd=Date.now()+6500;record(`第 ${state.batch} 批生成演示已开始`);closeModal();changeTab('gallery');toast('正在演示异步生成；可以切换页面或刷新。');break;
    case 'retry-image':state.recovered.push(id);record(`候选 0${id}：手动重试演示，已恢复图片`);render();toast(`候选 0${id} 已恢复，可直接使用。`);break;
    case 'download-manifest':{const blob=new Blob([JSON.stringify({kind:'design-demo-only',title:'After Thirty Years, I Choose Me',selected_candidate:state.selected,selected_version:state.selectedVersion,production:false},null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cover-design-demo-manifest.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),500);toast('已下载明确标注为演示的 JSON 清单。');break;}
    case 'reset':state=defaults();record('已重置交互演示');location.hash='gallery';if(dialog.open)closeModal();render();toast('已恢复初始设计稿。');break;
  }
});
document.addEventListener('input',event=>{if(event.target.id==='title-size'){typeDraft.scale=Number(event.target.value);updateType();}if(event.target.id==='title-position'){typeDraft.bottom=Number(event.target.value);updateType();}});
$('#scenario').addEventListener('change',event=>{state.scenario=event.target.value;state.recovered=[];delete state.generationEnd;render();toast('已切换演示状态；已选封面记录保留。');});
document.querySelector('.tabs').addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const tab=event.key==='Home'?'plan':event.key==='End'?'gallery':state.tab==='plan'?'gallery':'plan';changeTab(tab);$(`#tab-${tab}`).focus();}});
dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeModal();}});
dialog.addEventListener('close',()=>{if(previousFocus?.isConnected)previousFocus.focus();});
window.addEventListener('hashchange',render);
setInterval(()=>{if(state.generationEnd&&Date.now()>=state.generationEnd){delete state.generationEnd;state.scenario='ready';record(`第 ${state.batch} 批生成演示已完成`);render();toast('4 张示例图已保存，可以直接选择。');}},500);
render();
