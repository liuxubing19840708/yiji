/**
 * 打印 UI 统一流程（2026-10-25 P0/P2 收敛）：进度弹窗 + 可取消 + 结果/失败引导，
 * 供各打印入口页共用（生产列表 / 床次详情 / 建床向导 / 打印管理补打）。
 * 以前三个页面各维护一份 showLoading + 错误弹窗，体验不一致（有的没「去连接」按钮），
 * 改一次要改三处；现在统一在这里。
 *
 * 页面接入（三步）：
 *  1. index.json 的 usingComponents 注册 "print-progress": "/components/print-progress/index"
 *  2. wxml 末尾放 <print-progress show="{{printFlow.show}}" done="{{printFlow.done}}"
 *       total="{{printFlow.total}}" bind:cancel="onFlowCancel" />
 *  3. 页面加方法 onFlowCancel() { cancelPrintFlow(); }，data 初始 printFlow
 *
 * 交互口径：
 *  - 进行中：弹窗显示 done/total + 进度条，「取消打印」随时可停（已出的留痕，
 *    剩余不入失败队列）；打印期间屏幕常亮由 printer.printTickets 负责
 *  - 全部成功：toast + onSuccess
 *  - 部分失败：弹窗带「去补打」直达打印管理 + onFinished
 *  - 未连接/连接失败：弹窗带「去连接」直达打印管理 + onError
 */
import { printTickets, PrintResult } from '../services/printer';
import { PrintTicketData, PrintCompanyInfo } from './bluetooth/templates';

/** 宿主页面最小接口（setData 注入 printFlow 状态） */
export interface PrintFlowHost {
  setData(data: Record<string, unknown>): void;
}

export interface PrintFlowOptions {
  /** 打印公司信息（标签底部公司名） */
  company?: PrintCompanyInfo;
  /** 是否上报打印计数（默认 true；测试打印传 false） */
  report?: boolean;
  /** 失败是否入重试队列（默认 true；补打场景传 false 防重复入队） */
  queue?: boolean;
  /** 全部成功（或取消但已有部分出票）后回调——页面刷新列表/徽标 */
  onSuccess?: (printedCount: number) => void;
  /** 打印流程结束回调（成功/部分失败/取消都会调；连接失败不调） */
  onFinished?: (result: PrintResult) => void;
  /** 连接失败/未绑定引导弹窗关闭后回调 */
  onError?: (err: Error) => void;
}

/** 进行中的流程取消钩子（模块级：同一页面同时只有一个打印流程） */
let activeCancel: (() => void) | null = null;

/** 进度弹窗「取消打印」按钮回调（页面 onFlowCancel 转发到这里） */
export function cancelPrintFlow(): void {
  if (activeCancel) activeCancel();
}

/** 打印机相关错误 → 弹窗带「去连接/去补打」按钮直达打印管理 */
function showPrinterGuideModal(title: string, content: string, confirmText: string): void {
  wx.showModal({
    title,
    content,
    confirmText,
    cancelText: '留在本页',
    success: (r) => {
      if (r.confirm) {
        wx.navigateTo({ url: '/packages/admin/cloud-printer/index' });
      }
    }
  });
}

/**
 * 发起一次带进度弹窗的打印流程（替代各页手写 showLoading 循环）。
 * 页面 data 需有 printFlow 初始值 { show: false, done: 0, total: 0 }。
 */
export function startPrintFlow(
  host: PrintFlowHost,
  tickets: PrintTicketData[],
  opts: PrintFlowOptions = {}
): void {
  if (!tickets.length) return;
  let cancelled = false;
  activeCancel = () => { cancelled = true; };
  const close = () => host.setData({ printFlow: { show: false, done: 0, total: 0 } });
  host.setData({ printFlow: { show: true, done: 0, total: tickets.length } });

  printTickets(tickets, {
    company: opts.company,
    report: opts.report,
    queue: opts.queue,
    isCancelled: () => cancelled,
    onProgress: (done, total) => host.setData({ printFlow: { show: true, done, total } })
  })
    .then((result) => {
      close();
      activeCancel = null;
      if (opts.onFinished) opts.onFinished(result);
      if (result.cancelled) {
        wx.showToast({ title: `已取消，已打印 ${result.printed.length} 张`, icon: 'none' });
        // 取消时已有部分出票：同样刷新页面状态（is_printed 已变化）
        if (result.printed.length && opts.onSuccess) opts.onSuccess(result.printed.length);
        return;
      }
      if (!result.failed.length) {
        wx.showToast({ title: `已打印 ${result.printed.length} 张`, icon: 'success' });
        if (opts.onSuccess) opts.onSuccess(result.printed.length);
        return;
      }
      // 部分失败：引导到打印管理补打
      showPrinterGuideModal(
        '部分未打印',
        `成功 ${result.printed.length} 张，${result.failed.length} 张失败已入重试队列，可到「打印管理」补打`,
        '去补打'
      );
    })
    .catch((err: Error) => {
      close();
      activeCancel = null;
      const msg = (err && err.message) || '打印失败';
      const notConnected = msg.indexOf('连接') >= 0 || msg.indexOf('连接打印机') >= 0;
      if (notConnected) {
        showPrinterGuideModal(
          '未连接打印机',
          '先到「打印管理」页扫描绑定蓝牙打印机，再回来打印；也可稍后在「打印管理」补打',
          '去连接'
        );
      } else {
        wx.showModal({ title: '未能打印', content: msg, showCancel: false });
      }
      if (opts.onError) opts.onError(err instanceof Error ? err : new Error(msg));
    });
}
