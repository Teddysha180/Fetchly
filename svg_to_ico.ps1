# png_to_ico.ps1 — Convert a PNG to a proper multi-size ICO using System.Drawing
Add-Type -AssemblyName System.Drawing

$src    = "C:\Users\lenovo\Downloads\Fetchly.png"
$icoOut = [System.IO.Path]::GetFullPath("fetchly.ico")
$sizes  = @(256, 128, 64, 48, 32, 16)

Write-Host "[icon] Loading $src"
$srcBmp = [System.Drawing.Image]::FromFile($src)

function Make-PngBytes($size) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode  = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.SmoothingMode      = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $g.PixelOffsetMode    = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $g.DrawImage($srcBmp, 0, 0, $size, $size)
    $g.Dispose()

    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return $ms.ToArray()
}

$pngList = @()
foreach ($sz in $sizes) {
    Write-Host "  Resizing to ${sz}x${sz}..."
    $pngList += ,(Make-PngBytes $sz)
    Write-Host "    -> $($pngList[-1].Length) bytes"
}

$srcBmp.Dispose()

# ── Assemble ICO file ─────────────────────────────────────────────────────
# Format: 6-byte header + 16-byte directory per image + raw PNG data
$n      = $pngList.Count
$offset = 6 + $n * 16
$ico    = New-Object System.IO.MemoryStream

# Header: reserved=0, type=1 (ICO), count=n
$ico.Write([byte[]](0,0, 1,0, [byte]$n,0), 0, 6)

# Directory entries
$off = $offset
for ($i = 0; $i -lt $n; $i++) {
    $w   = if ($sizes[$i] -ge 256) { [byte]0 } else { [byte]$sizes[$i] }
    $h   = $w
    $len = $pngList[$i].Length
    $ico.Write([byte[]]($w,$h, 0,0, 1,0, 32,0), 0, 8)
    $ico.Write([System.BitConverter]::GetBytes([int]$len), 0, 4)
    $ico.Write([System.BitConverter]::GetBytes([int]$off),  0, 4)
    $off += $len
}

# Image data
foreach ($png in $pngList) { $ico.Write($png, 0, $png.Length) }

[System.IO.File]::WriteAllBytes($icoOut, $ico.ToArray())
Write-Host ""
Write-Host "[icon] Done! $icoOut"
Write-Host "[icon] Size: $([Math]::Round($ico.Length/1KB,1)) KB, $n sizes"
