async (page) => {
  const browser=page.context().browser();
  const context=await browser.newContext({viewport:{width:1440,height:960}});
  const qa=await context.newPage();
  const errors=[],writes=[],failed=[];
  qa.on('pageerror',e=>errors.push(String(e)));
  qa.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),url:r.url()});});
  qa.on('response',r=>{if(r.status()>=400)failed.push({status:r.status(),url:r.url()});});
  const root='/Users/max/OpenSource/Novel-OS/.playwright-mcp/direction-status-20261011';
  const url='http://localhost:5174/projects/my-husband-s-secret-daughter-lived-across-the-street-d7c6589c/covers';
  try {
    await qa.goto(url);await qa.waitForLoadState('networkidle');
    const panel=qa.getByRole('region',{name:'美术方向任务状态'});
    await panel.waitFor();
    const source=qa.locator('.cover-source');
    const firstText=await panel.innerText();
    const box=await panel.boundingBox(),sourceBox=await source.boundingBox();
    if(!box || !sourceBox || box.y+box.height>sourceBox.y || box.y<0 || box.y+box.height>960)throw new Error('Status must be visible above the source panel');
    if(!firstText.includes('状态已同步'))throw new Error('Missing confirmation time');
    if(await panel.locator('[aria-live="off"]').count()!==1)throw new Error('Timer should not be a live announcement');
    await qa.screenshot({path:root+'/desktop.png'});
    await qa.goto('http://localhost:5174/projects/my-husband-s-secret-daughter-lived-across-the-street-d7c6589c');
    await qa.waitForLoadState('networkidle');
    await qa.goto(url);await qa.waitForLoadState('networkidle');
    await qa.getByRole('region',{name:'美术方向任务状态'}).waitFor();
    const restoredText=await panel.innerText();
    await qa.reload();await qa.waitForLoadState('networkidle');
    const refreshedText=await panel.innerText();
    await qa.setViewportSize({width:390,height:844});
    await panel.scrollIntoViewIfNeeded();
    await qa.screenshot({path:root+'/mobile.png'});
    const geometry=await qa.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
      panel:(()=>{const b=document.querySelector('.cover-direction-task').getBoundingClientRect();return {left:b.left,right:b.right,width:b.width}})()}));
    if(geometry.scrollWidth>geometry.width || geometry.panel.left<0 || geometry.panel.right>geometry.width)throw new Error('Mobile overflow');
    await qa.emulateMedia({reducedMotion:'reduce'});
    const spinner=panel.locator('.cover-direction-task__spinner');
    const reducedMotion=await spinner.count()?await spinner.evaluate(n=>getComputedStyle(n).animationName):'terminal';
    if(reducedMotion!=='none' && reducedMotion!=='terminal')throw new Error('Spinner ignores reduced motion');
    const live=await (await qa.request.get('http://localhost:5174/api/projects/my-husband-s-secret-daughter-lived-across-the-street-d7c6589c/covers/direction-tasks/latest')).json();
    if(errors.length || failed.length || writes.length)throw new Error(JSON.stringify({errors,failed,writes}));
    return {firstText,restoredText,refreshedText,geometry,reducedMotion,live,errors,failed,writes};
  } finally {
    await context.close();
    const c=await browser.newBrowserCDPSession();
    try {await c.send('Browser.setDownloadBehavior',{behavior:'default',eventsEnabled:false});}
    finally {await c.detach();}
  }
}
