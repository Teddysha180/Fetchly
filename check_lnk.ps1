$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut("C:\Users\lenovo\OneDrive\Desktop\Fetchly.lnk")
Write-Host "TargetPath:     $($lnk.TargetPath)"
Write-Host "IconLocation:   $($lnk.IconLocation)"
Write-Host "WorkingDirectory: $($lnk.WorkingDirectory)"
