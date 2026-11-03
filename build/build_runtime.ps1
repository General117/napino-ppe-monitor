# Builds the portable Python runtime in ..\runtime\python  (developer machine only, needs internet).
# The client PC never runs this - it receives the finished runtime folder.
#
#   powershell -ExecutionPolicy Bypass -File build\build_runtime.ps1 -HostPython C:\path\to\python.exe
#
# -HostPython: any Python 3.11 x64 with pip (used only to download/install the wheels).

param([Parameter(Mandatory = $true)][string]$HostPython)
$ErrorActionPreference = "Stop"

$Root    = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime\python"
$PyVer   = "3.11.9"
$Zip     = Join-Path $PSScriptRoot "python-$PyVer-embed-amd64.zip"

if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
New-Item -ItemType Directory -Force $Runtime | Out-Null

if (-not (Test-Path $Zip)) {
    Write-Host "Downloading embeddable Python $PyVer from python.org ..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $Zip
}
Expand-Archive $Zip -DestinationPath $Runtime

# Let the embedded interpreter see site-packages
$pth = Get-ChildItem $Runtime -Filter "python*._pth" | Select-Object -First 1
Set-Content -Encoding ascii $pth.FullName "python311.zip`r`n.`r`nLib\site-packages`r`nimport site"

Write-Host "Installing packages ..."
& $HostPython -m pip install --no-warn-script-location --only-binary=:all: --python-version 3.11 `
    --platform win_amd64 --target (Join-Path $Runtime "Lib\site-packages") -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Microsoft C++ runtime DLLs (app-local), so the client PC needs no Visual C++ Redistributable install
foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll") {
    $src = Join-Path $env:WINDIR "System32\$dll"
    if (Test-Path $src) { Copy-Item $src $Runtime -Force }
}

# Remove caches / tests to keep the package small
Get-ChildItem $Runtime -Recurse -Directory -Include "__pycache__", "tests" | Remove-Item -Recurse -Force
Write-Host "Runtime ready: $Runtime"
& (Join-Path $Runtime "python.exe") -c "import cv2, openvino, numpy, PIL, flask, waitress; print('imports OK', cv2.__version__, openvino.__version__)"
