# Read or set this PC's default playback device master volume (0..1) for
# acoustic microphone tests.  tools/pc_volume.ps1 [-Set 0.8]
param([double]$Set=-1)
$ErrorActionPreference='Stop'
if(-not ('PcVolume' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f1(); int f2(); int f3(); int f4();
  int SetMasterVolumeLevelScalar(float level, Guid ctx);
  int f6();
  int GetMasterVolumeLevelScalar(out float level);
  int f8(); int f9(); int f10(); int f11();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, Guid ctx);
  int GetMute(out bool mute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref Guid id, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
  int OpenPropertyStore(int access, out IntPtr store);
  int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int f1();
  int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice dev);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}
public static class PcVolume {
  static IAudioEndpointVolume Ep() {
    var e = (IMMDeviceEnumerator)new MMDeviceEnumerator(); IMMDevice d;
    Marshal.ThrowExceptionForHR(e.GetDefaultAudioEndpoint(0, 1, out d));
    var g = typeof(IAudioEndpointVolume).GUID; object o;
    Marshal.ThrowExceptionForHR(d.Activate(ref g, 23, IntPtr.Zero, out o));
    return (IAudioEndpointVolume)o;
  }
  public static string Id() {
    var e = (IMMDeviceEnumerator)new MMDeviceEnumerator(); IMMDevice d; string s;
    Marshal.ThrowExceptionForHR(e.GetDefaultAudioEndpoint(0, 1, out d)); d.GetId(out s); return s;
  }
  public static float Get() { float v; Marshal.ThrowExceptionForHR(Ep().GetMasterVolumeLevelScalar(out v)); return v; }
  public static bool Muted() { bool m; Marshal.ThrowExceptionForHR(Ep().GetMute(out m)); return m; }
  public static void Set(float v) { var ep = Ep(); Marshal.ThrowExceptionForHR(ep.SetMute(false, Guid.Empty)); Marshal.ThrowExceptionForHR(ep.SetMasterVolumeLevelScalar(v, Guid.Empty)); }
}
'@
}
if($Set -ge 0){ [PcVolume]::Set([float][Math]::Min([double]1,$Set)) }
$id=[PcVolume]::Id()
$name=(Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $id.EndsWith($_.InstanceId.Split('\')[-1],[StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1).FriendlyName
[pscustomobject]@{Device=$name;Volume=[Math]::Round([PcVolume]::Get(),3);Muted=[PcVolume]::Muted()}
