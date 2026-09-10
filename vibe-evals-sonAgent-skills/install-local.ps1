param(
    [string]$SkillsRoot = (Join-Path $env:USERPROFILE '.codex\skills'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python launcher `py` was not found. Install Python 3.10+ and retry.'
}
py -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ is required'"
$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = 'vibe-evals-bundle-finalize'
$source = Join-Path $packageRoot $name
$skillsRootResolved = [System.IO.Path]::GetFullPath($SkillsRoot)
$destination = Join-Path $skillsRootResolved $name
$backupRoot = Join-Path (Split-Path -Parent $skillsRootResolved) 'codex-skill-backups'

New-Item -ItemType Directory -Force -Path $skillsRootResolved | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) {
    throw "Distribution is incomplete: $source\SKILL.md is missing."
}
if (Test-Path -LiteralPath $destination) {
    if (-not $Force) {
        throw "Skill already exists: $destination. Re-run with -Force only after backing it up."
    }
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backup = Join-Path $backupRoot "$name-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString('N').Substring(0,8))"
    Move-Item -LiteralPath $destination -Destination $backup
    Write-Host "Existing skill moved to $backup"
}
Copy-Item -LiteralPath $source -Destination $destination -Recurse
Get-ChildItem -LiteralPath (Join-Path $destination 'scripts') -Filter '*.py' | ForEach-Object {
    py $_.FullName --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Python script import check failed: $($_.FullName)" }
}
Write-Host "Installed $name -> $destination"
Write-Host 'Restart Codex or start a new task so the skill is discovered.'
