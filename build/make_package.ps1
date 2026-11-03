# Builds the client package NapinoPPE_<yyyyMMdd>.zip (+ .sha256) in -OutDir
# (default: <live_environment>\..\dist). The temporary copy is also made inside -OutDir, so nothing
# is written to any other drive.
#
#   powershell -ExecutionPolicy Bypass -File build\make_package.ps1 -OutDir E:\RenataBackup\NapinoPPE\dist
#
# Left out: run-time data (incidents, sign-in, preferences), logs, caches, and developer-only files
# (build\, demo / replay tools). The zip extracts to a single folder "live_environment".

param([string]$OutDir = "")
$ErrorActionPreference = "Stop"
$Root  = Split-Path -Parent $PSScriptRoot
$Dist  = if ($OutDir) { $OutDir } else { Join-Path (Split-Path -Parent $Root) "dist" }
$Stage = Join-Path $Dist ("_staging_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Out   = Join-Path $Dist ("NapinoPPE_" + (Get-Date -Format "yyyyMMdd") + ".zip")

New-Item -ItemType Directory -Force $Dist | Out-Null
$dst = Join-Path $Stage "live_environment"
robocopy $Root $dst /E /NFL /NDL /NJH /NJS /NP `
    /XD "$Root\data" "$Root\data_replay" "$Root\logs" "$Root\build" __pycache__ `
    /XF "demo_*.toml" RUN_DEMO.bat replay_check.py *.pyc .git .gitignore .gitattributes | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy failed ($LASTEXITCODE)" }

if (Test-Path $Out) { Remove-Item $Out -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
# Entries are written one by one with "/" separators (the zip standard); Windows PowerShell's
# CreateFromDirectory writes "\" which some unzip tools turn into odd file names.
$zip = [IO.Compression.ZipFile]::Open($Out, [IO.Compression.ZipArchiveMode]::Create)
try {
    $base = (Resolve-Path $Stage).Path.TrimEnd('\') + '\'
    Get-ChildItem $Stage -Recurse -File | ForEach-Object {
        $name = $_.FullName.Substring($base.Length).Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $name,
            [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {
    $zip.Dispose()
    Remove-Item -Recurse -Force $Stage
}

$hash = (Get-FileHash $Out -Algorithm SHA256).Hash
Set-Content -Encoding ascii "$Out.sha256" "$hash  $(Split-Path -Leaf $Out)"
"{0}  ({1:N0} MB)`nSHA256 {2}" -f $Out, ((Get-Item $Out).Length / 1MB), $hash
