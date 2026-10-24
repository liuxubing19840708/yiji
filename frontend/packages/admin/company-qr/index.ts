/**
 * 公司二维码页（规格 §3.1）：橙色全屏，公司名+城市卡片、大二维码、
 * 企业编号大字、文案「用衣计，扫码加入公司」、白色「分享」按钮、保存到相册。
 *
 * 二维码内容约定（重要 —— 「扫码加入」解析方需对齐）：
 *   `JOIN:<company_no>`，例如 `JOIN:111265`
 *   company_no 为 6 位数字企业编号；解析出编号后走 POST /companies/join 提交入职申请。
 *   （后端 wxacode 小程序码接入前，先以此文本二维码落地，扫码可用 wx.scanCode 识别）
 *
 * 二维码为 canvas 本地生成（./qrcode.ts，零依赖），分享走 button open-type="share"。
 */
import { requireLogin, getCompanyId, getToken } from '../../../utils/auth';
import { fetchMyCompanies } from '../../../services/auth';
import { CompanyItem } from '../../../services/types';
import { make as makeQrMatrix } from '../../../utils/qrcode';
import { BASE_URL } from '../../../utils/config';

/** 二维码文本前缀（与扫码加入解析约定对齐） */
const QR_TEXT_PREFIX = 'JOIN:';

Page({
  data: {
    loading: true,
    loadError: '',
    name: '',
    city: '',
    companyNo: '',
    saving: false,
    /** 小程序码本地文件路径（后端已配微信凭据时生成；空 = 用本地文本二维码） */
    wxacodePath: ''
  },

  /** 2d canvas 节点（renderQr 后可用，供导出图片） */
  canvasNode: null as WechatMiniprogram.Canvas | null,

  onShow() {
    if (!requireLogin()) return;
    this.loadCompany();
  },

  onShareAppMessage(): WechatMiniprogram.Page.ICustomShareContent {
    return {
      title: `用衣计，扫码/搜索编号 ${this.data.companyNo} 加入「${this.data.name}」`,
      // 落地参数约定：登录页 onLoad 读 companyNo 预填「凭编号加入」表单
      path: `/pages/login/index?companyNo=${this.data.companyNo}`
    };
  },

  // ---- 数据 ----

  loadCompany() {
    const cid = getCompanyId();
    this.setData({ loading: true, loadError: '' });
    fetchMyCompanies()
      .then((list: CompanyItem[]) => {
        const c = list.find((item) => item.id === cid);
        if (!c) {
          this.setData({ loading: false, loadError: '未找到当前公司信息，请重新登录后再试' });
          return;
        }
        this.setData(
          { loading: false, name: c.name, city: c.city || '', companyNo: c.company_no },
          () => {
            this.renderQr();
            this.loadWxacode();
          }
        );
      })
      .catch(() => this.setData({ loading: false, loadError: '加载失败，请重试' }));
  },

  onRetry() {
    this.loadCompany();
  },

  // ---- 本地生成二维码（canvas 2d） ----

  renderQr() {
    const text = QR_TEXT_PREFIX + this.data.companyNo;
    let matrix: boolean[][];
    try {
      matrix = makeQrMatrix(text);
    } catch (e) {
      this.setData({ loadError: '二维码生成失败，请重试' });
      return;
    }
    const query = this.createSelectorQuery();
    query.select('#qr-canvas').fields({ node: true, size: true });
    query.exec((res) => {
      const info = (res && res[0]) as
        | { node?: WechatMiniprogram.Canvas; width?: number; height?: number }
        | undefined;
      if (!info || !info.node) return;
      const canvas = info.node;
      const dpr = wx.getSystemInfoSync().pixelRatio || 2;
      const cssW = info.width || 0;
      const cssH = info.height || 0;
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      const ctx = canvas.getContext('2d');
      ctx.scale(dpr, dpr);
      this.drawMatrix(ctx, matrix, Math.min(cssW, cssH));
      this.canvasNode = canvas;
    });
  },

  /** 2D 画布上下文（miniprogram-api-typings：WechatMiniprogram.CanvasRenderingContext 命名空间内） */
  drawMatrix(ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D, matrix: boolean[][], boxSize: number) {
    const n = matrix.length;
    const quiet = 2; // 静区（模块数）
    const total = n + quiet * 2;
    const cell = Math.floor(boxSize / total);
    if (cell <= 0) return;
    const offset = Math.floor((boxSize - cell * total) / 2);
    // 白底（二维码规范黑白色，与主题色无关）
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, boxSize, boxSize);
    ctx.fillStyle = '#000000';
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        if (matrix[r][c]) {
          ctx.fillRect(
            offset + (quiet + c) * cell,
            offset + (quiet + r) * cell,
            cell,
            cell
          );
        }
      }
    }
  },

  // ---- 小程序码（微信「扫一扫」直达链路；需后端已配置 WECHAT_APPID/SECRET） ----

  /** 拉取后端小程序码 PNG 落本地文件；任何失败静默回落本地文本二维码 */
  loadWxacode() {
    const cid = getCompanyId();
    wx.request({
      url: `${BASE_URL}/companies/wxacode`,
      method: 'GET',
      header: { Authorization: `Bearer ${getToken()}`, 'X-Company-Id': String(cid) },
      responseType: 'arraybuffer',
      success: (res) => {
        if (res.statusCode !== 200 || !(res.data instanceof ArrayBuffer)) return;
        const path = `${wx.env.USER_DATA_PATH}/wxacode_${cid}.png`;
        wx.getFileSystemManager().writeFile({
          filePath: path,
          data: res.data as ArrayBuffer,
          success: () => this.setData({ wxacodePath: path }),
          fail: () => { /* 保持文本二维码 */ }
        });
      },
      fail: () => { /* 保持文本二维码 */ }
    });
  },

  // ---- 保存到相册 ----

  onSaveQr() {
    if (this.data.saving) return;
    // 小程序码已是本地图片文件，直接存
    if (this.data.wxacodePath) {
      this.setData({ saving: true });
      wx.saveImageToPhotosAlbum({
        filePath: this.data.wxacodePath,
        success: () => wx.showToast({ title: '已保存到相册', icon: 'success' }),
        fail: (err) => this.onSaveAlbumFail(err),
        complete: () => this.setData({ saving: false })
      });
      return;
    }
    if (!this.canvasNode) {
      wx.showToast({ title: '二维码尚未生成，请稍后重试', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    wx.canvasToTempFilePath({
      canvas: this.canvasNode as unknown as Record<string, unknown>,
      success: (res) => {
        wx.saveImageToPhotosAlbum({
          filePath: res.tempFilePath,
          success: () => {
            wx.showToast({ title: '已保存到相册', icon: 'success' });
          },
          fail: (err) => this.onSaveAlbumFail(err)
        });
      },
      fail: () => {
        wx.showToast({ title: '生成图片失败，请重试', icon: 'none' });
      },
      complete: () => this.setData({ saving: false })
    });
  },

  /** 相册授权被拒：引导去设置页开启 */
  onSaveAlbumFail(err: { errMsg?: string }) {
    const msg = (err && err.errMsg) || '';
    if (msg.indexOf('auth') >= 0 || msg.indexOf('deny') >= 0) {
      wx.showModal({
        title: '无法保存到相册',
        content: '请在设置中允许「保存到相册」权限后重试',
        confirmText: '去设置',
        success: (r) => {
          if (r.confirm) wx.openSetting({});
        }
      });
    } else if (msg.indexOf('cancel') < 0) {
      wx.showToast({ title: '保存失败，请重试', icon: 'none' });
    }
  }
});
