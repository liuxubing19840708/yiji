// Web Bluetooth 与小程序 API 不同：浏览器通过设备选择器授权设备，不能后台枚举附近设备。
// 这里只封装真实的 GATT 连接和分包写入，不把“指令写完”解释为打印机已经出纸。
const STANDARD_SERVICES = /^(0000)?(1800|1801|180a)-0000-1000-8000-00805f9b34fb$/i

// 标签/串口打印机常见的 GATT 服务。acceptAllDevices 让用户自行选择设备，
// optionalServices 决定本页连接后被允许读取的服务；厂商私有服务不在列表中时会被浏览器拒绝。
export const PRINTER_SERVICE_UUIDS = [
  '000018f0-0000-1000-8000-00805f9b34fb',
  '0000ff00-0000-1000-8000-00805f9b34fb',
  '0000ffe0-0000-1000-8000-00805f9b34fb',
  '0000ffe5-0000-1000-8000-00805f9b34fb',
  '0000fee7-0000-1000-8000-00805f9b34fb',
  '0000ae00-0000-1000-8000-00805f9b34fb',
  '49535343-fe7d-4ae5-8fa9-9fafd205e455', // ISSC 透明传输
  '6e400001-b5a3-f393-e0a9-e50e24dcca9e', // Nordic UART
  'e7810a71-73ae-499d-8c15-faa9aef0c3f2', // 常见 BLE 串口
]

export function webBluetoothSupported() {
  return typeof navigator !== 'undefined' && Boolean(navigator.bluetooth?.requestDevice)
}

export function bluetoothError(error, fallback = '蓝牙操作失败') {
  const name = error?.name || ''
  const messages = {
    NotFoundError: '未找到或未授权可访问的打印服务；请确认打印机已开机并支持 BLE GATT',
    SecurityError: '浏览器安全策略阻止了蓝牙访问；请使用 HTTPS 或 localhost，并在用户点击后操作',
    NetworkError: '蓝牙连接或写入中断，请靠近打印机、检查电源后重试',
    NotSupportedError: '设备或浏览器不支持此蓝牙操作',
    InvalidStateError: '蓝牙连接状态已变化，请重新连接后重试',
    NotAllowedError: '浏览器或系统拒绝了蓝牙权限，请检查网站权限与系统蓝牙设置',
  }
  if (name === 'NotFoundError' && error?.message?.includes('cancelled')) return new Error('未选择设备')
  return new Error(messages[name] || `${fallback}${error?.message ? `：${error.message}` : ''}`)
}

function writable(characteristic) {
  return characteristic.properties?.write || characteristic.properties?.writeWithoutResponse
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export class WebBluetoothPrinter {
  constructor(onDisconnect) {
    this.device = null
    this.characteristic = null
    this.onDisconnect = onDisconnect
  }

  async requestDevice() {
    if (!webBluetoothSupported()) throw new Error('当前浏览器不支持 Web Bluetooth，请改用 Chrome / Edge，或使用浏览器打印')
    try {
      this.disconnect()
      const device = await navigator.bluetooth.requestDevice({
        acceptAllDevices: true,
        optionalServices: PRINTER_SERVICE_UUIDS,
      })
      this.device = device
      this.characteristic = null
      device.addEventListener('gattserverdisconnected', () => {
        if (this.device?.id === device.id) {
          this.characteristic = null
          this.onDisconnect?.(device)
        }
      })
      return device
    } catch (error) {
      if (error?.name === 'NotFoundError') throw new Error('未选择蓝牙设备，未发起连接')
      throw bluetoothError(error, '打开蓝牙设备选择器失败')
    }
  }

  async connect(device = this.device) {
    if (!device?.gatt) throw new Error('请先通过浏览器设备选择器选择一台 BLE 打印机')
    if (this.characteristic && device.gatt.connected && this.device?.id === device.id) return
    this.disconnect()
    this.device = device
    try {
      const server = await device.gatt.connect()
      const services = await server.getPrimaryServices()
      for (const service of services) {
        if (STANDARD_SERVICES.test(service.uuid)) continue
        const characteristics = await service.getCharacteristics()
        // 与小程序策略一致，优先无应答写入；Web Bluetooth 不提供 MTU 协商。
        const selected = characteristics.find((item) => item.properties?.writeWithoutResponse)
          || characteristics.find(writable)
        if (selected) {
          this.characteristic = selected
          return
        }
      }
      throw new Error('未找到可写 GATT 特征值；该设备可能不是 BLE 打印机，或其私有服务未获浏览器授权')
    } catch (error) {
      this.characteristic = null
      if (device.gatt.connected) device.gatt.disconnect()
      if (error instanceof Error && !error.name) throw error
      if (error?.message?.startsWith('未找到可写')) throw error
      throw bluetoothError(error, '连接打印机或读取打印通道失败')
    }
  }

  disconnect() {
    const device = this.device
    this.characteristic = null
    if (device?.gatt?.connected) device.gatt.disconnect()
  }

  get connected() {
    return Boolean(this.device?.gatt?.connected && this.characteristic)
  }

  async write(data) {
    if (!this.connected) throw new Error('蓝牙打印机未连接，请重新连接后重试')
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data)
    const characteristic = this.characteristic
    try {
      // 保守按 20 字节串行分包；浏览器无 setBLEMTU，不能照搬小程序的 MTU 协商。
      for (let offset = 0; offset < bytes.length; offset += 20) {
        const chunk = bytes.slice(offset, offset + 20)
        if (characteristic.properties.writeWithoutResponse) {
          if (typeof characteristic.writeValueWithoutResponse === 'function') {
            await characteristic.writeValueWithoutResponse(chunk)
          } else {
            await characteristic.writeValue(chunk)
          }
        } else if (typeof characteristic.writeValueWithResponse === 'function') {
          await characteristic.writeValueWithResponse(chunk)
        } else {
          await characteristic.writeValue(chunk)
        }
        if (offset + 20 < bytes.length) await delay(20)
      }
    } catch (error) {
      throw bluetoothError(error, '向打印机写入 TSPL 指令失败')
    }
  }
}
