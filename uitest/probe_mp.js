const automator = require('miniprogram-automator');
(async () => {
  try {
    const mp = await automator.connect({ wsEndpoint: 'ws://localhost:9420' });
    console.log('connected');
    const sys = await mp.systemInfo();
    console.log('sys:', sys.SDKVersion, sys.platform);
    const page = await mp.currentPage();
    console.log('currentPage:', page && page.path);
    try { const p2 = await mp.reLaunch('/pages/login/index'); console.log('reLaunch ok:', p2 && p2.path); }
    catch (e) { console.log('reLaunch ERR:', e.message && e.message.slice(0, 120)); }
    process.exitCode = 0;
  } catch (e) { console.log('CONNECT ERR:', e.message); process.exitCode = 2; }
})();
