/* Local prototype assets only. No upload endpoint or model request. */
window.CoverAssets = (() => {
  const LIMIT = 12 * 1024 * 1024;
  const memory = new Map();
  let dbPromise;
  function database() {
    if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open('novelos.cover-reference.prototype', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('assets', { keyPath: 'id' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return dbPromise;
  }
  async function read(id) {
    if (memory.has(id)) return memory.get(id);
    try {
      const db = await database();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction('assets');
        const r = tx.objectStore('assets').get(id);
        r.onsuccess = () => resolve(r.result || null);
        r.onerror = () => reject(r.error);
      });
    } catch { return null; }
  }
  async function put(record) {
    memory.set(record.id, record);
    try {
      const db = await database();
      await new Promise((resolve, reject) => {
        const tx = db.transaction('assets', 'readwrite');
        tx.objectStore('assets').put(record);
        tx.oncomplete = resolve;
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
      return true;
    } catch { return false; }
  }
  async function inspect(file) {
    if (!file.size || file.size > LIMIT) throw new Error('请选择不超过 12 MiB 的 PNG 或 JPEG 图片。');
    const bytes = await file.arrayBuffer();
    const b = new Uint8Array(bytes);
    const png = [137,80,78,71,13,10,26,10].every((v,i) => b[i] === v);
    const jpeg = b[0] === 255 && b[1] === 216 && b[2] === 255;
    if (!png && !jpeg) throw new Error('文件内容不是 PNG/JPEG，原参考图保持不变。');
    const mime = png ? 'image/png' : 'image/jpeg';
    const blob = new Blob([bytes], { type: mime });
    const url = URL.createObjectURL(blob);
    let dimensions;
    try {
      dimensions = await new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
        image.onerror = () => reject(new Error('图片无法解码，原参考图保持不变。'));
        image.src = url;
      });
    } finally { URL.revokeObjectURL(url); }
    if (dimensions.width * dimensions.height > 40000000) throw new Error('原型参考图最多支持 4000 万像素，请换一张较小的图片。');
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const sha = [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, '0')).join('');
    const record = { id: sha, sha, name: file.name, mime, size: file.size, ...dimensions, blob };
    return { record, persisted: await put(record) };
  }
  return { inspect, read };
})();
