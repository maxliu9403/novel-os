(() => {
  const source = window.NOVEL_SKELETON;
  const navigation = document.querySelector('#skeleton-navigation');
  const content = document.querySelector('#skeleton-content');
  const pane = document.querySelector('#skeleton-reading-pane');
  if (!source) {
    content.textContent = '本地骨架快照未载入，请使用包含私有素材的完整设计稿。';
    return;
  }
  const groups = [
    ['故事基础', [1, 2, 3, 4]],
    ['人物与关系', [5, 6]],
    ['世界与因果', [7, 8, 9, 10]],
    ['阅读体验与章节', [11, 12]],
    ['结局与写法', [13, 14]],
    ['出版与交付', [15, 16, 17]],
  ];
  const labels = ['创作规格', '读者与市场', '完整故事梗概', '硬性限制', '人物档案', '关系与变化', '世界与时间线', '秘密与证据链', '全书结构', '伏笔与回收', '开篇与阅读回报', '20 章完整细纲', '结局与人物归宿', '文风与写作规范', '封面与交付', '假设与选定决策', '交付前检查'];
  const escapeHTML = (text) => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const inline = (text) => escapeHTML(text).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const sourceNote = (section) => `<div class="skeleton-source-note">采用骨架 · 第 ${section.number} 节 · 原文 ${section.lineStart}–${section.lineEnd} 行 <button class="text-button" data-story-open="raw">查看原文</button></div>`;

  // Limited local Markdown display: no remote images, links, HTML or execution.
  function renderMarkdown(text) {
    const lines = text.split('\n');
    const output = [];
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      if (!line.trim()) continue;
      const protocol = line.match(/^\[([A-Z_]+)\]$/);
      if (protocol || line.startsWith('```')) {
        const block = [line];
        const end = protocol ? `[/${protocol[1]}]` : '```';
        while (++i < lines.length) { block.push(lines[i]); if (lines[i].trim() === end) break; }
        output.push(`<details class="skeleton-protocol"><summary>${protocol ? inline(protocol[1]) : '结构化内容'} · 展开原文</summary><pre>${escapeHTML(block.join('\n'))}</pre></details>`);
      } else if (/^#{1,4} /.test(line)) {
        output.push(`<h3>${inline(line.replace(/^#+ /, ''))}</h3>`);
      } else if (line.trim().startsWith('|')) {
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i]); i += 1; }
        i -= 1;
        const cells = row => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim());
        const actual = rows.filter(row => !cells(row).every(cell => /^:?-+:?$/.test(cell.replace(/\s/g, ''))));
        output.push('<div class="skeleton-table-scroll"><table>' + actual.map((row, index) => '<tr>' + cells(row).map(cell => `<${index ? 'td' : 'th'}>${inline(cell)}</${index ? 'td' : 'th'}>`).join('') + '</tr>').join('') + '</table></div>');
      } else if (/^\s*[-*] /.test(line)) {
        output.push(`<p class="skeleton-bullet">${inline(line.replace(/^\s*[-*] /, ''))}</p>`);
      } else if (!/^---+$/.test(line.trim())) {
        output.push(`<p>${inline(line)}</p>`);
      }
    }
    return output.join('');
  }

  const navButton = (id, label, number = '') => `<button type="button" data-story-section="${id}"><span>${number}</span>${label}</button>`;
  navigation.innerHTML = navButton('overview', '全书总览', '◎') + groups.map(([group, numbers]) => `<div class="skeleton-nav-group"><p>${group}</p>${numbers.map(n => navButton(`section-${n}`, labels[n - 1], String(n).padStart(2, '0'))).join('')}</div>`).join('') + navButton('raw', '完整原文', '↗');
  document.querySelector('#skeleton-size').textContent = `${source.bytes.toLocaleString()} 字节 · 原文已载入`;

  function renderOverview() {
    content.innerHTML = `<div class="skeleton-content-head"><div><span class="eyebrow">全书总览</span><h3>她要离开的，是以照顾为名的控制</h3></div><span class="prototype-chip">中文导读</span></div>
      <p class="skeleton-intro">Riley 逃离前男友的背叛与家人的金钱算计，却在雪崩封路的木屋中再次陷入依赖。Kaleb 的能力与温柔逐步变成债务、公开标签和控制。她最终拒绝两人替自己定义的未来，建立属于自己的生活。</p>
      <div class="skeleton-facts"><div><span>创作规格</span><b>20 章 · 17,000 目标英文词</b></div><div><span>叙事视角</span><b>Riley · 第一人称 · 现在时</b></div><div><span>读者定位</span><b>美国 50–65 岁女性读者</b></div><div><span>核心题材</span><b>女性成长 · 家庭剧情 · 幸存者叙事</b></div></div>
      <div class="skeleton-section-label"><h4>全书走向</h4><button class="text-button" data-story-open="section-3">阅读完整梗概 →</button></div>
      <div class="skeleton-arc">
        <article><span>起点 · 离开</span><h4>从背叛中寻找喘息</h4><p>保存 Dane 背叛的证据，接受 Lily 的木屋邀请；雪崩让离开失去现实条件。</p></article>
        <article><span>困局 · 识别</span><h4>照顾逐渐成为控制</h4><p>生存依赖、公开关系标签和边界侵犯叠加，Riley 开始记录事实、盘点资源。</p></article>
        <article><span>高潮 · 命名</span><h4>拒绝别人替她定义</h4><p>独自求救，公开否认被强加的女友身份；面对两个男人，她问出 “Then where?”。</p></article>
        <article><span>结局 · 自主</span><h4>自己的房间与生活</h4><p>承担自身隐瞒的复杂性，换宿舍、工作、建立边界；结尾的雪终于只是雪。</p></article>
      </div>
      <div class="skeleton-section-label"><h4>深入设计</h4><span>点击查看完整模块</span></div>
      <div class="skeleton-depth-grid">
        <button data-story-open="section-6"><strong>13</strong><span>组人物关系</span><small>权力、信任、误解与变化 →</small></button>
        <button data-story-open="section-8"><strong>14</strong><span>条秘密与证据链</span><small>谁知道什么，何时被揭示 →</small></button>
        <button data-story-open="section-10"><strong>23</strong><span>条伏笔与回收</span><small>埋设、揭示与兑现 →</small></button>
        <button data-story-open="section-12"><strong>20</strong><span>章完整细纲</span><small>逐章因果、选择与钩子 →</small></button>
      </div>
      <div class="skeleton-guardrail"><strong>作者已确认的边界</strong><p>这是一部关于识别控制、重建自主的故事。骨架明确排除 Kaleb 与 Riley 的爱情圆满结局；人物的矛盾与道德复杂性要被保留。</p><button class="text-button" data-story-open="section-4">查看 15 条硬性限制 →</button></div>
      <div class="skeleton-source-note">以上为完整骨架的中文导读。目录内各模块展示原文；目标词数与已完成正文词数分别呈现。</div>`;
  }

  function show(id, focus = false) {
    if (id === 'raw') {
      content.innerHTML = `<div class="skeleton-content-head"><div><span class="eyebrow">完整来源</span><h3>采用骨架原文</h3></div></div><p class="skeleton-hash">SHA-256: ${escapeHTML(source.sha256)}</p><pre class="skeleton-raw"></pre>`;
      content.querySelector('pre').textContent = source.raw;
    } else if (id === 'overview') {
      renderOverview();
    } else {
      const section = source.sections.find(section => section.id === id);
      if (!section) return show('overview', focus);
      content.innerHTML = `<div class="skeleton-content-head"><div><span class="eyebrow">骨架模块 ${String(section.number).padStart(2, '0')}</span><h3>${escapeHTML(section.title.replace(/^\d+[.．、]\s*/, ''))}</h3></div><span class="prototype-chip">完整原文</span></div>`;
      if (section.number === 12) {
        const chapters = section.text.split(/(?=^### Chapter \d+)/m).filter(text => /^### Chapter \d+/.test(text));
        content.innerHTML += `<p class="skeleton-intro">全部 ${chapters.length} 章。展开查看每章的完整设计；此处为采用骨架规划，已写正文在「章节」中阅读。</p><div class="skeleton-chapters">` + chapters.map((chapter, index) => {
          const [heading, ...body] = chapter.split('\n');
          return `<details ${index === 0 ? 'open' : ''}><summary><span>${String(index + 1).padStart(2, '0')}</span>${escapeHTML(heading.replace(/^### Chapter \d+\s*[—-]\s*/, ''))}</summary><div class="skeleton-prose">${renderMarkdown(body.join('\n'))}</div></details>`;
        }).join('') + '</div>';
      } else {
        const body = section.text.replace(/^##[^\n]*\n/, '');
        content.innerHTML += `<div class="skeleton-prose">${renderMarkdown(body)}</div>`;
      }
      content.innerHTML += sourceNote(section);
    }
    navigation.querySelectorAll('[data-story-section]').forEach(button => {
      const active = button.dataset.storySection === id;
      button.classList.toggle('is-active', active);
      if (active) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    });
    pane.scrollTop = 0;
    if (focus) pane.focus({preventScroll:true});
  }

  navigation.addEventListener('click', event => {
    const button = event.target.closest('[data-story-section]');
    if (button) show(button.dataset.storySection, true);
  });
  document.querySelector('#panel-story').addEventListener('click', event => {
    const button = event.target.closest('[data-story-open]');
    if (button) show(button.dataset.storyOpen, true);
  });
  show('overview');
})();
