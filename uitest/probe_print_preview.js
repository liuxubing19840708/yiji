// -*- coding: utf-8 -*-
/**
 * 隔离打印预览融合测试：登录隔离测试公司，在 automator 中渲染 label-preview。
 * 不调用 onTestPrint、不连接打印机、不写任何业务数据。截图供真机出纸对照。
 * 运行：python ../backend/run_ui_gate.py probe_print_preview.js
 */
const path = require('path');
const fs = require('fs');
const {
  Suite, ApiClient, openSession, injectLogin, pageData, withTimeout, sleep
} = require('./helpers_ui');

(async () => {
  const s = new Suite('probe_print_preview 隔离标签预览');
  let mp = null;
  const api = new ApiClient();
  try {
    await api.login();
    s.check('B1 隔离测试公司登录与公司名校验', true, `company=${api.cid}`);
    mp = await openSession((m) => console.log(m));
    await injectLogin(mp);

    const style = {
      preset: 'custom', widthMm: 50, heightMm: 80, gapMm: 2, qrUnit: 6,
      fields: ['styleNo', 'color', 'bundleNo', 'size', 'bedNo', 'qty', 'company', 'date'],
      bigQty: true, offsetMm: 15, qrPosition: 'bottom'
    };
    await withTimeout(mp.callWxMethod(
      'setStorageSync', 'yisao_print_style', JSON.stringify(style)), 10000, '保存标签样式');

    const page = await withTimeout(
      mp.reLaunch('/packages/admin/cloud-printer/index'), 20000, '打开打印管理页');
    await sleep(1000);
    const d = await pageData(page);
    s.check('P1 打印管理页载入 50×80 码下布局',
      d.paperLabel === '50×80mm' && d.qrPosLabel === '码在下·两列' && d.offsetMm === 15,
      `paper=${d.paperLabel} qr=${d.qrPosLabel} offset=${d.offsetMm}`);

    await withTimeout(page.setData({
      previewShow: true,
      previewTickets: [{
        id: 0, qr_code: 'Y0001|01|T001|1|黑色|M|2',
        bed_no: '01', bundle_no: '1', color: '黑色', size: 'M',
        qty: 2, style_no: 'T001'
      }],
      previewCompany: { name: 'UI自动化隔离厂（合成数据）' }
    }), 10000, '设置合成菲票');
    await sleep(1500);
    const comp = await withTimeout(page.$('label-preview'), 10000, '查找标签组件');
    s.check('P2 label-preview 组件已渲染', !!comp);
    if (comp) {
      const cd = await withTimeout(comp.data(), 10000, '读取标签组件');
      s.check('P3 合成菲票数量与标签布局一致',
        cd.count === 1 && String(cd.styleLabel).startsWith('50×80mm')
          && cd.curTicket && cd.curTicket.qr_code === 'Y0001|01|T001|1|黑色|M|2',
        `style=${cd.styleLabel} count=${cd.count}`);
    }

    const shots = path.join(__dirname, 'shots');
    fs.mkdirSync(shots, { recursive: true });
    const shot = path.join(shots, 'preview_bottom.png');
    await withTimeout(mp.screenshot({ path: shot }), 15000, '标签预览截图');
    s.check('P4 预览截图已生成（不代表现场出纸通过）', fs.existsSync(shot), shot);
  } catch (e) {
    s.check('标签预览自动化执行', false, String((e && e.message) || e).slice(0, 240));
  } finally {
    if (mp) {
      try { await withTimeout(mp.disconnect(), 8000, 'disconnect'); } catch (e) { /* ignore */ }
    }
    s.summary();
  }
})();
