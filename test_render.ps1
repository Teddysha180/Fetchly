# test_render.ps1 — verify WPF can actually render geometry to pixels
Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase

$sz  = 64
$rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap($sz, $sz, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
$dv  = New-Object System.Windows.Media.DrawingVisual
$dc  = $dv.RenderOpen()

# Blue background
$dc.DrawRectangle([System.Windows.Media.Brushes]::DarkBlue, $null, [System.Windows.Rect]::new(0,0,$sz,$sz))

# Scale 0.1 then draw a 20x20 rectangle in SVG coords (200x200 * 0.1 = 20px at top left)
$tg = New-Object System.Windows.Media.TransformGroup
$tg.Children.Add([System.Windows.Media.ScaleTransform]::new(0.1, 0.1))
$dc.PushTransform($tg)

$geom = [System.Windows.Media.Geometry]::Parse("M10,10 L200,10 L200,200 L10,200 Z")
$dc.DrawGeometry([System.Windows.Media.Brushes]::Gold, $null, $geom)
$dc.Pop()
$dc.Close()

$rtb.Render($dv)

# Read pixels (Pbgra32 = B,G,R,A per pixel)
$buf = New-Object byte[]($sz * $sz * 4)
$rtb.CopyPixels($buf, $sz * 4, 0)

$px0  = "B=$($buf[0]) G=$($buf[1]) R=$($buf[2]) A=$($buf[3])"   # top-left (should be DarkBlue)
$px5  = $p = 5*$sz+5;  "B=$($buf[$p*4]) G=$($buf[$p*4+1]) R=$($buf[$p*4+2])"  # inside rect (gold)
$gold = $buf[(5*$sz+5)*4+2]  # R channel at (5,5)

Write-Host "Pixel (0,0) BG  : $px0"
Write-Host "Gold R at (5,5) : $gold  (expect ~255 for gold)"

if ($gold -gt 200) {
    Write-Host "PASS - geometry renders correctly"
} else {
    Write-Host "FAIL - geometry appears black/empty"
}

# Also save as PNG to inspect visually
$enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
$enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
$fs  = [System.IO.File]::OpenWrite("test_render.png")
$enc.Save($fs)
$fs.Close()
Write-Host "Saved test_render.png"
