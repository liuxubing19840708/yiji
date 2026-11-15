/**
 * BLE 蓝牙打印封装（规格 §7.2 / §21.4）。
 *
 * 流程：openBluetoothAdapter → startBluetoothDevicesDiscovery（按名称前缀过滤打印机，
 * 可配置）→ stopDiscovery → createBLEConnection → 探测可写 characteristic
 * （write 属性，优先 write-no-response）→ writeInChunks（自动分包：iOS 180
 * 字节/包、安卓 20 字节/包或协商 MTU-3，包间约 20ms 延时）→ 断开 / 关闭适配器清理。
 *
 * 全部 Promise 化；失败统一抛 Error（message 为显式中文）。
 * 注：分包大小固定 20 字节（Android 最小 MTU 安全值）；iOS 可通过 setBLEMTU
 * 协商更大 MTU 提速，但指令分包仍按 20 字节发送以保证兼容（§21.4）。
 */

/** 常见热敏标签打印机蓝牙名称前缀（按需通过 setPrinterPrefixes 覆盖） */
const DEFAULT_PREFIXES = [
  'Gprinter', 'GP-', '佳博', 'HPRT', '汉印', '快麦', 'KM-',
  'Xprinter', '芯烨', 'Zebra', 'TSC', 'BTP', 'VTR', 'PrintPort'
];
const KEY_PREFIXES = 'yisao_printer_prefixes';

export interface BleDevice {
  deviceId: string;
  /** 广播名（name 与 localName 取非空者） */
  name: string;
  rssi: number;
}

export interface ScanResult {
  /** 名称命中打印机前缀的设备（排在推荐位） */
  matched: BleDevice[];
  /** 其它在广播但未命中前缀的具名设备（兜底可选） */
  others: BleDevice[];
}

/** 已连接设备上探测到的可写特征值 */
export interface WritablePoint {
  serviceId: string;
  characteristicId: string;
  /** true = write-no-response 模式（写入更快，无应答） */
  writeNoResponse: boolean;
}

// ============ 错误码 → 中文 message（§21.4 错误显式提示） ============

const ERROR_MESSAGES: Record<number, string> = {
  10000: '蓝牙适配器未初始化，请重试',
  10001: '手机蓝牙不可用，请检查蓝牙开关与权限',
  10002: '未找到该打印机，请确认打印机已开机',
  10003: '蓝牙连接失败，请靠近打印机重试',
  10004: '未找到打印服务，请重新扫描连接',
  10005: '未找到可写入通道，该机型可能不支持蓝牙打印',
  10006: '蓝牙连接已断开，请重新连接打印机',
  10007: '当前通道不支持写入，请更换打印机',
  10008: '蓝牙系统异常，请重启小程序后重试',
  10009: '该手机不支持低功耗蓝牙',
  10012: '蓝牙连接超时，请靠近打印机重试',
  10013: '指令数据无效'
};

function btError(err: unknown, fallback: string): Error {
  const code = (err as { errCode?: number; errMsg?: string } | null)?.errCode;
  const errMsg = (err as { errMsg?: string } | null)?.errMsg || '';
  if (code !== undefined && ERROR_MESSAGES[code]) {
    return new Error(ERROR_MESSAGES[code]);
  }
  if (errMsg) {
    return new Error(`${fallback}（${errMsg}）`);
  }
  return new Error(fallback);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ============ 打印机名称前缀（可配置） ============

export function getPrinterPrefixes(): string[] {
  try {
    const saved = wx.getStorageSync(KEY_PREFIXES) as string[] | '';
    if (Array.isArray(saved) && saved.length) return saved;
  } catch (e) { /* 读取失败用默认 */ }
  return DEFAULT_PREFIXES;
}

export function setPrinterPrefixes(prefixes: string[]): void {
  wx.setStorageSync(KEY_PREFIXES, prefixes);
}

function isPrinterName(name: string): boolean {
  const n = (name || '').toUpperCase();
  if (!n) return false;
  return getPrinterPrefixes().some((p) => p && n.indexOf(p.toUpperCase()) === 0);
}

// ============ wx API Promise 化 ============

// wx API 选项为强类型，这里统一走宽松包装（指令参数在调用处逐个给定）
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function invoke<T = void>(fn: (opt: any) => void, opt: Record<string, unknown>): Promise<T> {
  return new Promise((resolve, reject) => {
    fn({
      ...opt,
      success: (res: T) => resolve(res),
      fail: (err: unknown) => reject(err)
    });
  });
}

async function openAdapter(): Promise<void> {
  try {
    await invoke((o) => wx.openBluetoothAdapter(o), {});
  } catch (err) {
    throw btError(err, '蓝牙初始化失败，请检查手机蓝牙是否开启');
  }
}

export async function closeAdapter(): Promise<void> {
  try {
    await invoke((o) => wx.closeBluetoothAdapter(o), {});
  } catch (err) {
    throw btError(err, '关闭蓝牙失败');
  }
}

async function startDiscovery(): Promise<void> {
  try {
    await invoke((o) => wx.startBluetoothDevicesDiscovery(o), {
      allowDuplicatesKey: false,
      interval: 0
    });
  } catch (err) {
    throw btError(err, '搜索打印机失败，请检查定位权限（Android 需要）');
  }
}

async function stopDiscovery(): Promise<void> {
  try {
    await invoke((o) => wx.stopBluetoothDevicesDiscovery(o), {});
  } catch (err) {
    throw btError(err, '停止搜索失败');
  }
}

async function createConnection(deviceId: string): Promise<void> {
  try {
    await invoke((o) => wx.createBLEConnection(o), { deviceId, timeout: 8000 });
  } catch (err) {
    throw btError(err, '连接打印机失败');
  }
}

export async function disconnect(deviceId: string): Promise<void> {
  if (connectedDeviceId === deviceId) {
    connectedDeviceId = '';
    cachedPoint = null;
  }
  try {
    await invoke((o) => wx.closeBLEConnection(o), { deviceId });
  } catch (err) {
    throw btError(err, '断开连接失败');
  }
}

async function getServices(deviceId: string): Promise<string[]> {
  try {
    const res = await invoke<{ services: Array<{ uuid: string; isPrimary: boolean }> }>(
      (o) => wx.getBLEDeviceServices(o), { deviceId });
    return (res.services || []).map((s) => s.uuid);
  } catch (err) {
    throw btError(err, '读取打印服务失败');
  }
}

async function getCharacteristics(deviceId: string, serviceId: string): Promise<
  Array<{ uuid: string; props: { write?: boolean; writeNoResponse?: boolean } }>
> {
  try {
    const res = await invoke<{
      characteristics: Array<{ uuid: string; props: { write?: boolean; writeNoResponse?: boolean } }>
    }>((o) => wx.getBLEDeviceCharacteristics(o), { deviceId, serviceId });
    return res.characteristics || [];
  } catch (err) {
    throw btError(err, '读取打印通道失败');
  }
}

/** 协商成功的 MTU（setBLEMTU 仅安卓支持；iOS 由系统自动协商，通常 ≥185） */
let negotiatedMtu = 0;

/** iOS 协商更大 MTU 提速（§21.4）；失败不影响打印（仅影响速度） */
async function trySetMtu(deviceId: string): Promise<void> {
  try {
    const res = await invoke<{ mtu: number }>((o) => wx.setBLEMTU(o), { deviceId, mtu: 185 });
    if (res && typeof res.mtu === 'number') negotiatedMtu = res.mtu;
  } catch (e) { /* 静默：iOS 不支持（系统已自动协商），部分安卓机型不支持 */ }
}

/** 位图打印前确保 MTU 已协商：连接时协商是异步发出未等待，整签位图约 32KB，
 *  按 20 字节保守分包要 1600+ 包（分钟级），必须先确认协商结果再发。
 *  失败（部分安卓不支持）不抛错，仅回落保守分包慢速打印。 */
export async function ensureMtu(deviceId: string): Promise<void> {
  if (!negotiatedMtu) await trySetMtu(deviceId);
}

/** 自动分包大小：iOS 系统协商 MTU 通常 ≥185，可安全发 180 字节/包；
 *  安卓仅在 setBLEMTU 协商成功后放宽到 mtu-3，否则保守 20 字节。
 *  大批量打印（上百扎）数据耗时约 1/9。 */
function autoChunkSize(): number {
  try {
    const info = wx.getDeviceInfo ? wx.getDeviceInfo() : wx.getSystemInfoSync();
    if (info.platform === 'ios') return 180;
  } catch (e) { /* 取不到平台信息走保守值 */ }
  return negotiatedMtu > 23 ? negotiatedMtu - 3 : 20;
}

// ============ 连接状态（模块级缓存，供连续打印复用） ============

let connectedDeviceId = '';
let cachedPoint: WritablePoint | null = null;

/** 当前是否已连接指定设备 */
export function isConnected(deviceId?: string): boolean {
  if (!connectedDeviceId) return false;
  return !deviceId || connectedDeviceId === deviceId;
}

export function connectedDevice(): string {
  return connectedDeviceId;
}

/**
 * 连接打印机并探测可写特征值。已连接时直接复用缓存。
 * 标准系统服务（GAP/GATT）与无可写特征的服务会被跳过。
 */
export async function connectPrinter(deviceId: string): Promise<WritablePoint> {
  if (isConnected(deviceId) && cachedPoint) {
    return cachedPoint;
  }
  if (connectedDeviceId && connectedDeviceId !== deviceId) {
    await disconnect(connectedDeviceId).catch(() => { /* 旧连接清理失败不阻塞 */ });
  }
  await openAdapter();
  await createConnection(deviceId);
  const point = await findWritablePoint(deviceId);
  connectedDeviceId = deviceId;
  cachedPoint = point;
  void trySetMtu(deviceId);
  return point;
}

/** 打印机常用数据通道 UUID（部分固件 props 上报不全，按 UUID 兜底命中） */
const KNOWN_PRINT_CHARS = /^0000(ff02|ae01|2af1|fe01)-0000-1000-8000-00805f9b34fb$/i;

async function findWritablePoint(deviceId: string): Promise<WritablePoint> {
  let diag = '';
  // 安卓 GATT 服务发现是异步的：连接刚返回时列表可能为空/残缺，轮询重查
  for (let attempt = 0; attempt < 5; attempt++) {
    const serviceIds = await getServices(deviceId);
    // 排除标准 GATT 服务（sig 定义，打印机数据通道不在这里）
    const dataServices = serviceIds.filter(
      (u) => u && !/^0000(1800|1801|180a)-0000-1000-8000-00805f9b34fb$/i.test(u));
    diag = ''; let fallback: { serviceId: string; uuid: string } | null = null;
    for (const serviceId of dataServices) {
      const chars = await getCharacteristics(deviceId, serviceId);
      // 诊断串：服务短码[特征短码+属性标记]，报错时随 toast 带出
      diag += `${serviceId.slice(4, 8)}[${chars.map((c) => {
        let tag = c.uuid.slice(4, 8);
        if (c.props) { if (c.props.write) tag += 'w'; if (c.props.writeNoResponse) tag += 'n'; }
        return tag;
      }).join(',')}] `;
      const writable = chars.filter((c) => c.props && (c.props.write || c.props.writeNoResponse));
      // 优先 write-no-response（写入更快、不易堵包）
      const noResp = writable.find((c) => c.props.writeNoResponse);
      const withResp = writable.find((c) => c.props.write && c.uuid !== noResp?.uuid);
      const pick = noResp || withResp;
      if (pick) {
        return {
          serviceId,
          characteristicId: pick.uuid,
          writeNoResponse: !!pick.props.writeNoResponse && !pick.props.write
        };
      }
      if (!fallback) {
        const fb = chars.find((c) => KNOWN_PRINT_CHARS.test(c.uuid));
        if (fb) fallback = { serviceId, uuid: fb.uuid };
      }
    }
    // props 全部缺失但 UUID 命中打印通道：按带应答写入兜底
    if (fallback) {
      return { serviceId: fallback.serviceId, characteristicId: fallback.uuid, writeNoResponse: false };
    }
    await delay(500);
  }
  throw new Error(`未找到可写入通道（探测到：${diag.trim() || '无服务'}）`);
}

/** 确保处于已连接状态（未连接则自动重连） */
export async function ensureConnected(deviceId: string): Promise<WritablePoint> {
  if (isConnected(deviceId) && cachedPoint) return cachedPoint;
  return connectPrinter(deviceId);
}

// ============ 数据写入 ============

async function writeChunk(point: WritablePoint, deviceId: string, value: ArrayBuffer): Promise<void> {
  try {
    await invoke((o) => wx.writeBLECharacteristicValue(o), {
      deviceId,
      serviceId: point.serviceId,
      characteristicId: point.characteristicId,
      value,
      writeType: point.writeNoResponse ? 'writeNoResponse' : 'write'
    });
  } catch (err) {
    const code = (err as { errCode?: number } | null)?.errCode;
    if (code === 10006 || code === 10002) {
      connectedDeviceId = '';
      cachedPoint = null;
    }
    throw btError(err, '写入打印指令失败');
  }
}

/**
 * 分包写入：默认自动分包（iOS 180 字节/包、安卓 20 字节/包，协商 MTU 成功则
 * mtu-3），包间 20ms 延时（§7.2，iOS 必须串行+间隔）。显式传 chunkSize 可覆盖。
 * 断连（10006）后抛错，由上层引导重连。
 */
export async function writeInChunks(
  deviceId: string,
  data: ArrayBuffer,
  chunkSize = 0,
  delayMs = 20
): Promise<void> {
  const point = await ensureConnected(deviceId);
  const size = chunkSize > 0 ? chunkSize : autoChunkSize();
  const u8 = new Uint8Array(data);
  for (let offset = 0; offset < u8.length; offset += size) {
    const end = Math.min(offset + size, u8.length);
    const part = u8.slice(offset, end); // slice 拷贝，保证独立 buffer
    await writeChunk(point, deviceId, part.buffer);
    if (end < u8.length) {
      await delay(delayMs);
    }
  }
}

// ============ 扫描 ============

/**
 * 扫描附近打印机：默认扫 3 秒，按名称前缀分类。
 * matched = 命中打印机前缀（推荐），others = 其它具名设备（兜底）。
 */
export async function scanPrinters(timeoutMs = 3000): Promise<ScanResult> {
  await openAdapter();
  const seen = new Map<string, BleDevice>();

  const collect = (devices: Array<{ deviceId?: string; name?: string; localName?: string; RSSI?: number }>) => {
    for (const d of devices) {
      if (!d.deviceId) continue;
      const name = d.name || d.localName || '';
      const prev = seen.get(d.deviceId);
      // 同设备保留信号更强的一条
      if (!prev || (d.RSSI || -255) > (prev.rssi || -255)) {
        seen.set(d.deviceId, { deviceId: d.deviceId, name, rssi: d.RSSI || 0 });
      }
    }
  };

  const onFound = (res: { devices: Array<{ deviceId?: string; name?: string; localName?: string; RSSI?: number }> }) => {
    collect(res.devices || []);
  };
  wx.onBluetoothDeviceFound(onFound);
  await startDiscovery();
  await delay(timeoutMs);
  await stopDiscovery().catch(() => { /* 停止失败不影响结果 */ });
  wx.offBluetoothDeviceFound();

  // 兜底：把监听开启前已广播的设备也合并进来
  try {
    const res = await invoke<{ devices: Array<{ deviceId?: string; name?: string; localName?: string; RSSI?: number }> }>(
      (o) => wx.getBluetoothDevices(o), {});
    collect(res.devices || []);
  } catch (e) { /* 忽略 */ }

  const matched: BleDevice[] = [];
  const others: BleDevice[] = [];
  seen.forEach((d) => {
    if (!d.name) return; // 无名设备不展示
    (isPrinterName(d.name) ? matched : others).push(d);
  });
  const byRssi = (a: BleDevice, b: BleDevice) => (b.rssi || 0) - (a.rssi || 0);
  return { matched: matched.sort(byRssi), others: others.sort(byRssi) };
}

/** 页面离开时的清理：断开连接并关闭适配器（由调用方按需触发） */
export async function cleanup(): Promise<void> {
  if (connectedDeviceId) {
    await disconnect(connectedDeviceId).catch(() => { /* 清理失败静默 */ });
  }
  await closeAdapter().catch(() => { /* 未初始化时忽略 */ });
}
