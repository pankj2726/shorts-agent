<#
.SYNOPSIS
    vault_sync.ps1 — Encrypts and syncs local secrets to Cloudflare R2 Vault and Google Drive history.

.DESCRIPTION
    Encrypts C:\Secrets\shorts-agent.env using asymmetric age cryptography (public key recipient)
    and pushes the encrypted ciphertext to:
      1. r2:workspace-cache/vault/shorts-agent.env.age (Current active)
      2. r2:workspace-cache/vault/history/shorts-agent-<timestamp>.env.age (Historical version archive)
      3. gdrive:data/vault-index.md (Audit ledger)

    SECURITY:
      - Encrypted locally before leaving disk.
      - Zero secret values printed, logged, or exposed in output.
#>

param(
    [string]$EnvPath = "C:\Secrets\shorts-agent.env",
    [string]$RecipientFile = "C:\Tools\ws\age-recipient.txt",
    [string]$R2Remote = "r2:workspace-cache",
    [string]$DriveRemote = "gdrive:"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $EnvPath)) {
    Write-Error "Source env file not found at: $EnvPath"
    exit 1
}

if (-not (Test-Path $RecipientFile)) {
    Write-Error "Public recipient file not found at: $RecipientFile"
    exit 2
}

$recipient = (Get-Content $RecipientFile -Raw).Trim()
if (-not $recipient -or -not $recipient.StartsWith("age1")) {
    Write-Error "Invalid age recipient in $RecipientFile"
    exit 3
}

# Resolve rclone and age
$rcloneExe = if ($env:RCLONE_EXE) { $env:RCLONE_EXE } else { "rclone.exe" }
$ageExe = "age.exe"

$tempEnc = [System.IO.Path]::GetTempFileName()
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

try {
    Write-Host "1. Encrypting $EnvPath with age recipient ($($recipient.Substring(0, 10))...)..." -ForegroundColor Cyan
    & $ageExe -r $recipient -o "$tempEnc" "$EnvPath"
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tempEnc)) {
        Write-Error "age encryption failed with exit code $LASTEXITCODE"
        exit 4
    }

    $encSize = (Get-Item $tempEnc).Length
    Write-Host "   Encrypted payload created ($encSize bytes of ciphertext)." -ForegroundColor Green

    # 2. Upload active version to R2 vault
    Write-Host "2. Uploading active encrypted secret to $R2Remote/vault/shorts-agent.env.age..." -ForegroundColor Cyan
    & $rcloneExe copyto "$tempEnc" "$R2Remote/vault/shorts-agent.env.age"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to copy active secret to R2 (code $LASTEXITCODE)"
        exit 5
    }

    # 3. Upload historical version archive to R2 vault/history/
    $histDest = "$R2Remote/vault/history/shorts-agent-$stamp.env.age"
    Write-Host "3. Archiving historical version to $histDest..." -ForegroundColor Cyan
    & $rcloneExe copyto "$tempEnc" "$histDest"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not push historical snapshot to R2 history"
    }

    # 4. Update audit index in Google Drive
    Write-Host "4. Updating audit ledger in $DriveRemote`data/vault-index.md..." -ForegroundColor Cyan
    try {
        $tempIndex = [System.IO.Path]::GetTempFileName()
        $entry = "- [shorts-agent.env] (vault/shorts-agent.env.age) - Snapshot: $stamp - Added: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
        & $rcloneExe cat "$DriveRemote`data/vault-index.md" 2>$null | Out-File -FilePath $tempIndex -Encoding utf8
        Add-Content -Path $tempIndex -Value $entry
        & $rcloneExe copyto "$tempIndex" "$DriveRemote`data/vault-index.md" 2>$null
        Remove-Item $tempIndex -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "Could not update Drive vault index: $_"
    }

    Write-Host "`nVAULT SYNC SUCCESSFUL: Secrets securely encrypted & backed up to Cloudflare R2 and Drive." -ForegroundColor Green
    Write-Host "Active:  $R2Remote/vault/shorts-agent.env.age"
    Write-Host "History: $histDest"
}
finally {
    if (Test-Path $tempEnc) {
        Remove-Item $tempEnc -Force -ErrorAction SilentlyContinue
    }
}
