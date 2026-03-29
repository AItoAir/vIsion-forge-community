[CmdletBinding()]
param(
    [string]$Profile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-Profile {
    param(
        [string]$Value
    )

    $rawValue = if ($null -eq $Value) { "" } else { $Value }
    $normalized = $rawValue.Trim().ToLowerInvariant()
    switch ($normalized) {
        "" { return "cpu" }
        "cpu" { return "cpu" }
        "gpu" { return "gpu" }
        "cloud" { return "cloud" }
        "dev" { return "gpu" }
        "stg" { return "cloud" }
        "prod" { return "cloud" }
        default {
            throw "Unknown verification profile '$Value'. Use cpu, gpu, or cloud."
        }
    }
}

function Test-TruthyString {
    param(
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    return @("1", "true", "yes", "on") -contains $Value.Trim().ToLowerInvariant()
}

$RequestedProfile = if ($Profile) {
    $Profile
} elseif ($env:LF_VERIFY_PROFILE) {
    $env:LF_VERIFY_PROFILE
} elseif ($env:LF_RUNTIME_PROFILE) {
    $env:LF_RUNTIME_PROFILE
} else {
    "cpu"
}

$ResolvedProfile = Resolve-Profile -Value $RequestedProfile

$RepoRoot = Split-Path -Path $PSScriptRoot -Parent
$ManageScript = Join-Path $RepoRoot "manage_vision_forge.bat"
$CanonicalEnvFile = if ($env:LF_ENV_FILE) { $env:LF_ENV_FILE } else { Join-Path $RepoRoot ".env" }
$ExampleEnvFile = Join-Path $RepoRoot ".env.$ResolvedProfile.example"
$FallbackEnvFile = Join-Path $RepoRoot ".env.example"
$Port = if ($env:LF_VERIFY_PORT) {
    [int]$env:LF_VERIFY_PORT
} elseif ($env:LF_PUBLIC_PORT) {
    [int]$env:LF_PUBLIC_PORT
} else {
    8001
}
$TimeoutSeconds = if ($env:LF_VERIFY_TIMEOUT_SECONDS) {
    [int]$env:LF_VERIFY_TIMEOUT_SECONDS
} else {
    180
}
$KeepRunning = Test-TruthyString -Value $env:LF_VERIFY_KEEP_RUNNING
$SkipEnvCopy = Test-TruthyString -Value $env:LF_VERIFY_SKIP_ENV_COPY
$HealthUrl = "http://127.0.0.1:$Port/healthz"
$VerificationProjectName = "vision-forge-verify-$ResolvedProfile-$PID"
$VerificationEnvFile = Join-Path ([System.IO.Path]::GetTempPath()) "vision-forge-verify-$ResolvedProfile-$PID.env"
$VerificationPassed = $false
$ExitCode = 0
$OriginalSkipDockerPrune = $env:LF_SKIP_DOCKER_PRUNE
$OriginalEnvFileOverride = $env:LF_ENV_FILE

function Ensure-ParentDirectory {
    param(
        [string]$PathValue
    )

    $parent = Split-Path -Path $PathValue -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Ensure-EnvFile {
    if (Test-Path -LiteralPath $CanonicalEnvFile) {
        Write-Host "[INFO] Using existing env file: $CanonicalEnvFile"
        return
    }

    Ensure-ParentDirectory -PathValue $CanonicalEnvFile

    if (Test-Path -LiteralPath $ExampleEnvFile) {
        Copy-Item -LiteralPath $ExampleEnvFile -Destination $CanonicalEnvFile
        Write-Host "[INFO] Created env file from profile template: $ExampleEnvFile"
        return
    }

    if (Test-Path -LiteralPath $FallbackEnvFile) {
        Copy-Item -LiteralPath $FallbackEnvFile -Destination $CanonicalEnvFile
        Write-Host "[INFO] Created env file from fallback template: $FallbackEnvFile"
        return
    }

    throw "No .env file exists and no template was found for profile '$ResolvedProfile'."
}

function Write-VerificationEnvFile {
    Ensure-ParentDirectory -PathValue $VerificationEnvFile

    if (Test-Path -LiteralPath $CanonicalEnvFile) {
        Copy-Item -LiteralPath $CanonicalEnvFile -Destination $VerificationEnvFile -Force
    }
    else {
        Set-Content -LiteralPath $VerificationEnvFile -Value "" -NoNewline
    }

    Add-Content -LiteralPath $VerificationEnvFile -Value ""
    Add-Content -LiteralPath $VerificationEnvFile -Value "LF_PROJECT_NAME=$VerificationProjectName"
    Add-Content -LiteralPath $VerificationEnvFile -Value "LF_PUBLIC_PORT=$Port"
    $env:LF_ENV_FILE = $VerificationEnvFile
}

function Invoke-Manage {
    param(
        [string[]]$Arguments
    )

    Push-Location $RepoRoot
    try {
        & $ManageScript @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: manage_vision_forge.bat $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Test-Healthz {
    try {
        $response = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 5
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

try {
    if (-not (Test-Path -LiteralPath $ManageScript)) {
        throw "Management script was not found: $ManageScript"
    }

    if (-not $SkipEnvCopy) {
        Ensure-EnvFile
    }

    Write-VerificationEnvFile

    if (-not $env:LF_SKIP_DOCKER_PRUNE) {
        $env:LF_SKIP_DOCKER_PRUNE = "1"
    }

    Write-Host "[INFO] Running VisionForge startup verification..."
    Write-Host "[INFO] Profile: $ResolvedProfile"
    Write-Host "[INFO] Project name: $VerificationProjectName"
    Write-Host "[INFO] Health URL: $HealthUrl"

    Invoke-Manage -Arguments @($ResolvedProfile, "up-build")

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Healthz) {
            $VerificationPassed = $true
            Write-Host "[INFO] Health check passed: $HealthUrl"
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $VerificationPassed) {
        throw "Health check did not pass within $TimeoutSeconds seconds: $HealthUrl"
    }
}
catch {
    Write-Error $_
    $ExitCode = 1
}
finally {
    if (-not $KeepRunning) {
        try {
            Write-Host "[INFO] Stopping verification stack..."
            Invoke-Manage -Arguments @($ResolvedProfile, "down")
        }
        catch {
            Write-Warning "Failed to stop the verification stack cleanly: $($_.Exception.Message)"
        }
    }
    else {
        Write-Host "[INFO] Leaving the stack running because LF_VERIFY_KEEP_RUNNING is enabled."
    }

    if ($null -ne $OriginalSkipDockerPrune) {
        $env:LF_SKIP_DOCKER_PRUNE = $OriginalSkipDockerPrune
    }
    else {
        Remove-Item Env:LF_SKIP_DOCKER_PRUNE -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $VerificationEnvFile) {
        Remove-Item -LiteralPath $VerificationEnvFile -Force -ErrorAction SilentlyContinue
    }

    if ($null -ne $OriginalEnvFileOverride) {
        $env:LF_ENV_FILE = $OriginalEnvFileOverride
    }
    else {
        Remove-Item Env:LF_ENV_FILE -ErrorAction SilentlyContinue
    }
}

exit $ExitCode
