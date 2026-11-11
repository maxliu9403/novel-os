(() => {
  'use strict';
  const fixture = window.routeFixture;
  const main = document.getElementById('main');
  const dock = document.getElementById('dock');
  const dialog = document.getElementById('detail-dialog');
  const state = {selected:null, scenario:'ready', screen:'routes', detail:null, detailTab:'emotion', drafts:{}, sent:[], handoff:null};
  let toastTimer;
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const route = id => fixture.routes.find(item => item.id === id);
  const tagName = id => fixture.tags.find(item => item.id === id)?.name || id;
  function draft(id) {
    if (!state.drafts[id]) state.drafts[id] = {audience:'female',genre:'genre.romance',tags:[...route(id).tags],note:''};
    return state.drafts[id];
  }
  const valid = value => !!value && ['female','male'].includes(value.audience) && ['genre.romance','genre.mystery'].includes(value.genre) && value.tags.length >= 1 && value.tags.length <= 2;
  const genreName = id => id === 'genre.romance' ? 'Romance · 浪漫' : 'Mystery · 悬疑';
  function toast(message) {
    clearTimeout(toastTimer);
    const el = document.getElementById('toast'); el.textContent = message; el.classList.add('show');
    toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
  }
  function sourceContext() {
    return `<section class="context-strip" aria-label="全部资料的共同承接点"><div class="context-icon">书</div><div><h2>这三个故事，都从同一个地方继续</h2><p>${esc(fixture.source.cutoff)}</p><details><summary>查看前六章共同事实与尚未揭晓的线索</summary><ul>${fixture.source.facts.map(f => `<li>${esc(f)}</li>`).join('')}</ul><p>${esc(fixture.source.sourceNotice)}</p></details></div><span class="chapter-count">样例 · 6 / 6 章</span></section>`;
  }
  function card(item) {
    const selected = item.id === state.selected;
    return `<article class="route-card${selected ? ' selected' : ''}" aria-labelledby="title-${item.id}"><div class="card-top"><span class="route-letter">${item.letter}</span><span class="route-label">${esc(item.label)}</span>${item.recommended ? '<span class="badge">建议方向</span>' : ''}</div><h3 id="title-${item.id}">${esc(item.title)}</h3><p class="premise">${esc(item.premise)}</p><div class="reader-feeling"><span class="field-label">读者会经历什么</span><p>${esc(item.emotion)}</p></div><div class="highlight-list"><span class="field-label">值得期待的场面</span>${item.highlights.map(text => `<p>${esc(text)}</p>`).join('')}</div><p class="payoff">${esc(item.payoff)}</p><div class="tag-line"><span class="chip">Romance</span>${item.tags.map(id => `<span class="chip">${esc(tagName(id))}</span>`).join('')}</div><div class="card-actions"><button class="link-detail" data-detail="${item.id}" aria-haspopup="dialog">看完整走向与情绪设计 ↗</button><button class="button${selected ? ' chosen' : ''}" data-select="${item.id}" aria-pressed="${selected}">${selected ? '✓ 已选方向' : '选这条路线'}</button></div></article>`;
  }
  function renderConfiguration() {
    if (!state.selected) return '';
    const item = route(state.selected), value = draft(item.id);
    const changed = value.genre !== 'genre.romance' || value.audience !== 'female' || value.tags.join('|') !== item.tags.join('|');
    return `<section class="configuration" id="configuration" aria-label="调整所选路线" tabindex="-1"><div class="configuration-head"><div><span class="eyebrow">方向 ${item.letter} · ${esc(item.title)}</span><h2 style="margin-top:6px">题材由模型建议，选择由你决定</h2></div><button class="text-button" id="reset-recommendation">恢复这条路线的推荐</button></div><p class="subtle" style="margin-bottom:20px">${changed ? '你已调整推荐。头脑风暴将采用下面的实际选择，并结合你的意见继续设计。' : '推荐已预填；点击进入头脑风暴时采用当前选择。路线细节仍可继续修改。'}</p><div class="config-grid"><div><fieldset><legend>读者方向</legend><div class="radio-row"><label><input type="radio" name="audience" value="female" ${value.audience === 'female' ? 'checked' : ''}> 女频</label><label><input type="radio" name="audience" value="male" ${value.audience === 'male' ? 'checked' : ''}> 男频</label></div></fieldset><label for="genre">Genre · 选择 1 个</label><select id="genre"><option value="genre.romance" ${value.genre === 'genre.romance' ? 'selected' : ''}>Romance · 浪漫</option><option value="genre.mystery" ${value.genre === 'genre.mystery' ? 'selected' : ''}>Mystery · 悬疑（演示切换）</option></select></div><div><fieldset><legend>Tags · 选择 1–2 个</legend><div class="tag-options">${fixture.tags.map(tag => `<label class="tag-option ${value.tags.includes(tag.id) ? 'selected' : ''}"><input type="checkbox" name="tag" value="${tag.id}" ${value.tags.includes(tag.id) ? 'checked' : ''}>${esc(tag.name)}</label>`).join('')}</div><p id="tag-error" class="soft-warning" role="status">${value.tags.length ? '' : '请选择至少 1 个 Tag，已填写的意见会保留。'}</p></fieldset></div></div><div class="config-note"><strong>原推荐为什么适合：</strong>${esc(item.tagReason)}<br><small>原型使用独立样例目录；正式产品只展示当前可用的 Genre / Tags。改变题材后，模型会结合你的新选择深化，不会偷偷恢复推荐。</small></div><label class="notes-area"><span>我想保留或改变的地方 <small class="subtle">（选填）</small></span><textarea id="author-note" rows="3" placeholder="例如：保留她重新掌握选择的结局，但把中段的情绪拉扯做得更强。">${esc(value.note)}</textarea></label><p class="subtle" style="margin-top:9px">选择路线是采用讨论方向；具体设想仍待共创，不会被当成原文事实。每条路线的调整分别保留。</p></section>`;
  }
  function renderSpecial() {
    if (state.scenario === 'legacy') {
      return `<div class="notice"><strong>这份旧分析和提案仍然完整可用</strong>旧结果是一条路线的连续剧情单元。它不会被自动拆成多个方案；历史讨论与已启动作品保持原样。</div><section class="empty-state"><span class="eyebrow">旧版单路线提案 · 历史版本</span><h2 style="margin-top:12px">迟到的信，让两人重新面对过去</h2><p>宁遥追问信件来源，与陆闻共同修复书店。两人在旧事逐渐清楚后重新决定关系。原提案中的三个剧情单元及其结尾设计仍可展开查看。</p><details><summary>查看已保存的三个剧情单元</summary><ol class="detail-source-list"><li>门口的信：她要求完整解释。</li><li>修复的旧窗：协作带来短暂靠近。</li><li>新的邀请：她决定是否重新开始。</li></ol></details><div class="empty-actions"><button class="button primary" id="generate-routes-demo">基于全部资料生成整书路线</button><button class="button" id="continue-legacy-demo">沿用旧提案继续讨论</button></div><p class="subtle" style="margin:16px 0 0">生成新路线会保存为新版本；旧分析、旧提案与既有讨论不会被替换。</p></section>`;
    }
    return `<section class="empty-state"><span class="eyebrow">资料阅读 · 尚未完成</span><h2 style="margin-top:12px">读完已提供资料，再提出完整路线</h2><p>已保存前 4 章的阅读结果。第 5–6 章尚未完成，因此目前没有可选择的整书路线。未完成范围不会被当成已分析。</p><div class="progress-lines"><div class="done"><i></i> 第 1–4 章 · 阅读结果已保存</div><div class="working"><i></i> 第 5–6 章 · 待继续</div><div><i></i> 整理共同终点与未解线索</div><div><i></i> 生成不同的完整故事路线</div></div><div class="empty-actions"><button class="button primary" id="resume-reading-demo">继续已保存的阅读</button><button class="button" id="view-read-demo">查看已读范围</button></div><p class="subtle" style="margin:16px 0 0">本状态为交互示意；按钮不会读取资料或请求模型。</p></section>`;
  }
  function renderRoutes() {
    document.getElementById('breadcrumb').innerHTML = '参考资料 <span>/</span> 潮汐来信 <span>/</span> 整书路线';
    const special = ['legacy','partial'].includes(state.scenario);
    let notice = '';
    if (state.scenario === 'generating') notice = '<div class="notice"><strong>正在形成新的整书路线 · 上一版仍可选择</strong>新一轮设计不会覆盖当前三个方案。完成后可以比较新旧版本，再决定采用哪一版。<br><button class="text-button" id="generation-details">查看这一轮进度</button></div>';
    if (state.scenario === 'failed') notice = '<div class="notice warning"><strong>新一轮路线尚未完成 · 已保存结果保留</strong>当前展示上一版完整路线，仍可直接带入头脑风暴。<br><button class="text-button" id="resume-routes-demo">继续未完成的设计</button></div>';
    main.innerHTML = `<span class="eyebrow">资料分析 → 整书设想</span><div class="heading-line"><h1>从这里，选一个值得继续的故事。</h1><span class="subtle">《潮汐来信》 · ${special ? '状态演示' : '6 章资料 · 分析版本 01'}</span></div><p class="intro">先看读者为什么在意、过程中期待什么、结局获得什么。选定方向后，与模型一起把故事打磨成完整骨架。</p>${special ? renderSpecial() : `${notice}${sourceContext()}<div class="section-title"><h2>三条完整故事路线</h2><span class="subtle">从共同起点，走向不同的高潮与结局</span></div><div class="route-grid">${fixture.routes.map(card).join('')}</div><div class="recommendation"><strong>为什么建议 A</strong><span>${esc(fixture.routes[0].reason)} 推荐不代表已经替你选择。</span></div>${renderConfiguration()}`}`;
    renderDock();
    bindRoutes();
  }
  function renderDock() {
    if (state.screen === 'workshop') {
      dock.innerHTML = '<div class="dock-copy">已进入共创方向预览<small>这是交互设计稿，未创建讨论，也未发送模型请求。</small></div><div class="dock-actions"><button class="button" id="back-routes-dock">返回路线比较</button><button class="button primary" id="skeleton-demo">查看完整骨架的下一步</button></div>';
      document.getElementById('back-routes-dock').onclick = backToRoutes;
      document.getElementById('skeleton-demo').onclick = () => toast('正式流程：讨论完善后，由你明确发起“生成完整骨架”。本原型不生成骨架。');
      return;
    }
    const usable = !['legacy','partial'].includes(state.scenario);
    const item = usable && state.selected ? route(state.selected) : null;
    const value = item ? draft(item.id) : null;
    dock.innerHTML = `<div class="dock-copy">${item ? `已选方向 ${item.letter} · ${esc(item.title)}` : usable ? '先选择一条你想继续发展的路线' : state.scenario === 'legacy' ? '旧结果保留，可继续旧提案或生成新的整书路线' : '阅读完成后，才能形成基于全部资料的整书路线'}<small>${item ? '路线、题材和你的修改意见会一起带入；随后仍可调整。' : '模型建议不会自动成为作者的选择。'}</small></div><div class="dock-actions">${item ? '<button class="text-button" id="edit-settings-dock">调整题材与意见</button>' : ''}<button class="button primary" id="enter-workshop" ${item && valid(value) ? '' : 'disabled'}>带入头脑风暴 <span aria-hidden="true">→</span></button></div>`;
    if (item) document.getElementById('edit-settings-dock').onclick = () => document.getElementById('configuration').scrollIntoView({behavior:'smooth',block:'center'});
    document.getElementById('enter-workshop').onclick = enterWorkshop;
  }
  function selectRoute(id, focus=false) {
    const hadSelection = state.selected;
    state.selected = id; draft(id); renderRoutes();
    toast(hadSelection && hadSelection !== id ? '已切换方向；每条路线的题材调整与意见分别保留。' : '已选为讨论方向。可以调整题材并补充你的想法。');
    if (focus) {document.getElementById('configuration').scrollIntoView({behavior:'smooth',block:'start'});document.getElementById('configuration').focus({preventScroll:true});}
  }
  function bindRoutes() {
    main.querySelectorAll('[data-select]').forEach(button => button.onclick = () => selectRoute(button.dataset.select));
    main.querySelectorAll('[data-detail]').forEach(button => button.onclick = () => {state.detail=button.dataset.detail;state.detailTab='emotion';renderDetail();dialog.showModal();});
    if (state.selected && document.getElementById('configuration')) {
      const value = draft(state.selected);
      document.getElementById('author-note').oninput = event => value.note=event.target.value;
      main.querySelectorAll('input[name=audience]').forEach(input => input.onchange = event => {value.audience=event.target.value;refreshConfigHeader();renderDock();});
      document.getElementById('genre').onchange = event => {value.genre=event.target.value;refreshConfigHeader();renderDock();};
      main.querySelectorAll('input[name=tag]').forEach(input => input.onchange = event => {
        const id=event.target.value;
        if (event.target.checked && value.tags.length>=2) {event.target.checked=false;toast('最多选择 2 个 Tag。可先取消一个，再选择新的。');return;}
        value.tags=event.target.checked?[...value.tags,id]:value.tags.filter(t=>t!==id);
        event.target.closest('label').classList.toggle('selected',event.target.checked);
        document.getElementById('tag-error').textContent=value.tags.length?'':'请选择至少 1 个 Tag，已填写的意见会保留。';refreshConfigHeader();renderDock();
      });
      document.getElementById('reset-recommendation').onclick=()=>{const note=value.note;state.drafts[state.selected]={audience:'female',genre:'genre.romance',tags:[...route(state.selected).tags],note};renderRoutes();document.getElementById('configuration').scrollIntoView({block:'center'});toast('已恢复此路线的推荐；你的修改意见保留。');};
    }
    const actions={
      'generate-routes-demo':()=>toast('正式产品将保留旧分析并创建新路线版本。本原型不会启动生成。'),
      'continue-legacy-demo':()=>toast('正式产品沿用原提案与分析版本创建讨论，不强制升级；本原型不创建讨论。'),
      'resume-reading-demo':()=>toast('正式产品会从未完成范围继续，保留已有阅读结果；本原型不发起请求。'),
      'view-read-demo':()=>toast('样例仅已读第 1–4 章。不能把未读第 5–6 章列为已覆盖。'),
      'generation-details':()=>toast('状态示意：原文与已完成路线保留，正在设计新版本的结局与读者回报。'),
      'resume-routes-demo':()=>toast('正式产品会复用已完成设计继续；本原型不会重试或调用模型。')
    };
    Object.entries(actions).forEach(([id,fn])=>{const el=document.getElementById(id);if(el)el.onclick=fn;});
  }
  function refreshConfigHeader() {
    const item=route(state.selected),value=draft(item.id);
    const changed=value.genre!=='genre.romance'||value.audience!=='female'||value.tags.join('|')!==item.tags.join('|');
    document.querySelector('#configuration > .subtle').textContent=changed?'你已调整推荐。头脑风暴将采用下面的实际选择，并结合你的意见继续设计。':'推荐已预填；点击进入头脑风暴时采用当前选择。路线细节仍可继续修改。';
  }
  function detailPanel(item) {
    if(state.detailTab==='emotion')return `<h3>读者为什么会在意，又为什么愿意读下去</h3><p class="subtle">情绪由事件、人物选择和代价产生。这里展示主要情绪推进，不预设每章必须达到某种情绪或分数。</p>${item.emotionalBeats.map(b=>`<section class="emotional-beat"><span class="beat-phase">${esc(b.phase)}</span><div class="beat-main"><h4>${esc(b.want)}</h4><span class="emotion-word">${esc(b.feeling)}</span><div class="beat-grid"><div><span class="field-label">通过怎样的场面发生</span><p>${esc(b.event)}</p></div><div><span class="field-label">读者得到什么回报</span><p>${esc(b.reward)}</p></div></div></div></section>`).join('')}`;
    if(state.detailTab==='arc')return `<h3>一个完整的故事闭环</h3><p class="subtle">以下是主要因果走向。具体章节、场景长度与对白将在共创中展开。</p><div class="story-arc">${item.arc.map(([title,text])=>`<article><div><h4>${esc(title)}</h4><p>${esc(text)}</p></div></article>`).join('')}</div><div class="notice" style="margin-bottom:0"><strong>这条路线的取舍</strong>${esc(item.tradeoff)}</div>`;
    if(state.detailTab==='promises')return `<h3>让期待有去处，让结局有回声</h3><p class="subtle">这里规划全书关键吸引点、揭示与回报，不是提前列出每章的结尾。</p>${item.promises.map(([question,payoff])=>`<section class="promise"><h4>${esc(question)}</h4><p><strong>如何推进与兑现：</strong>${esc(payoff)}</p></section>`).join('')}`;
    return `<h3>共同原文进度</h3><p style="font-size:13px;line-height:1.9;margin-top:12px">${esc(fixture.source.cutoff)}</p><ul class="detail-source-list">${fixture.source.facts.map(f=>`<li>${esc(f)}</li>`).join('')}</ul><h3 style="margin-top:25px">这条路线新增的设想 · 待共创</h3><ul class="detail-source-list">${item.assumptions.map(f=>`<li>${esc(f)}</li>`).join('')}</ul><p class="subtle">选定方向不把新增设想变成原文事实，也不冻结每个细节。全页面均为虚构样例，未读取你的真实资料。</p>`;
  }
  function renderDetail() {
    const item=route(state.detail);
    const tabs=[['emotion','情绪与看点'],['arc','完整故事走向'],['promises','全书悬念与兑现'],['source','原文与新设想']];
    document.getElementById('detail-content').innerHTML=`<header class="detail-head"><span class="eyebrow">整书路线 ${item.letter} · ${esc(item.label)}</span><h2 id="detail-title">${esc(item.title)}</h2><p>${esc(item.premise)}</p><button class="close-dialog" id="close-detail" aria-label="关闭路线详情">×</button></header><div class="detail-tabs" role="tablist" aria-label="路线详情内容">${tabs.map(([key,label])=>`<button class="detail-tab" id="tab-${key}" role="tab" aria-selected="${state.detailTab===key}" aria-controls="detail-panel" tabindex="${state.detailTab===key?0:-1}" data-tab="${key}">${label}</button>`).join('')}</div><section class="detail-body" id="detail-panel" role="tabpanel" aria-labelledby="tab-${state.detailTab}">${detailPanel(item)}</section><footer class="detail-footer"><small>这是待共创的整书设想。<br>选择方向后，仍可修改剧情和题材。</small><button class="button primary" id="choose-detail">${state.selected===item.id?'继续调整这条路线':'选这条路线，继续调整'} →</button></footer>`;
    document.getElementById('close-detail').onclick=()=>dialog.close();
    document.getElementById('choose-detail').onclick=()=>{dialog.close();selectRoute(item.id,true);};
    dialog.querySelectorAll('[data-tab]').forEach(button=>{
      button.onclick=()=>{state.detailTab=button.dataset.tab;renderDetail();document.getElementById(`tab-${state.detailTab}`).focus();};
      button.onkeydown=event=>{const keys=tabs.map(t=>t[0]);const index=keys.indexOf(state.detailTab);const next=event.key==='ArrowRight'?keys[(index+1)%keys.length]:event.key==='ArrowLeft'?keys[(index+keys.length-1)%keys.length]:event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):null;if(next){event.preventDefault();state.detailTab=next;renderDetail();document.getElementById(`tab-${next}`).focus();}};
    });
  }
  function enterWorkshop() {
    if(!state.selected||!valid(draft(state.selected))||['legacy','partial'].includes(state.scenario))return;
    state.handoff={routeId:state.selected,...JSON.parse(JSON.stringify(draft(state.selected)))};
    state.screen='workshop';state.sent=[];renderWorkshop();window.scrollTo({top:0,behavior:'smooth'});main.focus({preventScroll:true});
  }
  function backToRoutes(){state.screen='routes';renderRoutes();window.scrollTo({top:0,behavior:'smooth'});main.focus({preventScroll:true});}
  function renderWorkshop(){
    const value=state.handoff,item=route(value.routeId);
    document.getElementById('breadcrumb').innerHTML='头脑风暴 <span>/</span> 从资料路线继续 <span>/</span> 设计预览';
    main.innerHTML=`<span class="eyebrow">已选方向 → 共创完整骨架</span><div class="heading-line"><h1>方向带进来了，我们从这里往前走。</h1><button class="text-button" id="back-routes">返回比较路线</button></div><p class="intro">资料、选中的方向和你的修改意见分别保留。接下来的讨论聚焦人物选择、情绪铺垫、关键场面与结局兑现。</p><div class="notice"><strong>原型交接预览 · 没有创建真实讨论</strong>实际产品将创建可恢复的讨论并发起首轮共创。下面的回应只是预先编写的界面示意，不是模型已执行的结果。</div><div class="workshop-layout"><section class="discussion"><div class="speaker">首轮共创的展开示意</div><h2>先把最值得期待的情绪，落到人物的选择里。</h2><p>我们以方向 ${item.letter}「${esc(item.title)}」为讨论起点，原文仍停留在那封信出现的时刻。路线中的新增事件会作为待完善设想，不写入原文事实。</p><p>这一轮可以先深化三个关键部分：</p><ul><li>让读者在意她：${esc(item.emotionalBeats[0].event)}</li><li>让中段关系发生真实变化：${esc(item.emotionalBeats[1].event)}</li><li>让最后的回报值得等待：${esc(item.emotionalBeats.at(-1).reward)}</li></ul><p>当前选择是 <strong>${esc(genreName(value.genre))}</strong>，Tags 为 <strong>${esc(value.tags.map(tagName).join(' + '))}</strong>。正式首轮会依照这些实际选择，以及你写下的修改意见展开；这里尚未执行新的推演。</p><div class="workshop-compose"><label class="notes-area"><span>继续提出你的想法</span><textarea id="discussion-note" rows="4" placeholder="例如：我喜欢结局的主动选择，但希望中段有一个更让人心疼、又不靠误会拖延的场面。"></textarea></label><div class="compose-actions"><span class="subtle">这里是模拟交互，不会发送模型请求。</span><button class="button primary" id="simulate-send">模拟保存这条意见</button></div><div id="sent-notes"></div></div></section><aside class="handoff-summary"><h3>已选方向</h3><span class="eyebrow">${item.letter} · ${esc(item.title)}</span><p>《潮汐来信》 · 全部六章样例资料<br>分析版本 01 · 路线版本 01</p><h3 style="margin-top:22px">待完善的模型设想</h3><p>${esc(item.premise)}</p><p>${esc(item.payoff)}</p><p>采用讨论方向，不等于每个设想都已确定。</p><h3 style="margin-top:22px">我的实际题材选择</h3><p>${value.audience==='female'?'女频':'男频'} · ${esc(genreName(value.genre))}<br>${esc(value.tags.map(tagName).join(' / '))}</p><h3 style="margin-top:22px">我的修改意见</h3>${value.note?`<blockquote>${esc(value.note)}</blockquote>`:'<p>尚未补充。可以直接在共创中提出。</p>'}<p class="subtle" style="margin-top:22px">原文事实、模型提案与作者输入分别保存；未选路线不成为创作要求。</p></aside></div>`;
    document.getElementById('back-routes').onclick=backToRoutes;
    document.getElementById('simulate-send').onclick=()=>{const input=document.getElementById('discussion-note');const text=input.value.trim();if(!text){toast('先写下一点想法，再模拟保存。');input.focus();return;}state.sent.push(text);document.getElementById('sent-notes').innerHTML=state.sent.map(note=>`<div class="sent-note"><small>我的意见 · 原型中暂存，未发送</small><p>${esc(note)}</p></div>`).join('');input.value='';toast('已在本页暂存；没有调用模型。');};
    renderDock();
  }
  document.getElementById('scenario').onchange=event=>{state.scenario=event.target.value;state.screen='routes';dialog.close();renderRoutes();toast('已切换设计状态；你的路线调整仍保留在本页。');};
  renderRoutes();
})();
