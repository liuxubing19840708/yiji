const automator = require('miniprogram-automator');
const TMO = 60000;
const t = setTimeout(() => { console.log('TIMEOUT'); process.exitCode = 1; }, TMO);
automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' }).then(async mp => {
  try {
    const page = await mp.reLaunch('/packages/admin/kanban/index');
    await page.waitFor(3500);
    const headCells = await page.$$('.pt-head .pt-cell');
    const total = await page.$('.pt-merged.pt-total');
    if (headCells.length < 7 || !total) { console.log('MISS'); process.exitCode = 1; return; }
    const h6 = await headCells[5].offset(); const h6s = await headCells[5].size();
    const h7 = await headCells[6].offset(); const h7s = await headCells[6].size();
    const tv = await total.offset(); const ts = await total.size();
    console.log('header col6 right:', (h6.left + h6s.width).toFixed(1), ' col7 left:', h7.left.toFixed(1), 'w:', h7s.width);
    console.log('block total  left:', tv.left.toFixed(1), 'w:', ts.width);
    console.log('dx vs col7:', Math.abs(h7.left - tv.left).toFixed(1));
    const holds = await page.$$('.pt-holds');
    const alts = await page.$$('.pt-block.pt-alt');
    console.log('holds blocks:', holds.length, 'alt blocks:', alts.length);
    clearTimeout(t);
    process.exitCode = 0;
  } catch (e) { console.log('ERR', e.message); process.exitCode = 1; }
  await mp.disconnect();
}).catch(e => { console.log('CONN-ERR', e.message); clearTimeout(t); process.exitCode = 1; });
