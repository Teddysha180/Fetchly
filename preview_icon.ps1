# preview_icon.ps1
Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase

$SVG_W = 432.13; $SVG_H = 617.08; $sz = 256
$p1 = "M385.67,266.82h-79.01c-25.11,0-45.46,20.36-45.46,45.46v69.14c0,26.24-21.27,47.5-47.51,47.5s-47.51-21.26-47.51-47.5v-183.62c0-17.46,14.15-31.61,31.61-31.61h184.82c25.23,0,45.67-20.45,45.67-45.67V46.69c0-25.22-20.45-45.69-45.67-45.69h-175.73c-25.23,0-45.67,20.46-45.67,45.69v79.11c0,17.46-14.15,31.61-31.61,31.61H32.97c-17.66,0-31.97,14.31-31.97,31.97v14.33c0,125.88,102.04,227.93,227.92,227.93h36.63v.38h120.12c25.11,0,45.46-20.36,45.46-45.46v-74.27c0-25.1-20.35-45.46-45.46-45.46Z"
$p2 = "M133.65,616.08h-64.26c-28.64,0-51.87-23.22-51.87-51.87v-80.33c0-28.65,23.22-51.87,51.87-51.87h116.12v132.2c0,28.65-23.22,51.87-51.86,51.87Z"

$pad    = $sz * 0.06
$avail  = $sz - 2.0 * $pad
$scaleF = [Math]::Min($avail / $SVG_W, $avail / $SVG_H)
$ox     = $pad + ($avail - $SVG_W * $scaleF) / 2.0
$oy     = $pad + ($avail - $SVG_H * $scaleF) / 2.0
Write-Host "sz=$sz scaleF=$scaleF ox=$ox oy=$oy"

$rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap($sz, $sz, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
$dv  = New-Object System.Windows.Media.DrawingVisual
$dc  = $dv.RenderOpen()

# Background
$bg = [System.Windows.Media.Color]::FromArgb(255, 13, 14, 19)
$dc.DrawRectangle((New-Object System.Windows.Media.SolidColorBrush($bg)), $null, [System.Windows.Rect]::new(0,0,$sz,$sz))

# Transform
$tg = New-Object System.Windows.Media.TransformGroup
$tg.Children.Add([System.Windows.Media.ScaleTransform]::new($scaleF, $scaleF))
$tg.Children.Add([System.Windows.Media.TranslateTransform]::new($ox, $oy))
$dc.PushTransform($tg)

# Gold gradient
$stops = New-Object System.Windows.Media.GradientStopCollection
$stops.Add([System.Windows.Media.GradientStop]::new([System.Windows.Media.Color]::FromArgb(255,138,84,3),   0.0))
$stops.Add([System.Windows.Media.GradientStop]::new([System.Windows.Media.Color]::FromArgb(255,234,179,44), 0.19))
$stops.Add([System.Windows.Media.GradientStop]::new([System.Windows.Media.Color]::FromArgb(255,253,229,128),0.52))
$stops.Add([System.Windows.Media.GradientStop]::new([System.Windows.Media.Color]::FromArgb(255,224,167,39), 0.76))
$stops.Add([System.Windows.Media.GradientStop]::new([System.Windows.Media.Color]::FromArgb(255,145,89,2),   1.0))
$gold = New-Object System.Windows.Media.LinearGradientBrush($stops, [System.Windows.Point]::new(0,0), [System.Windows.Point]::new(0,$SVG_H))

$dc.DrawGeometry($gold, $null, [System.Windows.Media.Geometry]::Parse($p1))
$dc.DrawGeometry($gold, $null, [System.Windows.Media.Geometry]::Parse($p2))
$dc.Pop()
$dc.Close()
$rtb.Render($dv)

# Sample multiple pixels
$buf = New-Object byte[]($sz * $sz * 4)
$rtb.CopyPixels($buf, $sz * 4, 0)
Write-Host "Pixel (50,30)   R=$($buf[(30*$sz+50)*4+2])"
Write-Host "Pixel (100,100) R=$($buf[(100*$sz+100)*4+2])"
Write-Host "Pixel (180,200) R=$($buf[(200*$sz+180)*4+2])"
Write-Host "Pixel (50,200)  R=$($buf[(200*$sz+50)*4+2])"

# Save PNG
$enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
$enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
$fs = [System.IO.File]::OpenWrite("preview_icon.png")
$enc.Save($fs); $fs.Close()
Write-Host "Saved preview_icon.png ($((Get-Item preview_icon.png).Length) bytes)"
