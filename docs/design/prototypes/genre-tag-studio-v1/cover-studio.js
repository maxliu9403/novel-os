/* Isolated design controller. Production orchestration remains in poster_studio. */
window.CoverStudio = (() => {
  const STORE = 'novelos.cover-reference.prototype.v1';
  const SAMPLE = '../cover-studio-v2/assets/cover-1.png';
  const fixtures = [
    { title:'同一屋檐，不同的距离', hook:'关系有裂缝，仍有再靠近的可能。', layout:'depth', action:'女主在前，前夫在后侧；温暖旅馆连接两人，留出清晰的身体间距。', promise:'背叛 × 第二次机会', color:'#adb9b1' },
    { title:'这次，由她决定', hook:'爱情的吸引，与她保有的选择权。', layout:'key', action:'以女主为视觉中心，前夫停留在次要平面；旅馆钥匙提示她拥有自己的事业。', promise:'独立自主 × 浪漫', color:'#b6adbe' },
    { title:'门两边的牵引', hook:'共享的过去，使分开的两人仍有关联。', layout:'door', action:'以旅馆门廊组织双人站位，光线跨过空间，人物不被“拦在门外”的动作定义。', promise:'关系张力 × 生活归属', color:'#b6b4a5' },
    { title:'旧伤之后，仍有光', hook:'克制的靠近，不预告最终复合。', layout:'overlap', action:'两个人物轮廓与旅馆暖窗轻度叠合，视线错开；以海报式蒙太奇传达关系余温。', promise:'第二次机会 × 情绪对照', color:'#a4b4c8' }
  ];
  const facets = [
    {id:'layers', title:'人物层次', visible:'前方一位人物，后方左右各一位，前景是视觉中心。', suggestion:'转译为本书双主角的前后关系，不沿用第三个人物。'},
    {id:'light', title:'光线与情绪', visible:'建筑暖灯在后，人物服装和前景较暗。', suggestion:'保留暖冷对照，让温暖背景与关系裂缝同时存在。'},
    {id:'medium', title:'画面质感', visible:'细致的人像质感、柔和景深与电影式照明。', suggestion:'借鉴摄影式质感；人物外观按本书重新设计。'},
    {id:'space', title:'书名空间', visible:'原图无文字，底部仍有深色衣物，并非完全留白。', suggestion:'调整下部细节与明度，给本书准确书名预留安静区域。'}
  ];
  const fresh = () => ({ ref:null, mode:'inspiration', keep:['layers','light','medium','space'], preference:'突出两个人物之间的牵引与裂痕，保留女主的主体性。', revision:0, plan:null, analysisId:null, history:[], gallery:false, selected:null, tab:'design' });
  let state;
  try { state = {...fresh(), ...JSON.parse(localStorage.getItem(STORE) || 'null')}; } catch { state = fresh(); }
  let root, context, ui, controller, url='', asset=null, loading=false, message='', busy=false, ticket=0;
  const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const btn = (label, action, cls='', attrs='') => `<button class="button ${cls}" data-cover-action="${action}" ${attrs}>${label}</button>`;
  const badge = (t, cls='') => `<span class="badge ${cls}">${e(t)}</span>`;
  function save() { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch { message='浏览器存储不可用，本次操作仅在当前页面保留。'; } }
  function rewrite() { return !!context?.rewrite; }
  function role() { return state.ref && (rewrite() || state.mode==='adaptation') ? 'adaptation_reference' : 'visual_inspiration'; }
  function binding() { return JSON.stringify({ idea:context.idea, entries:context.entries, rewrite:rewrite(), ref:state.ref?.id || null, role:role(), keep:state.keep, preference:state.preference }); }
  function currentPlan() { return state.plan && state.plan.binding === binding(); }
  function invalidate() {
    if (state.plan) { state.history.push(state.plan); state.history = state.history.slice(-8); state.plan=null; }
    state.revision++; message=''; save();
  }
  function imageSrc() { return state.ref?.kind==='sample' ? SAMPLE : url; }
  function referenceReady() { return !state.ref || state.ref.kind==='sample' || !!asset; }
  function projectSections(body, names) {
    return String(body).split(/(?=^## )/m).filter(block => names.includes(block.split('\n')[0].replace(/^## /,'').trim())).join('\n').trim();
  }
  function request() {
    const selections = context.entries.map(i => ({id:i.id, version:i.version, name:i.name,
      guidance:projectSections(i.body,['核心承诺','封面表达'])}));
    return {
      preview_only:true, sent_to_model:false,
      story_source:{kind:'prototype_story_brief', complete_skeleton_attached:false, idea:context.idea},
      selected_knowledge:selections,
      author_preference:state.preference,
      reference:state.ref ? {role:role(), id:state.ref.id, filename:state.ref.name,
        mime:state.ref.mime, dimensions:state.ref.width ? [state.ref.width,state.ref.height] : null,
        sha256:state.ref.sha || 'production_will_bind_actual_bytes', actual_bytes_sent:false,
        planning_attachment:'image_content', generation_attachment:'image_edit_input',
        requested_facets:[...state.keep]} : null,
      intended_image_operation:state.ref ? 'edit' : 'generate',
      planning:'Complete frozen skeleton + selected knowledge + optional image → reference suggestions and four directions in ONE response.',
      output:'Text-free artwork + existing local title renderer. Artistic poster staging allowed; core book identities preserved.'
    };
  }
  function prompt(index=0) {
    const f=fixtures[index];
    return [state.ref ? `Use the attached image as ${role()==='adaptation_reference'?'the edit basis':'a visual reference'} for a NEW book-poster artwork.` : 'Create an original book-poster artwork.',
      `BOOK (fictional prototype): ${context.idea}`,
      `DIRECTION: ${f.title}. ${f.action}`,
      `GENRE / TAGS: ${context.entries.map(i=>i.name).join(' / ')}. Translate them into one relational and emotional promise, not a collage of props.`,
      `AUTHOR PREFERENCE: ${state.preference}`,
      state.ref ? `REFERENCE FACETS REQUESTED: ${facets.filter(f=>state.keep.includes(f.id)).map(f=>f.title).join(', ') || 'None; freely reinterpret the image'}. Do not treat other source features as required.` : '',
      'Use the two protagonists of this demo book; do not copy the sample’s third figure or invent an affair. Physical identities come from the confirmed book, not the reference image.',
      'Convey lingering attraction, fractured trust and personal agency. The promotional arrangement need not reproduce a literal story scene or prove a precise event.',
      '2:3 text-free artwork. Keep faces above the quiet lower title region. The exact approved title is rendered locally. No source publication title, author name or platform branding in the new artwork.'
    ].filter(Boolean).join('\n\n');
  }
  function diagram(f, n) {
    let people = f.layout==='overlap' ? '<g opacity=".68"><ellipse cx="91" cy="92" rx="30" ry="37"/><path d="M35 225v-59q55-67 111 0v59z"/></g><g opacity=".42"><ellipse cx="162" cy="101" rx="29" ry="36"/><path d="M109 229v-60q54-62 107 0v60z"/></g>' : `<g opacity=".42"><ellipse cx="${f.layout==='door'?173:170}" cy="94" rx="20" ry="25"/><path d="M135 220v-81q35-45 70 0v81z"/></g><g opacity=".85"><ellipse cx="${f.layout==='key'?101:85}" cy="117" rx="25" ry="30"/><path d="M38 237v-66q48-57 96 0v66z"/></g>`;
    if(f.layout==='key') people='<g opacity=".35"><ellipse cx="53" cy="91" rx="17" ry="22"/><path d="M23 200v-68q30-39 61 0v68z"/></g><g opacity=".85"><ellipse cx="136" cy="109" rx="29" ry="35"/><path d="M79 234v-67q57-64 113 0v67z"/></g><g fill="none" stroke="#efd4a1" stroke-width="3"><circle cx="173" cy="180" r="7"/><path d="m168 185-16 19m7-9 6 5m-11 1 5 5"/></g>';
    return `<svg class="cs-diagram" viewBox="0 0 250 310" aria-label="${e(f.title)}构图示意，非生成图片" role="img"><defs><linearGradient id="cs-bg-${n}" x2="0" y2="1"><stop stop-color="${f.color}"/><stop offset="1" stop-color="#e8e9ef"/></linearGradient></defs><rect width="250" height="310" fill="url(#cs-bg-${n})"/><path d="M24 200V61l101-36 101 36v139" fill="none" stroke="#fff" stroke-opacity=".5" stroke-width="2"/><rect x="104" y="63" width="45" height="122" fill="#f1dec3" opacity=".7"/>${f.layout==='door'?'<path d="M127 56v176" stroke="#607084" stroke-width="5"/>':''}<g fill="#273b52">${people}</g><rect x="0" y="231" width="250" height="79" fill="#eef0f6" opacity=".92"/><text x="125" y="263" text-anchor="middle" fill="#45516d" font-size="15" font-family="sans-serif">书名排版区</text><text x="125" y="284" text-anchor="middle" fill="#8791a7" font-size="10" font-family="sans-serif">${String(n+1).padStart(2,'0')} / COMPOSITION STUDY</text></svg>`;
  }
  function referencePanel() {
    const loaded = !!state.ref;
    return `<section class="cs-reference"><div class="row between"><h2>参考图 <span class="optional">可选 · 1 张</span></h2>${loaded?btn('移除','remove','small'):''}</div>
      ${loaded?`<div class="cs-reference-image">${imageSrc()?`<img src="${e(imageSrc())}" alt="${e(state.ref.name)}">`:'<span>正在恢复本地参考图…</span>'}<span class="cs-image-label">${state.ref.kind==='sample'?'既有演示素材':'本地预览 · 未上传'}</span></div><div class="cs-file"><b>${e(state.ref.name)}</b><span>${state.ref.kind==='sample'?'项目既有生成素材 · 非源书人物':`${state.ref.width} × ${state.ref.height} · ${(state.ref.size/1024/1024).toFixed(2)} MiB`}</span></div>`:
      `<div class="cs-upload"><span class="cs-upload-icon">＋</span><h3>用一张图，表达你的想法</h3><p>喜欢的构图、光线或人物层次。</p><label class="button" for="cs-file">添加参考图</label><button class="text-button purple" data-cover-action="sample">试用示例图</button><small>PNG / JPEG · 最多 12 MiB</small></div>`}
      <input type="file" id="cs-file" accept="image/png,image/jpeg" class="cs-file-input" aria-label="上传参考图">
      ${loaded?`<div class="row wrap cs-reference-actions"><label class="button small" for="cs-file">替换图片</label>${state.ref.kind!=='sample'?btn('试用示例图','sample','small'):''}</div><div class="cs-reference-use">${rewrite()?`${badge('原作重写 · 已自动采用原图二创')}<p>有参考图时直接以图片为二创基础。</p>`:`<label for="cs-mode">参考用法</label><select class="field" id="cs-mode"><option value="inspiration" ${state.mode==='inspiration'?'selected':''}>借鉴视觉，设计新作</option><option value="adaptation" ${state.mode==='adaptation'?'selected':''}>基于原图二创</option></select>`}</div>`:''}</section>`;
  }
  function design() {
    const compact=currentPlan();
    return `${compact?`<details class="cs-input-details"><summary><span>${state.ref?'<img src="'+e(imageSrc())+'" alt="当前参考图">':'<span class="cs-summary-symbol">✦</span>'}<span><b>本书素材与偏好</b><small>${state.ref?(role()==='adaptation_reference'?'原图二创':'参考图辅助设计'):'无参考图 · 从故事设计'} · ${context.entries.map(i=>e(i.name)).join(' / ')}</small></span></span><em>调整素材与偏好</em></summary>`:''}<div class="cs-input-grid">${referencePanel()}<section class="cs-brief"><div class="row between"><h2>让画面抓住核心关系</h2>${badge('示例故事','neutral')}</div><p class="cs-premise">${e(context.idea)}</p><div class="cs-tags">${context.entries.map(i=>badge(i.name)).join('')}</div><div class="cs-principles"><div><b>故事的核心不变</b><span>人物身份、关系与世界，以本书为准。</span></div><div><b>画面可以自由设计</b><span>海报式同框、层次与象征，不必还原某一幕。</span></div></div><label class="input-label" for="cs-preference">这次希望封面突出什么</label><textarea id="cs-preference" class="field" rows="3" placeholder="例如：两人仍有吸引力，但女主不再是被动的一方。">${e(state.preference)}</textarea><div class="cs-route"><span class="dot"></span><b>${state.ref?(role()==='adaptation_reference'?'原图二创':'参考图辅助设计'):'从故事设计'}</b><span>${state.ref?'同一次规划完成图片拆解与四个方向':'完整骨架与所选题材一起规划'}</span></div><div class="cs-plan-actions">${btn(currentPlan()?'重新规划 · 演示':state.ref?.kind==='upload'||!context.demoSupported?'准备规划输入':'规划 4 个方向 · 演示','plan','primary',loading||busy||!referenceReady()?'disabled':'')}${btn('查看输入','input','small')}</div><p class="cs-fine">本页不调用模型。示例图片的拆解为预写样例，新上传图片只在浏览器本地预览。</p></section></div>${compact?'</details>':''}
      ${message?`<div class="note ${message.includes('待')?'':'green'} cs-message" role="status">${e(message)}</div>`:''}
      ${state.plan&&!currentPlan()?'<div class="note amber cs-message">故事或标签已变化。旧方向保留为历史，请按当前输入重新规划。</div>':''}
      ${state.ref?.kind==='sample'&&state.analysisId===state.ref.id?analysis():''}${currentPlan()?planned():''}`;
  }
  function analysis() { return `<details class="cs-analysis" ${!currentPlan()?'open':''}><summary>参考图如何融入本书 <span>图片拆解与建议</span></summary><div class="cs-analysis-grid">${facets.map(f=>`<label class="cs-observation"><span class="cs-observation-head"><b>${e(f.title)}</b><span><input type="checkbox" data-cover-facet="${f.id}" ${state.keep.includes(f.id)?'checked':''}> 借鉴</span></span><span class="cs-observed">图中可见 · ${e(f.visible)}</span><span class="cs-translated">本书建议 · ${e(f.suggestion)}</span></label>`).join('')}</div><p>人物的具体身份、关系与年龄不从参考图推定。取消借鉴后，重新规划才会更新方向。</p></details>`; }
  function planned() {
    return `<div class="cs-plan-result"><div class="section-top"><div><div class="eyebrow">方向提案 · ${state.plan.revision} 版</div><h2>四种表达，同一个故事</h2></div>${badge('预写示例 · 未生成图片','neutral')}</div>
      <div class="cs-direction-grid">${fixtures.map((f,n)=>`<article class="cs-direction">${diagram(f,n)}<div class="cs-direction-text"><div class="eyebrow">方向 ${String(n+1).padStart(2,'0')} · 构图示意</div><h3>${f.title}</h3><p>${f.hook}</p><span class="cs-promise">${f.promise}</span><details><summary>人物安排与提示词</summary><p>${f.action}</p>${btn('查看图像提示词','prompt','small',`data-index="${n}"`)}</details></div></article>`).join('')}</div>
      <div class="cs-confirm"><div><b>基于这些方向，生成 4 张候选</b><p>完整图片可直接选用；每一张都能单独精修。</p></div>${btn('查看候选交互 · 演示','candidates','primary')}</div></div>`;
  }
  function gallery() {
    if(!state.gallery) return `<div class="cs-empty"><span class="cs-upload-icon">▧</span><h2>候选封面将在这里呈现</h2><p>规划后可体验选用与单张精修。原型不产生真实图片。</p>${btn('前往设计','design','primary')}</div>`;
    return `<div class="note cs-message">以下是项目既有的 4 张演示素材，用于体验选用和精修；不是按当前参考图、故事或方向新生成的结果。</div><div class="cs-gallery">${fixtures.map((f,n)=>`<article class="cs-candidate ${state.selected===n?'selected':''}"><div class="cs-art"><img src="../cover-studio-v2/assets/cover-${n+1}.png" alt="既有候选素材 ${n+1}" loading="lazy">${badge(state.selected===n?'已选用 · 演示':'既有素材','neutral')}</div><div class="cs-candidate-bottom"><h3>候选 ${String(n+1).padStart(2,'0')}</h3><div class="row wrap">${btn(state.selected===n?'已选用':'选用这张','select',state.selected===n?'':'primary',`data-index="${n}" ${state.selected===n?'disabled':''}`)}${btn('精修','refine','',`data-index="${n}"`)}</div></div></article>`).join('')}</div><div class="cs-gallery-foot"><p>完整图片直接选用，不需要接受理由。精修只编辑选中图片，不影响其他候选。</p><a href="../cover-studio-v2/index.html" target="_blank" rel="noopener">查看既有工作室完整交互 ↗</a></div>`;
  }
  function draw() {
    if (!root?.isConnected || !root.querySelector('[data-cover-root]')) return;
    root.querySelector('[data-cover-root]').innerHTML = `<div class="page-head"><div><div class="eyebrow">NOVEL OS / COVER STUDIO</div><h1>封面工作室</h1><p class="subtitle">雨停之后，再选择你 <span class="cs-demo-label">交互示例</span></p></div><a class="button small" href="../../genre-tag-studio-v1/cover-reference-design.md" target="_blank" rel="noopener">设计说明 ↗</a></div><div class="cs-tabs" role="tablist" aria-label="封面工作室"><button role="tab" aria-selected="${state.tab==='design'}" data-cover-action="design">封面设计</button><button role="tab" aria-selected="${state.tab==='gallery'}" data-cover-action="gallery">候选封面 ${state.gallery?badge('4'):badge('0','neutral')}</button><span>${rewrite()?'原作重写':'新作创作'}${state.ref?' · 已有参考图':''}</span></div><div role="tabpanel" class="cs-content">${state.tab==='gallery'?gallery():design()}</div>`;
  }
  async function restoreAsset() {
    const ref=state.ref;
    if(!ref || ref.kind!=='upload' || asset?.id===ref.id) return;
    const seq=++ticket; loading=true; draw();
    const found=await window.CoverAssets.read(ref.id);
    if(seq!==ticket || state.ref?.id!==ref.id) return;
    loading=false;
    if(found) { asset=found; if(url)URL.revokeObjectURL(url); url=URL.createObjectURL(found.blob); }
    else { message='本地参考图已不可用，请重新选择图片；旧方向不会按无图路径继续生成。'; }
    draw();
  }
  async function upload(file) {
    if(!file || busy) return;
    const seq=++ticket; busy=true; draw();
    try {
      const result=await window.CoverAssets.inspect(file);
      if(seq!==ticket)return;
      invalidate(); asset=result.record;
      if(url)URL.revokeObjectURL(url); url=URL.createObjectURL(asset.blob);
      const {blob,...meta}=asset;
      state.ref={...meta,kind:'upload'};
      message=result.persisted?'参考图已保存在本地浏览器。可查看待发送输入；本原型不会调用模型分析这张图片。':'参考图仅在当前页面保留；浏览器未允许持久保存。';
      save();
    } catch(error) { message=error.message; }
    finally { if(seq===ticket){busy=false;loading=false;draw();} }
  }
  function sample() {
    ticket++;busy=false;loading=false;invalidate();
    state.ref={id:'existing-demo-cover-1',kind:'sample',name:'人物层次 · 演示参考图',mime:'image/png',width:1024,height:1536};
    asset=null;if(url)URL.revokeObjectURL(url);url='';save();draw();
  }
  function handleClick(event) {
    const target=event.target.closest('[data-cover-action]');
    if(!target || target.disabled)return;
    const action=target.dataset.coverAction;
    if(action==='sample'){sample();return;}
    if(action==='remove'){ticket++;busy=false;loading=false;invalidate();state.ref=null;asset=null;if(url)URL.revokeObjectURL(url);url='';save();draw();return;}
    if(action==='design'||action==='gallery'){state.tab=action==='design'?'design':'gallery';save();draw();return;}
    if(action==='input'){ui.dialog('本次规划输入 · 未发送',`<p class="small muted">正式实现将附完整骨架与实际图片字节；本页只有示例故事和本地预览，不伪装真实模型请求。</p><pre>${e(JSON.stringify(request(),null,2))}</pre>`);return;}
    if(action==='plan') {
      if(!referenceReady())return;
      if(state.ref?.kind==='upload'||!context.demoSupported) {
        message='输入已准备，待真实模型分析。原型不调用模型，也不会用示例拆解冒充你这张图或当前组合的结果。';save();draw();return;
      }
      invalidate();state.analysisId=state.ref?.id || null;state.plan={revision:state.revision,binding:binding()};save();draw();
      root.querySelector('.cs-plan-result')?.scrollIntoView({behavior:'smooth',block:'start'});return;
    }
    if(action==='prompt'){ui.dialog('图像提示词 · 演示',`<p class="small muted">${state.ref?'计划实际附带参考图；此原型没有发送图片。':'无参考图，计划使用文生图。'}</p><pre>${e(prompt(Number(target.dataset.index)))}</pre>`);return;}
    if(action==='candidates'){if(!currentPlan())return;state.gallery=true;state.tab='gallery';save();draw();root.scrollIntoView({behavior:'smooth'});return;}
    if(action==='select'){state.selected=Number(target.dataset.index);save();draw();ui.toast('已选用这张演示封面');return;}
    if(action==='refine') {
      const n=Number(target.dataset.index);
      ui.dialog(`精修候选 ${n+1}`,`<div class="cs-refine-preview"><img src="../cover-studio-v2/assets/cover-${n+1}.png" alt="本次精修的实际候选"><p>以这张候选为基础。<br>原图与其他候选保留。</p></div><label class="input-label" for="cs-refine-prompt">希望怎么调整</label><textarea class="field" id="cs-refine-prompt" rows="4" placeholder="例如：拉开两人的距离，保留人物外观和光线。"></textarea><p class="small muted">只准备一张图的编辑请求。不会重新规划全部方向。</p>`,
        `<button class="button" data-action="close-dialog">取消</button><button class="button primary" id="cs-refine-submit">准备精修请求</button>`);
      document.getElementById('cs-refine-submit').onclick=()=>{
        const value=document.getElementById('cs-refine-prompt').value.trim();
        if(!value){ui.toast('先写下希望修改的内容');return;}
        ui.dialog('单张精修输入已准备',`<div class="note">原型未调用图片模型，未产生新版本。</div><pre>${e(JSON.stringify({preview_only:true,operation:'edit',reference:`candidate_${n+1}`,use_original_external_reference:false,user_prompt:value,siblings_unchanged:true},null,2))}</pre>`);
      };return;
    }
  }
  function handleChange(event) {
    const t=event.target;
    if(t.id==='cs-file'){upload(t.files[0]);return;}
    if(t.id==='cs-mode'){invalidate();state.mode=t.value;save();draw();return;}
    if(t.dataset.coverFacet){invalidate();state.keep=t.checked?[...new Set([...state.keep,t.dataset.coverFacet])]:state.keep.filter(v=>v!==t.dataset.coverFacet);save();draw();}
  }
  function mount(element, nextContext, helpers) {
    controller?.abort();controller=new AbortController();root=element;context=nextContext;ui=helpers;
    root.innerHTML='<div data-cover-root></div>';
    root.addEventListener('click',handleClick,{signal:controller.signal});
    root.addEventListener('change',handleChange,{signal:controller.signal});
    root.addEventListener('input',event=>{
      if(event.target.id!=='cs-preference')return;
      invalidate();state.preference=event.target.value;save();
      root.querySelector('.cs-plan-result')?.remove();
      const planButton=root.querySelector('[data-cover-action=plan]');
      if(planButton)planButton.textContent=state.ref?.kind==='upload'||!context.demoSupported?'准备规划输入':'规划 4 个方向 · 演示';
    },{signal:controller.signal});
    draw();restoreAsset();
  }
  function handoff(isRewrite, includeImage=true) {
    state.mode=isRewrite?'adaptation':'inspiration';
    if(includeImage)sample();
    else { ticket++;busy=false;loading=false;invalidate();state.ref=null;asset=null;if(url)URL.revokeObjectURL(url);url=''; }
    state.tab='design';save();
  }
  function reset() { ticket++;state=fresh();asset=null;loading=false;busy=false;message='';if(url)URL.revokeObjectURL(url);url='';save(); }
  return { mount, handoff, reset };
})();
