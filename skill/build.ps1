# Sangeki RoopeR skill packaging (Windows / PowerShell).
# engine/ and rules/ at the repo root are the source of truth; copy them into
# dist/ and produce a zip uploadable to Claude.ai. No copies are kept in git
# (dist/ is gitignored).
#
#   powershell -File skill/build.ps1
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads a no-BOM script
# as the system ANSI codepage; multibyte (e.g. Japanese) comments corrupt tokens.
#
# Output: skill/dist/sangeki-rooper-rules/  and  skill/dist/sangeki-rooper-rules.zip

$ErrorActionPreference = "Stop"
$SkillDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $SkillDir
$Name = "sangeki-rooper-rules"
$Dist = Join-Path $SkillDir "dist"
$Pkg  = Join-Path $Dist $Name

if (Test-Path $Pkg) { Remove-Item -Recurse -Force $Pkg }
New-Item -ItemType Directory -Force -Path $Pkg | Out-Null

# Skill body
Copy-Item (Join-Path $SkillDir "SKILL.md")      $Pkg
Copy-Item (Join-Path $SkillDir "run_engine.py") $Pkg

# Deterministic engine (source of truth: repo-root engine/)
Copy-Item (Join-Path $RepoRoot "engine") (Join-Path $Pkg "engine") -Recurse
# Knowledge base (source of truth: repo-root rules/)
Copy-Item (Join-Path $RepoRoot "rules")  (Join-Path $Pkg "rules")  -Recurse

# Drop items the skill does not need (dev-only demo/README, caches)
foreach ($d in @("demo.py", "README.md")) {
    Get-ChildItem -Path (Join-Path $Pkg "engine") -Recurse -Force -Filter $d -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
}
Get-ChildItem -Path $Pkg -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

# Zip. NOTE: do NOT use Compress-Archive here. Windows PowerShell 5.1 writes zip
# entries with backslash separators (e.g. "sangeki-rooper-rules\SKILL.md"), which
# violates the ZIP spec. On Linux (Claude.ai backend) that unpacks as a single
# oddly-named file, not a folder, so the skill fails with "missing SKILL.md".
# Build the archive manually with forward-slash entry names instead.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = Join-Path $Dist ($Name + ".zip")
if (Test-Path $Zip) { Remove-Item -Force $Zip }
$base = (Resolve-Path $Dist).Path.TrimEnd('\') + '\'
$archive = [System.IO.Compression.ZipFile]::Open($Zip, 'Create')
try {
    Get-ChildItem -Path $Pkg -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($base.Length) -replace '\\', '/'
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, $rel) | Out-Null
    }
} finally {
    $archive.Dispose()
}

Write-Host "built: $Pkg"
Write-Host "zip:   $Zip"
