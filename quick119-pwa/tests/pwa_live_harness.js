(async () => {
  const result = document.getElementById('result');
  try {
    if (!window.isSecureContext) throw new Error('Loopback secure context missing');
    await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' });
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
    }
    const names = (await caches.keys()).filter(name => name.startsWith('quick119-static-'));
    if (names.length !== 1) throw new Error('Unexpected owned cache count');
    const cache = await caches.open(names[0]);
    const keys = async () => (await cache.keys()).map(request => new URL(request.url).pathname).sort();
    const initial = await keys();
    if (initial.length !== 10 || !initial.includes('/static/voice-intake.js') || initial.some(path => !path.startsWith('/static/'))) throw new Error('Unexpected precache');
    for (const path of ['/patients', '/voice-intake', '/emergency/report?patient_id=0', '/emergency/hospitals?latitude=37.5&longitude=127']) {
      const response = await fetch(path);
      if (response.headers.get('Cache-Control') !== 'no-store') throw new Error('Sensitive response HTTP cache policy');
    }
    if (JSON.stringify(await keys()) !== JSON.stringify(initial)) throw new Error('Dynamic request added to cache');
    let layouts = 0;
    for (const width of [360, 375, 390, 412, 430]) {
      for (const path of ['/', '/emergency?step=patient', '/voice-intake', '/pwa-check']) {
        const frame = document.createElement('iframe');
        frame.style.cssText = `display:block;width:${width}px;height:1100px;border:0`;
        const loaded = new Promise(resolve => { frame.onload = resolve; });
        frame.src = path;
        document.body.appendChild(frame);
        await loaded;
        const doc = frame.contentDocument;
        if (doc.documentElement.scrollWidth > width) throw new Error(`Horizontal overflow: ${path} at ${width}`);
        if (doc.querySelector('#pwa-check') && !doc.getElementById('check-secure').textContent.includes('예')) throw new Error('pwa.js did not run');
        layouts++;
        frame.remove();
      }
    }
    result.textContent = `PASS: live worker, private cache exclusion, ${layouts} layouts`;
  } catch (error) { result.textContent = 'FAIL: ' + error.message; }
})();
