Add-Type -AssemblyName System.Drawing
$exe = "C:\Program Files\Fetchly\Fetchly.exe"
$ico = [System.Drawing.Icon]::ExtractAssociatedIcon($exe)
$bmp = $ico.ToBitmap()
$bmp.Save("extracted_from_exe.png", [System.Drawing.Imaging.ImageFormat]::Png)
Write-Host "Extracted associated icon size: $($ico.Width)x$($ico.Height)"
