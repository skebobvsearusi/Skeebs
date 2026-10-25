$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller==6.20.0
if ($LASTEXITCODE -ne 0) { throw 'Cannot install dependencies' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --name Skeebs --icon skeebs.ico --collect-data soundcard --collect-all dxcam --exclude-module matplotlib --exclude-module scipy --exclude-module pandas skeebs.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Copy-Item skeebs.ico,skeebs-icon.png,README.md,THIRD_PARTY.md -Destination dist\Skeebs
# Use the Windows ICU implementation if a foreign tool in PATH supplied one.
$internal = Join-Path $PSScriptRoot 'dist\Skeebs\_internal'
if (Test-Path -LiteralPath (Join-Path $env:WINDIR 'System32\icuuc.dll')) {
    $icu = Join-Path $internal 'icuuc.dll'
    if (Test-Path -LiteralPath $icu) { Remove-Item -LiteralPath $icu }
}
Write-Output 'Ready: dist\Skeebs\Skeebs.exe. Install FFmpeg separately.'
