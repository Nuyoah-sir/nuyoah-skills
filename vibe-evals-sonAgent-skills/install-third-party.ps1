param(
    [string]$SkillsRoot = (Join-Path $env:USERPROFILE '.codex\skills'),
    [switch]$Force,
    # Diagnostic-only switch used by the installer test suite to prove rollback.
    [string]$FailAfterSkill = ''
)

$ErrorActionPreference = 'Stop'
$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python launcher `py` was not found. Install Python 3.10+ and retry.'
}
py -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ is required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$skills = @('vibe-evals-son-evidence-export', 'vibe-evals-son-evidence-supplement', 'vibe-evals-son-complete-eval')
$validator = Join-Path $packageRoot 'shared\scripts\validate_skill_layout.py'
$manifestTool = Join-Path $packageRoot 'shared\scripts\build_runtime_manifest.py'
$systemValidator = Join-Path $env:USERPROFILE '.codex\skills\.system\skill-creator\scripts\quick_validate.py'

function Test-SkillPayload {
    param([string]$Path)
    & py $manifestTool verify $Path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Runtime manifest check failed: $Path" }
    & py $validator $Path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Skill layout check failed: $Path" }
    if (Test-Path -LiteralPath $systemValidator) {
        & py $systemValidator $Path | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "System skill validator rejected: $Path" }
    }
    $scripts = Join-Path $Path 'scripts'
    if (-not (Test-Path -LiteralPath $scripts)) { throw "Skill ships no scripts directory: $Path" }
    Get-ChildItem -LiteralPath $scripts -Filter '*.py' | ForEach-Object {
        & py $_.FullName --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Python script --help failed: $($_.FullName)" }
    }
}

$skillsRootResolved = [System.IO.Path]::GetFullPath($SkillsRoot)
$parentRoot = Split-Path -Parent $skillsRootResolved
$backupRoot = Join-Path $parentRoot 'codex-skill-backups'
$stagingRoot = Join-Path $parentRoot ('.codex-skill-staging-' + [guid]::NewGuid().ToString('N').Substring(0, 8))

# --- preflight: nothing installed has been touched yet -----------------------
foreach ($name in $skills) {
    $source = Join-Path $packageRoot $name
    if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) {
        throw "Distribution is incomplete: $source\SKILL.md is missing."
    }
}
New-Item -ItemType Directory -Force -Path $skillsRootResolved | Out-Null
if (-not $Force) {
    foreach ($name in $skills) {
        $destination = Join-Path $skillsRootResolved $name
        if (Test-Path -LiteralPath $destination) {
            throw "Skill already exists: $destination. Re-run with -Force only after backing it up."
        }
    }
}

# --- staging: copy everything and validate it before the swap ----------------
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
$installed = @()
try {
    foreach ($name in $skills) {
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination (Join-Path $stagingRoot $name) -Recurse
        Test-SkillPayload (Join-Path $stagingRoot $name)
    }

    foreach ($name in $skills) {
        $staged = Join-Path $stagingRoot $name
        $destination = Join-Path $skillsRootResolved $name
        $entry = [pscustomobject]@{ Name = $name; Destination = $destination; Backup = $null; Created = $false }
        if (Test-Path -LiteralPath $destination) {
            New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            $backup = Join-Path $backupRoot "$name-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString('N').Substring(0,8))"
            Move-Item -LiteralPath $destination -Destination $backup
            $entry.Backup = $backup
        } else {
            $entry.Created = $true
        }
        $installed += $entry
        Move-Item -LiteralPath $staged -Destination $destination
        Test-SkillPayload $destination
        Write-Host "Installed $name -> $destination"
        if ($FailAfterSkill -and $name -eq $FailAfterSkill) {
            throw "Diagnostic failure requested after installing $name."
        }
    }
} catch {
    foreach ($entry in ($installed | Sort-Object -Property Destination -Descending)) {
        if (Test-Path -LiteralPath $entry.Destination) {
            Remove-Item -LiteralPath $entry.Destination -Recurse -Force
        }
        if ($entry.Backup) {
            Move-Item -LiteralPath $entry.Backup -Destination $entry.Destination
            Write-Warning "Restored $($entry.Destination)"
        } elseif ($entry.Created) {
            Write-Warning "Removed partial install $($entry.Destination)"
        }
    }
    throw
} finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}

Write-Host 'Installed skills:'
foreach ($name in $skills) { Write-Host "  - $name" }
Write-Host 'Restart Codex or start a new task so the skills are discovered.'
