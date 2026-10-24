# 直接打开 USBPRINT 设备接口写原始字节（无需驱动/假脱机）：
#   send_usbprint.ps1 <bin文件>
# 设备实例：USBPRINT\XPRINTERXP-D362B（USB 打印类标准接口 GUID）
param([Parameter(Mandatory=$true)][string]$BinFile)
$code = @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class UsbPrint {
  public static readonly Guid UsbPrintGuid = new Guid("28D78FAD-5A12-11d1-AE5B-0000F803A8C6");
  [DllImport("cfgmgr32.dll", CharSet=CharSet.Ansi)]
  public static extern int CM_Get_Device_Interface_ListA(ref Guid guid, string deviceId, StringBuilder buf, uint len, uint flags);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool WriteFile(IntPtr h, byte[] buf, uint n, out uint written, IntPtr ov);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool FlushFileBuffers(IntPtr h);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool CloseHandle(IntPtr h);
}
'@
Add-Type -TypeDefinition $code
$devId = $null
$guid = [UsbPrint]::UsbPrintGuid
$sb = New-Object System.Text.StringBuilder 4096
# CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0；DeviceID=null 枚举全部 USBPRINT 接口
$cr = [UsbPrint]::CM_Get_Device_Interface_ListA([ref]$guid, $devId, $sb, 4096, 0)
if ($cr -ne 0 -or $sb.Length -eq 0) {
  Write-Output "IFACE-FAIL cr=$cr (CR_SUCCESS=0, CR_NO_INTERFACE=25) devId=$devId"
  exit 2
}
$ifaces = $sb.ToString().Split([char]0) | Where-Object { $_ }
if ($ifaces.Count -gt 1) {
  Write-Output "candidates:"; $ifaces | ForEach-Object { Write-Output "  $_" }
}
$iface = ($ifaces | Where-Object { $_ -match 'VID_2D37' })[0]; if (-not $iface) { $iface = $ifaces[0] }
Write-Output "iface=$iface"
$handle = [UsbPrint]::CreateFile($iface, 0x40000000, 0x00000003, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)  # GENERIC_WRITE, FILE_SHARE_READ|WRITE
if ($handle -eq [IntPtr]::new(-1)) {
  $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
  Write-Output "OPEN-FAIL win32err=$err"
  exit 3
}
$bytes = [System.IO.File]::ReadAllBytes($BinFile)
$written = 0
$ok = [UsbPrint]::WriteFile($handle, $bytes, [uint32]$bytes.Length, [ref]$written, [IntPtr]::Zero)
$werr = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
$flush = [UsbPrint]::FlushFileBuffers($handle)
[UsbPrint]::CloseHandle($handle) | Out-Null
Write-Output "file=$BinFile bytes=$($bytes.Length) write=$ok written=$written writeErr=$werr flush=$flush"
