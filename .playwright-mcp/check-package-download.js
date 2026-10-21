async (page) => {
  const button = page.getByRole('link', { name: '下载交付包' });
  const url = await button.getAttribute('href');
  const started = page.waitForEvent('download');
  await button.click();
  const download = await started;
  await download.path();
  const downloads = page.context().pages().find(p => p.url() === 'chrome://downloads/');
  if (!downloads) throw new Error('Open chrome://downloads/ before this probe');
  await downloads.reload();
  await downloads.locator('downloads-item').first().waitFor();
  const record = await downloads.evaluate((suffix) => {
    const items = document.querySelector('downloads-manager').shadowRoot.querySelectorAll('downloads-item');
    const data = Array.from(items).map(item => item.data).find(item => item.url.endsWith(suffix));
    return data && { fileName: data.fileName, filePath: data.filePath, url: data.url, state: data.state };
  }, url);
  const result = {
    suggestedFilename: download.suggestedFilename(),
    browserRecord: record,
    passed: Boolean(record && record.fileName.endsWith('.zip')),
    assertion: 'The actual Chrome download record retains the .zip extension',
  };
  console.log(JSON.stringify(result));
  if (!result.passed) throw new Error('Browser saved package without .zip: ' + JSON.stringify(result));
  return result;
}
