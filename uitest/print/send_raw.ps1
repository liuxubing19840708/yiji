# 原始字节写打印机端口（带 Win32 错误码诊断）：send_raw.ps1 <bin文件> [端口名]
param(
  [Parameter(Mandatory=$true)][string]$BinFile,
  [string]$Port = 'USB001'
)
$code = @'
using System;
using System.Runtime.InteropServices;
public static class RawPort {
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
$path = "\\.\$Port"
$GENERIC_WRITE = 0x40000000
$handle = [RawPort]::CreateFile($path, $GENERIC_WRITE, 0, [IntPtr]::Zero, 3, 0, [IntPtr]::Zero)  # OPEN_EXISTING=3
if ($handle -eq [IntPtr]::new(-1)) {
  $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
  Write-Output "OPEN-FAIL path=$path win32err=$err"
  exit 2
}
$bytes = [System.IO.File]::ReadAllBytes($BinFile)
$written = 0
$ok = [RawPort]::WriteFile($handle, $bytes, [uint32]$bytes.Length, [ref]$written, [IntPtr]::Zero)
$werr = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
$flush = [RawPort]::FlushFileBuffers($handle)
$ferr = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
[RawPort]::CloseHandle($handle) | Out-Null
Write-Output "file=$BinFile bytes=$($bytes.Length) write=$ok written=$written writeErr=$werr flush=$flush flushErr=$ferr"
