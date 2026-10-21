async (page) => {
  const browser = page.context().browser();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const qa = await context.newPage();
  const errors = [], writes = [], failed = [];
  qa.on('pageerror', e => errors.push(String(e)));
  qa.on('request', r => { if (!['GET','HEAD','OPTIONS'].includes(r.method())) writes.push({method:r.method(),url:r.url()}); });
  qa.on('response', r => { if (r.status() >= 400) failed.push({status:r.status(),url:r.url()}); });
  const root = '/Users/max/OpenSource/Novel-OS/.playwright-mcp/composition-framework-20261011';
  try {
    await qa.goto('http://localhost:5174/projects/my-husband-s-secret-daughter-lived-across-the-street-d7c6589c/covers');
    await qa.waitForLoadState('networkidle');
    await qa.getByRole('region', {name:'主要评估问题',exact:true}).first().waitFor();
    const gallery = qa.getByRole('region', {name:'封面候选画廊'});
    const cards = gallery.locator('article');
    if (await cards.count() !== 4) throw new Error('Expected four unchanged candidates');
    const summaries = await qa.getByRole('region', {name:'主要评估问题',exact:true}).allTextContents();
    const reliability = await qa.getByRole('region', {name:'评估依据可靠性',exact:true}).allTextContents();
    if (summaries.length !== 4 || !summaries.every(s=>s.includes('核心冲突不清晰'))) throw new Error('Missing visible blocker summary');
    if (!summaries[1].includes('组合图层关系不清晰')) throw new Error('Composite failure is hidden');
    if (reliability.filter(s=>s.includes('无法确认盲读未受文字影响')).length !== 3) throw new Error('Historical uncertainty hidden');
    if (reliability.filter(s=>s.includes('已独立观察无标题画稿')).length !== 1) throw new Error('Independent artwork observation missing');
    const imageResults = await cards.locator('.cover-candidate__artwork img').evaluateAll(images=>images.map(i=>({complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})));
    if (imageResults.some(i=>!i.complete || !i.width)) throw new Error('Candidate image failed to load');
    await qa.setViewportSize({width:1440,height:2000});
    await gallery.scrollIntoViewIfNeeded();
    await gallery.screenshot({path:root+'/desktop-gallery.png'});
    await cards.first().getByText('查看故事与画面评估',{exact:true}).click();
    const evidence = cards.first().getByRole('region',{name:'具体证据与问题'});
    await evidence.waitFor();
    if (!(await evidence.innerText()).includes('未提供对应的单独视觉证据')) throw new Error('Derived blocker lacks explicit evidence limitation');
    await evidence.screenshot({path:root+'/desktop-evidence.png'});
    await cards.first().getByText('查看故事与画面评估',{exact:true}).click();
    await qa.setViewportSize({width:390,height:1800});
    await cards.first().scrollIntoViewIfNeeded();
    await cards.first().screenshot({path:root+'/mobile-candidate.png'});
    await qa.setViewportSize({width:390,height:844});
    await cards.first().getByRole('region',{name:'评估依据可靠性'}).scrollIntoViewIfNeeded();
    await qa.screenshot({path:root+'/mobile-review.png'});
    const geometry = await qa.evaluate(()=>({viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
      cards:[...document.querySelectorAll('.cover-candidate')].map(n=>({width:n.getBoundingClientRect().width,left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right}))}));
    if (geometry.scrollWidth > geometry.viewport || geometry.cards.some(c=>c.left<0 || c.right>geometry.viewport)) throw new Error('Mobile horizontal overflow');
    if (writes.length || errors.length || failed.length) throw new Error(JSON.stringify({writes,errors,failed}));
    return {candidateCount:4,summaries,reliability,imageResults,geometry,errors,writes,failed,
      screenshots:['desktop-gallery.png','desktop-evidence.png','mobile-candidate.png','mobile-review.png']};
  } finally {
    await context.close();
    const cdp = await browser.newBrowserCDPSession();
    try { await cdp.send('Browser.setDownloadBehavior',{behavior:'default',eventsEnabled:false}); }
    finally { await cdp.detach(); }
  }
}
