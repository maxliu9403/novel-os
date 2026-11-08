/* Isolated interaction prototype. No application endpoints or model calls. */
const $ = (s, root = document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  pen:'<path d="m15 4 5 5M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z"/>',
  layers:'<path d="m12 3 10 6-10 6L2 9zM3 13l9 5 9-5M3 17l9 5 9-5"/>',
  book:'<path d="M3 4h6c2 0 3 1 3 2 0-1 1-2 3-2h6v16h-6c-2 0-3 1-3 1s-1-1-3-1H3zM12 6v15"/>',
  arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
  folder:'<path d="M3 7V5a1 1 0 0 1 1-1h5l3 3h8a1 1 0 0 1 1 1v11H3z"/>',
  search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  file:'<path d="M5 3h9l5 5v13H5zM14 3v6h5M8 13h8m-8 4h6"/>',
};
const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.file}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
const STORE = 'novelos.genre-tag-prototype.v1';
const initial = () => ({
  items: structuredClone(window.KNOWLEDGE_SEED),
  idea:'一位与丈夫共同经营小旅馆的女性，发现他为了扩张事业，背着她出售了两人承诺保留的旧宅。分开一年后，一场洪水让他们不得不共同安置旅客。我想写她重新掌握自己的人生，也想探讨：真正改变过的人，能否值得再爱一次？',
  genre:'genre.romance', tags:['tag.betrayal','tag.second_chance','tag.independent'],
  language:'英文', chapterWords:1100, chapters:16, audience:'北美 · 成熟女性',
  source:false, mode:'reference', sourceReady:false, sourceRights:false, coverReference:true,
  sourceScope:'保留旅馆这一背景；人物、因果与结局重新设计。',
  tonePriority:'', frozen:null, route:null, adopted:false, supplement:'',
});
let state;
try { const cached = JSON.parse(localStorage.getItem(STORE)); state = cached?.items && cached.items.some(i=>i.id==='genre.romance') ? cached : initial(); } catch { state = initial(); }
let step = 1, filter = '全部', tagQuery = '', libraryKind = 'genre', libraryQuery = '', selectedId = 'genre.romance', editorMode = 'edit', editorDraft = null;
let routeName = '', dialogReturnFocus = null, toastTimer;
const item = id => state.items.find(i => i.id === id);
const enabled = kind => state.items.filter(i=>i.kind===kind && i.status==='enabled');
const currentTags = () => state.tags.map(item).filter(Boolean);
const availableTags = () => enabled('tag').filter(i=>i.genres.includes(state.genre));
function persist() { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch { /* Private mode still supports this live session. */ } }
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').classList.add('show'); toastTimer = setTimeout(()=>$('#toast').classList.remove('show'),3200); }
function badge(text, cls='') { return `<span class="badge ${cls}">${esc(text)}</span>`; }
function button(text, action, cls='', extra='') { return `<button class="button ${cls}" data-action="${action}" ${extra}>${text}</button>`; }
function chip(i, removable=false) { return `<span class="chip ${i.kind==='genre'?'primary':''}">${esc(i.name)}${removable?`<button data-action="tag" data-id="${esc(i.id)}" aria-label="移除${esc(i.name)}">×</button>`:''}</span>`; }
function head(eyebrow,title,subtitle,actions='') { return `<header class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="row">${actions}</div></header>`; }
function cover() { return '<div class="mini-cover" aria-label="示意封面">AFTER<br>THE RAIN<small>REFERENCE DEMO</small></div>'; }
function markdown(text) {
  return esc(text).split(/\n\s*\n/).map(block=> {
    if (/^## /.test(block)) return `<h2>${block.slice(3)}</h2>`;
    if (/^- /.test(block)) return `<ul>${block.split('\n').map(l=>`<li>${l.replace(/^- /,'')}</li>`).join('')}</ul>`;
    return `<p>${block.replace(/\n/g,'<br>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>')}</p>`;
  }).join('');
}
function fullMarkdown(i) {
  return `---\nschema: novel-knowledge.v1\nid: ${i.id}\nkind: ${i.kind}\nname: ${JSON.stringify(i.name)}\naliases: [${JSON.stringify(i.en)}]\n${i.kind==='tag'?`genre_ids: [${i.genres.join(', ')}]\n`:''}---\n\n${i.body}\n`;
}
function openDialog(title, content, actions='') {
  dialogReturnFocus = document.activeElement;
  const d=$('#dialog');
  d.innerHTML=`<div class="dialog-head"><h2 id="dialog-title">${title}</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭弹窗">${icon('close')}</button></div><div class="dialog-content">${content}</div>${actions?`<div class="dialog-actions">${actions}</div>`:''}`;
  if(!d.open)d.showModal();
}
function closeDialog() { $('#dialog').close(); if(dialogReturnFocus?.isConnected)dialogReturnFocus.focus(); }
$('#dialog').addEventListener('click',e=>{if(e.target===$('#dialog')){const r=e.target.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog();}});
function invalidate() { state.frozen=null; state.route=null; state.adopted=false; state.supplement=''; persist(); }
function canStart() { return state.idea.trim() && item(state.genre)?.status==='enabled' && state.tags.length && state.tags.every(id=>availableTags().some(t=>t.id===id)) && (!state.source || (state.sourceReady && (state.mode==='reference'||state.sourceRights))) && (!hasToneTension() || state.tonePriority); }
function hasToneTension() { return state.tags.includes('tag.sweet_love') && state.tags.includes('tag.dark_romance'); }
function goStep(next) {
  if(next>1 && !state.idea.trim()){toast('先写下你的故事构想'); $('#idea')?.focus(); return;}
  if(next>2 && (!state.genre || !state.tags.length)){toast('请选择一个主类型和至少一个标签'); return;}
  step=next;render();window.scrollTo({top:0,behavior:'instant'});
}
function steps() { return `<div class="stepbar" aria-label="创建步骤">${['构想与来源','题材与标签','确认创作方向'].map((name,n)=>`<button class="step ${step===n+1?'active':step>n+1?'done':''}" data-action="step" data-step="${n+1}" ${step===n+1?'aria-current="step"':''}><span class="num">${step>n+1?'✓':n+1}</span>${name}</button>${n<2?'<span class="step-arrow">›</span>':''}`).join('')}<span class="step-status">草稿仅保存在此浏览器</span></div>`; }
function creation() {
  return head('新小说 · 创作准备','一起把故事想完整','从你的构想出发，让每个选择都进入故事。',button('题材与标签 '+icon('arrow'),'nav-library'))+steps()+`<div class="stage-body">${step===1?ideaView():step===2?tagsView():reviewView()}</div>`+creationFooter();
}
function ideaView() {
  return `<div class="creation-grid"><div><div class="section-top"><label class="input-label" for="idea" style="margin:0">想写一个怎样的故事？</label><button class="text-button purple" data-action="fill-example">填入示例</button></div><textarea class="field idea-field" id="idea" placeholder="写下人物、处境，或一个让你念念不忘的场景。">${esc(state.idea)}</textarea><div class="field-meta"><span>不必先写完整大纲。</span><span id="idea-count">${state.idea.length} 字</span></div><div class="form-grid"><label>正文语言<select class="field" id="language">${['英文','中文'].map(v=>`<option ${v===state.language?'selected':''}>${v}</option>`).join('')}</select></label><label>目标读者<input class="field" id="audience" value="${esc(state.audience)}" placeholder="例如：北美市场，40–60 岁女性"></label><label>计划章节数<input class="field" id="chapters" type="number" min="1" max="511" value="${state.chapters}"></label><label>每章目标 <span class="optional" id="unit-label">${state.language==='英文'?'词':'字'}</span><input class="field" id="chapterWords" type="number" min="100" max="10000" step="100" value="${state.chapterWords}"></label></div><p class="tiny muted" style="margin-top:16px">故事需要更多空间时，讨论增章方案；保留每章目标。</p></div>${sourceBox()}</div>`;
}
function sourceBox() {
  if(!state.source) return `<aside class="source-box"><div class="source-icon">${icon('folder')}</div><h3>也可以从一本参考书开始</h3><p>导入章节、简介与封面，选择参考创作或原作重写。</p>${button(icon('plus')+' 导入参考书','source-import')}<span class="fine">原型使用演示资料；不上传本机文件。</span></aside>`;
  return `<aside class="source-box"><div class="row between"><h3>参考资料</h3><button class="text-button" data-action="source-remove">移除</button></div><div class="source-book">${cover()}<div><b>雨夜之后</b><p style="margin:5px 0">示例资料 · 8 个免费章节</p>${badge(state.sourceReady?'分析结果已载入 · 演示':'待分析','neutral')}</div></div><div class="mode-pills" aria-label="导入模式"><button data-action="source-mode" data-mode="reference" class="${state.mode==='reference'?'active':''}">参考创作</button><button data-action="source-mode" data-mode="rewrite" class="${state.mode==='rewrite'?'active':''}">原作重写</button></div><p>${state.mode==='reference'?'提炼关系与叙事方法，重新设计人物、事件与结局。':'按你的保留范围重新创作；未提供的后续保持未知。'}</p>${state.mode==='rewrite'?`<label class="check-label"><input id="sourceRights" type="checkbox" ${state.sourceRights?'checked':''}>我拥有原作的改编权</label><label class="input-label small" for="sourceScope" style="margin-top:16px">保留与改写范围</label><textarea id="sourceScope" class="field small" rows="3">${esc(state.sourceScope)}</textarea>`:''}<label class="check-label" style="margin-top:15px"><input id="coverReference" type="checkbox" ${state.coverReference?'checked':''}>将参考封面带入封面工作室</label><button class="text-button purple" style="margin-top:12px" data-action="source-detail">查看资料范围 ${icon('arrow')}</button></aside>`;
}
function tagsView() {
  return `<div class="creation-grid"><div><div class="section-top"><h2>主类型 <span class="optional">单选</span></h2><a href="#library" class="small">维护类型 ↗</a></div><div class="genre-grid" role="group" aria-label="选择主类型">${enabled('genre').map(g=>`<button class="genre-card ${state.genre===g.id?'selected':''}" data-action="genre" data-id="${g.id}" aria-pressed="${state.genre===g.id}"><span class="genre-letter">${esc(g.name.slice(0,1))}</span><span><span class="genre-name">${esc(g.name)}<span class="genre-en">${esc(g.en)}</span></span><span class="genre-desc">${esc(g.id==='genre.mystery'?'交互示例 · 展示不同类型的关联标签':g.description)}</span></span><span class="radio"></span></button>`).join('')}</div><div class="tag-section"><div class="section-top"><h2>故事标签 <span class="optional">至少选择一个</span></h2><span class="small muted">${availableTags().length} 个可用</span></div><div class="search-input">${icon('search')}<input id="tag-search" class="field" placeholder="搜索标签或关键词" value="${esc(tagQuery)}" aria-label="搜索故事标签"></div><div class="filter-row" aria-label="标签分组">${['全部',...new Set(availableTags().map(t=>t.group))].map(g=>`<button class="filter ${g===filter?'active':''}" data-action="filter" data-filter="${esc(g)}" aria-pressed="${g===filter}">${esc(g)}</button>`).join('')}</div><div class="tag-grid" id="tag-grid">${tagCards()}</div>${state.genre==='genre.mystery'?'<p class="tiny muted" style="margin-top:16px">悬疑仅用于验证关联筛选交互，本轮语料研究聚焦 Romance。</p>':''}</div></div><aside class="combination" id="combination">${combination()}</aside></div>`;
}
function tagCards() {
  const list=availableTags().filter(t=>(filter==='全部'||t.group===filter)&&`${t.name} ${t.en} ${t.description}`.toLowerCase().includes(tagQuery.toLowerCase()));
  return list.map(t=>`<article class="tag-card ${state.tags.includes(t.id)?'selected':''}"><button class="tag-choice" data-action="tag" data-id="${t.id}" aria-pressed="${state.tags.includes(t.id)}"><div class="tag-name"><span class="check">✓</span>${esc(t.name)}</div><p>${esc(t.description)}</p></button><button class="tag-info" data-action="tag-info" data-id="${t.id}" aria-label="查看${esc(t.name)}的写作说明">${icon('info')}</button></article>`).join('') || '<div class="empty">没有匹配的标签。试试其他关键词。</div>';
}
function combination() {
  return `<div class="combo-top">这本书的创作组合</div><h3>选择要变成故事的动力</h3><div class="selected-genre">${esc(item(state.genre)?.en || '尚未选择类型')}</div><div class="chips">${currentTags().map(t=>chip(t,true)).join('')||'<span class="small muted">选择你最在意的故事元素</span>'}</div><div class="effect-list">${currentTags().map(t=>`<div class="effect"><b>${esc(t.name)}会改变什么</b><p>${esc(t.description)}</p></div>`).join('')}</div>${hasToneTension()?`<div class="note amber">甜蜜与危险可以共存。请先决定整体体验：<div class="row wrap" style="margin-top:9px">${button('甜蜜为主','tone','small',`data-value="sweet" aria-pressed="${state.tonePriority==='sweet'}"`)}${button('暗色为主','tone','small',`data-value="dark" aria-pressed="${state.tonePriority==='dark'}"`)}</div>${state.tonePriority?`<p class="tiny">已选：${state.tonePriority==='sweet'?'甜蜜为主，危险制造阻力':'暗色为主，温柔形成对照'}</p>`:''}</div>`:''}<button class="text-button purple" data-action="prompt">${icon('file')} 查看模型将读取的说明</button>`;
}
function reviewView() {
  return `<div class="review-layout"><div><div class="brief-intro"><div class="row between"><h2>你的故事起点</h2>${badge('待讨论')}</div><p>${esc(state.idea)}</p><div class="chips" style="margin-top:20px">${[item(state.genre),...currentTags()].filter(Boolean).map(i=>chip(i)).join('')}</div></div><div class="section-top" style="margin-top:27px"><h2>这些选择，怎样共同起作用</h2></div><div class="promise-grid">${[item(state.genre),...currentTags()].filter(Boolean).map(t=>`<div class="promise"><b>${esc(t.name)}</b><p>${esc(t.description)}</p></div>`).join('')}</div><div class="note" style="margin-top:18px">下一轮先给出不同的总体走向。你决定主线与结局取舍，具体场景由模型展开。</div><details class="preview-input"><summary>查看组合后的创作输入</summary><pre>${esc(compiledInput())}</pre></details></div><aside><div class="source-summary"><h3>创作设定</h3><div class="kv"><span>正文语言</span><b>${esc(state.language)}</b></div><div class="kv"><span>读者</span><b>${esc(state.audience)||'讨论中完善'}</b></div><div class="kv"><span>初步篇幅</span><b>${state.chapters} 章</b></div><div class="kv"><span>每章目标</span><b>${state.chapterWords.toLocaleString()} ${state.language==='英文'?'词':'字'}</b></div><div class="kv"><span>创作方式</span><b>${state.source?(state.mode==='reference'?'参考创作':'原作重写'):'原创构想'}</b></div><div class="kv"><span>资料版本</span><b>确认时保存快照</b></div></div>${state.source?`<div class="note" style="margin-top:15px">来源仅覆盖 8 个示例章节。${state.coverReference?'参考封面将带入封面工作室。':''}</div>`:''}${!canStart()?'<p class="error">请完成必选项；原作重写需确认改编权，混合情绪需明确主次。</p>':''}</aside></div>`;
}
function creationFooter() {
  return `<footer class="footer"><div class="footer-summary">${step===1?'<span>先有一个起点，就可以开始。</span>':`<b>${esc(item(state.genre)?.en||'选择主类型')}</b><span>＋ ${state.tags.length} 个标签</span>`}</div><div class="row">${step>1?button('上一步','prev'):''}${step<3?button((step===1?'选择题材与标签':'确认创作方向')+' '+icon('arrow'),'next','primary'):button('开始讨论 · 演示 '+icon('arrow'),'start','primary',canStart()?'':'disabled')}</div></footer>`;
}
function storyKnowledge(body) {
  return body.split(/(?=^## )/m).filter(block=>block.split('\n')[0].trim()!=='## 封面表达').join('').trim();
}
function compiledInput() {
  const selected=[item(state.genre),...currentTags()].filter(Boolean);
  return `作者构想\n${state.idea}\n\n创作设定\n${state.language}；${state.audience||'读者待明确'}；${state.chapters} 章，每章目标 ${state.chapterWords} ${state.language==='英文'?'词':'字'}。\n${state.source?`\n来源模式：${state.mode==='reference'?'参考创作，提炼方法，重新构建新故事':'原作重写，按已确认保留范围执行'}。范围仅为所供章节。\n${state.sourceScope}\n`:''}${state.tonePriority?`\n情绪主次：${state.tonePriority==='sweet'?'甜蜜为主、危险制造阻力':'暗色为主、温柔形成对照'}。\n`:''}\n本轮任务\n提出 2–3 个实质不同的总体方案。每个选择须改变人物行动、代价或阅读体验；整合为一条故事因果，不按标签依次堆剧情。大走向交给作者选择，细节由你设计。未确认的假设明确保留为提议。\n\n${selected.map(i=>`=== ${i.name} · ${i.id} · v${i.version} ===\n${storyKnowledge(i.body)}`).join('\n\n')}`;
}
const demoRoutes = [
  {id:'cooperation',name:'从共同承担开始',kicker:'责任与信任',opening:'洪水当晚，她接下原本不该由她独自承担的旅客安置。前夫带着帮忙的条件出现，她拒绝再交出决定权。',choice:'两人必须合作，但她只接受不以复合为条件的帮助。',cost:'他放弃扩张利益并承担赔偿；她也要决定是否让一个变过的人靠近。',reward:'关系在一次次可靠行动中重建，她保有自己的事业。'},
  {id:'competition',name:'先成为平等的对手',kicker:'能力与选择',opening:'重逢时，他们各自经营旅馆。她带着被轻视的小团队，赢下他原本认为稳操胜券的订单。',choice:'从竞争走向合作，关键在于他能否承认她不再是附属。',cost:'他公开承担旧事责任，失去部分生意；她拒绝用爱情交换职业让步。',reward:'读者先看到她赢得能力与尊重，再看到两人自由重新选择。'},
  {id:'distance',name:'隔着距离重新相爱',kicker:'自立与重逢',opening:'她决定离开小镇，在出发前处理旧宅出售的余账。他第一次没有要求她留下。',choice:'先完成各自的人生，再决定是否重新建立关系。',cost:'他接受无法追回的时间；她在独立站稳后面对仍未消失的感情。',reward:'机会来自真正的改变，重新靠近也不取消她已经选择的生活。'},
];
function isDemoCombination() { return state.frozen?.genre==='genre.romance' && ['tag.betrayal','tag.second_chance','tag.independent'].every(id=>state.frozen.tags.includes(id)) && state.frozen.tags.length===3; }
function discussion() {
  if(!state.frozen) return head('头脑风暴','从一个明确的起点开始','先完成构想与题材选择。')+`<div class="empty">${button('返回新建小说','nav-create','primary')}</div>`;
  const frozen=state.frozen;
  return head('头脑风暴 · 总体方案','把这组选择，写成一个故事','示例方案展示交互方式，不是模型实时生成。',button('调整创作设定','edit-selection'))+
    `<div class="timeline">${['总体走向','开篇冲突','中段推进','高潮与结局','骨架交付'].map((v,n)=>`<div class="phase ${n===0?'active':''}">${String(n+1).padStart(2,'0')} ${v}<span>${n===0?(state.adopted?'方案已采用':'本轮待决定'):'尚未展开'}</span></div>`).join('')}</div><div class="story-sofar"><div class="row between"><div class="small">当前故事概要</div>${isDemoCombination()?'<button class="text-button purple" data-action="story-effects">查看标签的故事作用 ↗</button>':''}</div><p>${esc(frozen.idea)}</p><div class="chips" style="margin-top:14px">${frozen.entries.map(i=>chip(i)).join('')}</div></div>`+
    (!isDemoCombination()?`<div class="note amber">你的组合已冻结。任意组合的真实方案将在开发后由模型生成；本原型不把预写故事冒充成你的组合结果。</div><div class="stage-body"><div class="section-top"><h2>已准备好的创作输入</h2>${button('查看完整输入','frozen-prompt','small')}</div><div class="promise-grid">${frozen.entries.map(i=>`<div class="promise"><b>${esc(i.name)}</b><p>${esc(i.description)}</p></div>`).join('')}</div><p class="small muted" style="margin:24px 0">想体验完整的方案选择，可载入“浪漫＋背叛＋第二次机会＋独立自主”的固定交互示例。</p>${button('载入固定交互示例','demo-discussion','primary')}</div>`:
    state.adopted?`<div class="adopted"><div class="row between"><h3>已采用：${esc(demoRoutes.find(r=>r.id===state.route)?.name)}</h3>${badge('模拟提交完成','green')}</div><p>${esc(demoRoutes.find(r=>r.id===state.route)?.choice)}</p>${state.supplement?`<p>你的补充：${esc(state.supplement)}</p>`:''}</div><div class="stage-body"><h2>下一步：开篇冲突</h2><p class="small muted" style="margin-top:10px">真实系统将围绕已采用的主线推进开篇，不再反复询问普通场景细节。</p><div class="row wrap" style="margin-top:25px">${button('返回方案选择','rechoose')}${button('预览 H5 作品展示','nav-publish','primary')}</div></div>`:
    `<div class="section-top"><div><h2>本轮方案与取舍</h2><p class="small muted" style="margin-top:7px">3 个总体走向 · 选择一个，可以补充自己的想法。</p></div>${badge('示例 · 单选')}</div><div class="routes">${demoRoutes.map((r,n)=>`<article class="route ${state.route===r.id?'selected':''}"><div class="route-index">${String(n+1).padStart(2,'0')} / ${r.kicker}</div><h3>${r.name}</h3><p>${r.opening}</p><dl><dt>主要选择</dt><dd>${r.choice}</dd><dt>真正的代价</dt><dd>${r.cost}</dd><dt>读者的回报</dt><dd>${r.reward}</dd></dl>${button(state.route===r.id?icon('check')+' 已选择':'采用这个走向','choose-route',state.route===r.id?'purple':'',`data-id="${r.id}" aria-pressed="${state.route===r.id}"`)}</article>`).join('')}</div><div class="composer"><label class="input-label" for="supplement">补充想法 <span class="optional">选填</span></label><textarea id="supplement" class="field" placeholder="例如：希望她的事业胜利更鲜明；复合之前，先让他学会尊重她的拒绝。">${esc(state.supplement)}</textarea><div class="row between wrap"><span class="small muted">${state.route?'已选择 1 个走向，将与补充一起提交。':'选择一个走向后继续；也可以提出不同的大方向。'}</span><div class="row">${button('我有不同方向','custom-route')}${button('采用并继续 · 演示 '+icon('arrow'),'adopt','primary',state.route?'':'disabled')}</div></div></div>`);
}
function library() {
  if(!item(selectedId)||item(selectedId).status==='deleted')selectedId=state.items.find(i=>i.kind===libraryKind&&i.status!=='deleted')?.id;
  if(!editorDraft||editorDraft.id!==selectedId)editorDraft=selectedId?structuredClone(item(selectedId)):null;
  return head('创作资料库','题材与标签','用一份写作说明，定义一种有辨识度的阅读体验。',button('新建小说 '+icon('arrow'),'nav-create'))+
    `<div class="library-toolbar"><div class="tabs" aria-label="资料类型"><button class="tab ${libraryKind==='genre'?'active':''}" data-action="library-kind" data-kind="genre" aria-pressed="${libraryKind==='genre'}">主类型 <span class="optional">${state.items.filter(i=>i.kind==='genre'&&i.status!=='deleted').length}</span></button><button class="tab ${libraryKind==='tag'?'active':''}" data-action="library-kind" data-kind="tag" aria-pressed="${libraryKind==='tag'}">故事标签 <span class="optional">${state.items.filter(i=>i.kind==='tag'&&i.status!=='deleted').length}</span></button></div>${button(icon('plus')+` 新建${libraryKind==='genre'?'类型':'标签'}`,'new-entry','primary small')}</div><div class="library-grid"><aside class="list-pane"><div class="search-input">${icon('search')}<input id="library-search" class="field" placeholder="搜索名称或关键词" value="${esc(libraryQuery)}" aria-label="搜索知识条目"></div><div class="entry-list" id="entry-list">${libraryList()}</div></aside><section class="editor-pane" id="editor-pane">${editorView()}</section></div>`;
}
function libraryList() {
  const list=state.items.filter(i=>i.kind===libraryKind&&i.status!=='deleted'&&`${i.name} ${i.en} ${i.description}`.toLowerCase().includes(libraryQuery.toLowerCase()));
  return list.map(i=>`<button class="entry ${selectedId===i.id?'active':''}" data-action="entry" data-id="${i.id}" aria-pressed="${selectedId===i.id}"><div class="entry-name">${esc(i.name)}${badge(i.status==='enabled'?'已启用':i.status==='disabled'?'已停用':'草稿',i.status==='enabled'?'neutral':'amber')}</div><p>${esc(i.description)}</p><small>${esc(i.en)} · v${i.version}</small></button>`).join('')||'<div class="empty">没有找到条目</div>';
}
const dirty = () => !!editorDraft && JSON.stringify(editorDraft)!==JSON.stringify(item(editorDraft.id));
function editorView() {
  const i=editorDraft; if(!i)return '<div class="empty">新建一个条目，开始定义创作方法。</div>';
  return `<div class="editor-head"><div><div class="row"><h2>${esc(i.name)}</h2>${badge(i.status==='enabled'?'已启用':i.status==='disabled'?'已停用':'草稿',i.status==='enabled'?'green':'neutral')}</div><p class="muted">${esc(i.origin)} · v${i.version}</p></div><div class="editor-controls"><button class="text-button" data-action="entry-meta">编辑信息</button><button class="text-button" data-action="clone">复制</button><button class="text-button" data-action="toggle-status">${i.status==='enabled'?'停用':'启用'}</button><button class="text-button" data-action="delete" style="color:var(--red)">删除</button></div></div>`+
    (i.kind==='tag'?`<div class="editor-meta"><span class="muted">适用主类型</span>${i.genres.map(id=>`<span class="chip">${esc(item(id)?.name||id)}</span>`).join('')}<button class="text-button purple" data-action="associations">调整关联</button></div>`:`<div class="editor-meta"><span class="muted">关联 ${state.items.filter(t=>t.kind==='tag'&&t.status!=='deleted'&&t.genres.includes(i.id)).length} 个标签</span><button class="text-button purple" data-action="related-tags">查看与管理 ${icon('arrow')}</button></div>`)+
    `${i.savedDraft && !dirty()?`<div class="note" style="margin-bottom:15px">有一份已保存的草稿。 <button class="text-button purple" data-action="load-draft">载入草稿</button></div>`:''}<div class="editor-frame"><div class="editor-tools"><span class="small">写作说明 · Markdown</span><div class="row"><button class="text-button purple" data-action="format-help">格式示例</button><div class="segmented"><button class="${editorMode==='edit'?'active':''}" data-action="editor-mode" data-mode="edit" aria-pressed="${editorMode==='edit'}">编辑</button><button class="${editorMode==='preview'?'active':''}" data-action="editor-mode" data-mode="preview" aria-pressed="${editorMode==='preview'}">预览</button></div></div></div>${editorMode==='edit'?`<textarea class="editor" id="md-editor" spellcheck="false" aria-label="Markdown 写作说明">${esc(i.body)}</textarea>`:`<article class="md-preview">${markdown(i.body)}</article>`}</div><div class="row between" style="margin:12px 0"><span class="tiny muted">启用后供新讨论使用，已开始的讨论保留原版。</span><button class="text-button purple" data-action="full-md">查看完整文件</button></div><div id="editor-footer">${editorFooter()}</div>`;
}
function editorFooter() { return `<footer class="editor-footer ${dirty()?'dirty':''}"><span class="small muted">${dirty()?'有未保存的修改':'当前内容已保存'}</span>${dirty()?`<div class="row">${button('放弃修改','discard','small')}${button('保存草稿','save-draft')}${button('保存并启用','save-enable','primary')}</div>`:''}</footer>`; }
function publication() {
  const selected=state.frozen?.entries || [item(state.genre),...currentTags()].filter(Boolean);
  return head('发行预览','同一套类型与标签，连接创作与阅读','这里展示 H5 升级后的目标效果。',button('调整创作组合','edit-selection'))+
    `<div class="library-toolbar"><div class="tabs"><span class="tab active">读者看到的作品页</span></div>${badge('目标方案 · 尚未接入 H5','neutral')}</div><div class="phone-layout"><div class="publication-summary"><div class="eyebrow">NOVEL OS → H5</div><h2>创作中选的，读者也能找到。</h2><p class="small muted">类型与标签来自作品确认的版本。新标签启用后可随交付包登记到 H5，无需为每个名称改前端代码。</p><div class="chips">${selected.map(i=>chip(i)).join('')}</div><div class="publishing-flow"><b>创作选择</b><span>→</span><b>作品分类快照</b><span>→</span><b>H5 展示与筛选</b></div><table class="taxonomy-table"><thead><tr><th>作品分类</th><th>名称</th><th>来源</th></tr></thead><tbody>${selected.map(i=>`<tr><td>${i.kind==='genre'?'主类型':'标签'}</td><td>${esc(i.name)}</td><td>作品快照 · v${i.version}</td></tr>`).join('')}</tbody></table><div class="note" style="margin-top:24px">写作说明留在创作系统。H5 接收类型、标签与作品的关系，不接收创作提示词或参考小说全文。</div><div class="row wrap" style="margin-top:22px">${button('查看发行数据示例','taxonomy-json') }<a class="text-button purple" href="../../genre-tag-studio-v1/h5-taxonomy-handoff.md" target="_blank" rel="noopener">H5 交接草案 ↗</a></div></div><div class="phone" aria-label="H5 作品页演示"><div class="phone-top"><b>9:41</b><span class="notch"></span><span>● ▰</span></div><div class="phone-nav"><span>‹ 返回</span><b>NOVEL NEST</b><span>···</span></div><div class="phone-book">${cover()}<div><h3>雨停之后，<br>再选择你</h3><div class="phone-meta">示例书名 · 示例作者</div><div class="phone-meta">16 章 · 完结</div></div></div><div class="chips">${selected.map(i=>`<button class="chip ${i.kind==='genre'?'primary':''}" data-action="h5-tag" data-id="${i.id}">${esc(i.name)}</button>`).join('')}</div><h4>作品简介</h4><p>她守了十年的旅馆，是他计划出售的一笔资产。</p><p style="margin-top:11px">分开一年后，一场洪水将他们困在同一屋檐下。旅客需要安置，旧账等待清偿，他说这一次只想帮忙。</p><p style="margin-top:11px">她不再需要一个替她决定人生的人。如果他真的变了，就先学会接受：她随时可以说不。</p><div class="phone-meta" style="margin-top:16px">简介为固定交互示例，未随任意组合自动生成。</div>${button('开始阅读','read-demo','primary')}</div></div>`;
}
function render() {
  const route=location.hash.slice(1).split('?')[0]||'create'; routeName=route;
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===(route==='discussion'?'create':route)));
  $('#main').innerHTML=route==='covers'?'':route==='library'?library():route==='publish'?publication():route==='discussion'?discussion():creation();
  if(route==='covers') {
    const entries=state.frozen?.entries || [item(state.genre),...currentTags()].filter(Boolean);
    const idea=state.frozen?.idea || state.idea;
    const ids=entries.map(i=>i.id);
    window.CoverStudio.mount($('#main'), {
      entries, idea, rewrite:state.source && state.mode==='rewrite',
      demoSupported:idea===initial().idea && ids.length===4 && ['genre.romance','tag.betrayal','tag.second_chance','tag.independent'].every(id=>ids.includes(id))
    }, {dialog:openDialog,toast});
  }
  persist();
}
function dirtyGuard(run) { if(!dirty()){run();return;} openDialog('还没有保存修改','离开前，可以先保存，或放弃这次修改。',button('继续编辑','close-dialog')+button('放弃并继续','confirm-leave','danger')); pendingLeave=run; }
let pendingLeave=null, pendingGenre=null;
function navigate(route) { dirtyGuard(()=>{editorDraft=null;if(location.hash===`#${route}`)render();else location.hash=route;}); }
function validSections(body) {
  const sections=body.split(/^## /m).slice(1).map(block=>({name:block.split('\n')[0].trim(),content:block.slice(block.indexOf('\n')+1).trim()}));
  return ['核心承诺','故事机制','发散空间','组合原则','场景与表达','简介与兑现'].every(name=>sections.filter(s=>s.name===name).length===1&&sections.find(s=>s.name===name).content.length>0);
}
function saveEntry(enable) {
  if(enable && (!editorDraft.name.trim()||!validSections(editorDraft.body)||(editorDraft.kind==='tag'&&!editorDraft.genres.length))){toast('启用前请补齐名称、六个分节与适用类型');return;}
  const before=item(editorDraft.id);
  // Saving a draft cannot replace an enabled version in this prototype.
  if(!enable && before.status==='enabled') {
    before.savedDraft=structuredClone(editorDraft); editorDraft=structuredClone(before);
    persist();render();openDialog('草稿已保存','当前启用版本保持不变。下次可以载入草稿继续编辑。',button('载入草稿','load-draft')+button('知道了','close-dialog','primary'));return;
  }
  const updated={...editorDraft,version:before.version+1,status:enable?'enabled':'draft'};delete updated.savedDraft;
  state.items[state.items.findIndex(i=>i.id===updated.id)]=updated;editorDraft=structuredClone(updated);persist();render();
  openDialog(enable?'已保存并启用':'草稿已保存',`<div class="success-mark">✓</div><p>${esc(updated.name)} · v${updated.version}</p><p class="small muted">${enable?'新讨论将读取这一版说明；已开始的讨论保持原版。':'草稿不会用于新讨论。'}</p>`,button('知道了','close-dialog','primary'));
}
function getSelectionSnapshot() { return {genre:state.genre,tags:[...state.tags],idea:state.idea,entries:structuredClone([item(state.genre),...currentTags()]),input:compiledInput(),mode:state.mode}; }
function changeGenre(id) {
  const incompatible=state.tags.filter(tag=>!item(tag)?.genres.includes(id));
  if(incompatible.length) {pendingGenre=id;openDialog('切换主类型',`<p>以下标签不适用于「${esc(item(id).name)}」，切换后将从本次选择移除：</p><div class="chips" style="margin-top:15px">${incompatible.map(id=>chip(item(id))).join('')}</div><p class="small muted" style="margin-top:18px">其他兼容标签会保留。已有讨论快照不会被改写。</p>`,button('保留当前类型','close-dialog')+button('切换并移除','confirm-genre','primary'));return;}
  applyGenre(id);
}
function applyGenre(id){state.genre=id;state.tags=state.tags.filter(tag=>item(tag)?.genres.includes(id));filter='全部';tagQuery='';invalidate();closeDialog();render();}
document.addEventListener('click', e=> {
  const a=e.target.closest('[data-action]'); if(!a||a.disabled)return;
  const action=a.dataset.action,id=a.dataset.id;
  if(action==='close-dialog'){closeDialog();return;}
  if(action.startsWith('nav-')){navigate(action.slice(4));return;}
  switch(action) {
    case 'next':goStep(step+1);break;
    case 'prev':goStep(step-1);break;
    case 'step':goStep(Number(a.dataset.step));break;
    case 'fill-example':state.idea=initial().idea;invalidate();render();break;
    case 'genre':changeGenre(id);break;
    case 'confirm-genre':applyGenre(pendingGenre);break;
    case 'tag':state.tags=state.tags.includes(id)?state.tags.filter(t=>t!==id):[...state.tags,id];invalidate();render();break;
    case 'filter':filter=a.dataset.filter;render();break;
    case 'tone':state.tonePriority=a.dataset.value;invalidate();render();break;
    case 'tag-info':openDialog(`${esc(item(id).name)} · 写作说明`,`<div class="md-preview">${markdown(item(id).body)}</div>`,button(state.tags.includes(id)?'从组合中移除':'加入创作组合','dialog-tag','primary',`data-id="${id}"`));break;
    case 'dialog-tag':state.tags=state.tags.includes(id)?state.tags.filter(t=>t!==id):[...state.tags,id];invalidate();closeDialog();render();break;
    case 'prompt':openDialog('模型将读取的组合说明',`<p class="small muted">包含所选条目的完整写作说明。正式开发后按阶段编译，不静默遗漏标签。</p><pre>${esc(compiledInput())}</pre>`);break;
    case 'frozen-prompt':openDialog('本次讨论的冻结输入',`<pre>${esc(state.frozen.input)}</pre>`);break;
    case 'start':if(canStart()){state.frozen=getSelectionSnapshot();state.route=null;state.adopted=false;persist();navigate('discussion');window.scrollTo(0,0);}break;
    case 'demo-discussion':Object.assign(state,{idea:initial().idea,genre:'genre.romance',tags:initial().tags});state.frozen=getSelectionSnapshot();state.route=null;state.adopted=false;render();break;
    case 'choose-route':state.route=id;persist();render();break;
    case 'rechoose':state.adopted=false;render();break;
    case 'adopt':if(state.route){state.adopted=true;persist();render();toast('已模拟提交所选走向与补充；未调用模型');window.scrollTo(0,0);}break;
    case 'story-effects':openDialog('从标签到人物的行动',`<p class="small muted">固定示例 · 下列是规划意图，不代表正文已兑现。</p><div class="promise-grid"><div class="promise"><b>浪漫 → 关系主线</b><p>她是否愿意再次相信他，改变两人在共同责任中的选择。</p></div><div class="promise"><b>背叛 → 开篇与代价</b><p>出售共同承诺保留的旧宅，损害生活与信任；伤害不会因为重逢被抹去。</p></div><div class="promise"><b>独立自主 → 持续的行动方式</b><p>她经营自己的事业，提出合作边界；不以放弃决定权换取帮助。</p></div><div class="promise"><b>第二次机会 → 中后段回报</b><p>他先在利益冲突中做出不同选择，再由双方自由决定关系是否重新成立。</p></div></div><div class="note" style="margin-top:18px">这些意图进入骨架和相关章节。具体场景在原有写作与编辑阶段落实，不增加一次逐章阅读评议。</div>`,button('知道了','close-dialog','primary'));break;
    case 'custom-route':openDialog('提出不同的大方向','<label class="input-label" for="custom-route">希望主线怎样变化？</label><textarea class="field" id="custom-route" rows="4" placeholder="例如：我想保留重新相爱的主题，但以她的事业转变为主要行动。"></textarea><p class="small muted" style="margin-top:12px">正式流程会先重新提出方案，不将未选择的建议自动采用。</p>',button('取消','close-dialog')+button('提交新方向 · 演示','custom-submit','primary'));break;
    case 'custom-submit':if(!$('#custom-route').value.trim()){toast('请写下不同的方向');break;}state.supplement=$('#custom-route').value;state.route=null;closeDialog();render();toast('新方向已记录。真实模型重规划将在开发后接入。');break;
    case 'edit-selection':step=2;navigate('create');break;
    case 'source-import':openDialog('导入参考书',`<div class="note">此处使用虚构演示资料，展示导入后的交互。</div><div class="source-book">${cover()}<div><h3>雨夜之后</h3><p class="small muted">8 个免费章节 · 官方简介 · 封面</p></div></div><p class="small muted">正式支持 metadata.json、synopsis.md、chapters.json、chapters/ 与可选封面；overview.md 不要求提供。</p>`,button('取消','close-dialog')+button('载入演示资料','source-load','primary'));break;
    case 'source-load':state.source=true;state.sourceReady=true;invalidate();closeDialog();render();toast('已载入演示资料与模拟分析结果');break;
    case 'source-remove':state.source=false;state.sourceReady=false;invalidate();render();break;
    case 'source-mode':state.mode=a.dataset.mode;invalidate();render();break;
    case 'source-detail':openDialog('来源资料范围',`<div class="note">演示资料，不代表本轮已分析了这本虚构小说。</div><div class="kv"><span>章节</span><b>所供免费章节 1–8</b></div><div class="kv"><span>简介</span><b>synopsis.md · 已提供</b></div><div class="kv"><span>overview / reading_guide</span><b>未提供 · 可选</b></div><div class="kv"><span>参考封面</span><b>${state.coverReference?'带入工作室':'不使用'}</b></div><p class="section-label">分析结果呈现方式</p><p class="small muted">区分原作已知事件、未读后续和可迁移的写作方法。参考图只提供视觉参考，不自动指定新故事中的人物身份。</p>`,button('查看封面交接','cover-handoff')+button('知道了','close-dialog','primary'));break;
    case 'cover-handoff':window.CoverStudio.handoff(state.mode==='rewrite',state.coverReference);closeDialog();navigate('covers');break;
    case 'library-kind':dirtyGuard(()=>{libraryKind=a.dataset.kind;libraryQuery='';selectedId=state.items.find(i=>i.kind===libraryKind&&i.status!=='deleted')?.id;editorDraft=null;render();});break;
    case 'entry':dirtyGuard(()=>{selectedId=id;editorDraft=null;editorMode='edit';render();});break;
    case 'editor-mode':editorMode=a.dataset.mode;$('#editor-pane').innerHTML=editorView();break;
    case 'full-md':openDialog('完整 Markdown 文件',`<pre>${esc(fullMarkdown(editorDraft))}</pre>`,button('下载 Markdown','download-md','primary'));break;
    case 'download-md':{const u=URL.createObjectURL(new Blob([fullMarkdown(editorDraft)],{type:'text/markdown;charset=utf-8'}));const link=document.createElement('a');link.href=u;link.download=`${editorDraft.id}.md`;link.click();setTimeout(()=>URL.revokeObjectURL(u),1000);break;}
    case 'format-help':openDialog('一份文件，六个创作重点',`<div class="md-preview">${markdown('## 核心承诺\n\n读者选择它，应该得到什么体验？\n\n## 故事机制\n\n它如何改变行动、代价与回报？\n\n## 发散空间\n\n哪些人物、关系与行动可以变化？\n\n## 组合原则\n\n它与其他标签怎样互相影响？\n\n## 场景与表达\n\n通过哪些动作、对白与环境让它可感？\n\n## 简介与兑现\n\n简介承诺什么，正文如何做到？')}</div>`);break;
    case 'save-draft':saveEntry(false);break;
    case 'save-enable':saveEntry(true);break;
    case 'load-draft':editorDraft=structuredClone(item(selectedId).savedDraft);closeDialog();$('#editor-pane').innerHTML=editorView();break;
    case 'discard':editorDraft=structuredClone(item(selectedId));render();break;
    case 'confirm-leave':{const fn=pendingLeave;pendingLeave=null;editorDraft=null;closeDialog();fn?.();break;}
    case 'entry-meta':openDialog('编辑条目信息',`<label class="input-label" for="entry-name">名称</label><input id="entry-name" class="field" value="${esc(editorDraft.name)}"><label class="input-label" for="entry-desc" style="margin-top:18px">一句话作用</label><textarea class="field" id="entry-desc" rows="2">${esc(editorDraft.description)}</textarea>`,button('取消','close-dialog')+button('应用到草稿','apply-meta','primary'));break;
    case 'apply-meta':if(!$('#entry-name').value.trim()){toast('名称不能为空');break;}editorDraft.name=$('#entry-name').value.trim();editorDraft.description=$('#entry-desc').value.trim();closeDialog();$('#editor-pane').innerHTML=editorView();break;
    case 'associations':openDialog('适用主类型',`<p class="small muted">只把含义一致的标签复用于不同类型。</p><div style="display:grid;gap:14px;margin-top:20px">${state.items.filter(i=>i.kind==='genre'&&i.status!=='deleted').map(i=>`<label class="check-label"><input type="checkbox" name="association" value="${i.id}" ${editorDraft.genres.includes(i.id)?'checked':''}>${esc(i.name)} · ${esc(i.en)}</label>`).join('')}</div>`,button('取消','close-dialog')+button('应用到草稿','apply-associations','primary'));break;
    case 'apply-associations':editorDraft.genres=[...document.querySelectorAll('[name="association"]:checked')].map(i=>i.value);closeDialog();$('#editor-pane').innerHTML=editorView();break;
    case 'related-tags':openDialog('关联的故事标签',`<div class="related-list">${state.items.filter(t=>t.kind==='tag'&&t.status!=='deleted'&&t.genres.includes(editorDraft.id)).map(t=>`<button class="related-item" data-action="open-related" data-id="${t.id}">${esc(t.name)}<span>${esc(t.en)}</span></button>`).join('')||'<p class="small muted">尚未关联标签</p>'}</div><p class="small muted">在标签的“适用主类型”中维护关联，创建页会读取同一份关系。</p>`);break;
    case 'open-related':closeDialog();dirtyGuard(()=>{libraryKind='tag';selectedId=id;editorDraft=null;render();});break;
    case 'new-entry':dirtyGuard(()=>openDialog(`新建${libraryKind==='genre'?'主类型':'故事标签'}`,`<label class="input-label" for="new-name">名称</label><input id="new-name" class="field" placeholder="例如：久别重逢"><p class="small muted" style="margin-top:14px">先保存为草稿，写好说明后再启用。</p>`,button('取消','close-dialog')+button('创建草稿','create-entry','primary')));break;
    case 'create-entry':{const name=$('#new-name').value.trim();if(!name){toast('请填写名称');break;}const key=`custom_${Date.now()}`;const created={id:`${libraryKind}.${key}`,key,name,en:'Custom',kind:libraryKind,description:'填写它如何影响故事。',group:'自定义',genres:libraryKind==='tag'?[state.genre]:[],version:1,status:'draft',origin:'自定义',body:['核心承诺','故事机制','发散空间','组合原则','场景与表达','简介与兑现'].map(h=>`## ${h}\n\n`).join('\n')};state.items.push(created);selectedId=created.id;editorDraft=null;closeDialog();render();toast('草稿已创建');break;}
    case 'clone':dirtyGuard(()=>{const cloned=structuredClone(item(selectedId));cloned.id=`${cloned.kind}.copy_${Date.now()}`;cloned.name+=' · 副本';cloned.status='draft';cloned.version=1;cloned.origin='自定义副本';delete cloned.savedDraft;state.items.push(cloned);selectedId=cloned.id;editorDraft=null;render();toast('已复制为草稿');});break;
    case 'toggle-status':if(dirty()){toast('请先保存或放弃修改');break;}if(editorDraft.status!=='enabled'){saveEntry(true);break;}openDialog('停用此条目？','停用后不再供新建选择。已经开始的讨论仍使用原来的快照。',button('取消','close-dialog')+button('停用','confirm-disable','danger'));break;
    case 'confirm-disable':item(selectedId).status='disabled';editorDraft=null;closeDialog();render();toast('已停用，历史快照保持不变');break;
    case 'delete':openDialog('删除这个条目？',`<p>「${esc(editorDraft.name)}」将从资料库和新建选项中移除。</p><p class="small muted" style="margin-top:10px">已开始的讨论保留原文快照；本次删除不会修改现有作品。</p>`,button('取消','close-dialog')+button('删除条目','confirm-delete','danger'));break;
    case 'confirm-delete':item(selectedId).status='deleted';state.tags=state.tags.filter(t=>t!==selectedId);if(state.genre===selectedId){state.genre='';state.tags=[];}editorDraft=null;selectedId=null;closeDialog();render();toast('已删除，历史快照保持不变');break;
    case 'taxonomy-json':{const entries=state.frozen?.entries||[item(state.genre),...currentTags()].filter(Boolean);openDialog('H5 数据合同示例',`<p class="small muted">以下是字段示意，不是当前线上 H5 已支持的协议。交接草案中列出了联合升级方式。</p><pre>${esc(JSON.stringify({schema_version:'novel-taxonomy.v1',namespace:'novel-os.publisher-example',genre:entries.filter(i=>i.kind==='genre').map(i=>({id:i.id,revision:1,labels:{'zh-CN':i.name,en:i.en},definition:i.description}))[0],tags:entries.filter(i=>i.kind==='tag').map(i=>({id:i.id,revision:1,labels:{'zh-CN':i.name,en:i.en},definition:i.description}))},null,2))}</pre>`);break;}
    case 'h5-tag':openDialog(`按「${esc(item(id)?.name||id)}」筛选作品`, '<p>正式 H5 将按稳定 ID 查询同类型作品，并从服务端目录读取名称。</p><p class="small muted" style="margin-top:12px">本原型只展示筛选入口，不连接线上书库。</p>',button('返回作品页','close-dialog','primary'));break;
    case 'read-demo':toast('这是 H5 展示预览，未加载示例小说正文');break;
    case 'reset':openDialog('重置演示数据？','仅清除此原型的本地选择、草稿和演示讨论，不影响实际系统。',button('取消','close-dialog')+button('重置','confirm-reset','primary'));break;
    case 'confirm-reset':window.CoverStudio.reset();state=initial();step=1;filter='全部';tagQuery='';libraryKind='genre';libraryQuery='';selectedId='genre.romance';editorDraft=null;editorMode='edit';persist();closeDialog();location.hash='create';render();break;
  }
});
document.addEventListener('input',e=>{
  const target=e.target;
  if(target.id==='idea'){state.idea=target.value;$('#idea-count').textContent=`${state.idea.length} 字`;invalidate();}
  else if(['audience','sourceScope'].includes(target.id)){state[target.id]=target.value;invalidate();}
  else if(target.id==='supplement'){state.supplement=target.value;persist();}
  else if(target.id==='tag-search'){tagQuery=target.value;$('#tag-grid').innerHTML=tagCards();}
  else if(target.id==='library-search'){libraryQuery=target.value;$('#entry-list').innerHTML=libraryList();}
  else if(target.id==='md-editor'){editorDraft.body=target.value;$('#editor-footer').innerHTML=editorFooter();}
});
document.addEventListener('change',e=>{
  if(['chapters','chapterWords'].includes(e.target.id)){const t=e.target;const n=Number(t.value);if(!Number.isInteger(n)||n<Number(t.min)||n>Number(t.max)){toast(`请输入 ${t.min}–${t.max} 之间的整数`);t.value=state[t.id];return;}state[t.id]=n;invalidate();}
  if(e.target.id==='language'){state.language=e.target.value;invalidate();$('#unit-label').textContent=state.language==='英文'?'词':'字';}
  if(['sourceRights','coverReference'].includes(e.target.id)){state[e.target.id]=e.target.checked;invalidate();}
});
// Protect an unsaved editor on navigation links as well as buttons.
document.addEventListener('click',e=>{const link=e.target.closest('a[href^="#"]');if(link&&dirty()){e.preventDefault();navigate(link.hash.slice(1));}});
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0);});
window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
render();
