param([string]$Version = '1.76.0', [switch]$SkipExecutionCheck)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Execute este script no Windows.' }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Versao invalida.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Use o Windows de 64 bits.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ArchiveName = "rclone-v$Version-windows-amd64.zip"
$BaseUrl = "https://downloads.rclone.org/v$Version"
$TaskTemp = Join-Path ([IO.Path]::GetTempPath()) ('DriverDownloader-' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $TaskTemp | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host "Obtendo Rclone $Version do site oficial..."
    $ZipPath = Join-Path $TaskTemp $ArchiveName
    Invoke-WebRequest "$BaseUrl/$ArchiveName" -OutFile $ZipPath -UseBasicParsing
    $SumsPath = Join-Path $TaskTemp 'SHA256SUMS'
    Invoke-WebRequest "$BaseUrl/SHA256SUMS" -OutFile $SumsPath -UseBasicParsing
    $Pattern = '^([a-fA-F0-9]{64})\s+\*?' + [regex]::Escape($ArchiveName) + '\r?$'
    $Checksum = [regex]::Match((Get-Content $SumsPath -Raw), $Pattern, 'Multiline')
    if (-not $Checksum.Success) { throw 'SHA-256 oficial nao encontrado.' }
    if ((Get-FileHash $ZipPath -Algorithm SHA256).Hash -ne $Checksum.Groups[1].Value) {
        throw 'O SHA-256 diverge. O arquivo nao sera instalado.'
    }
    Expand-Archive -Path $ZipPath -DestinationPath $TaskTemp
    $Extracted = Join-Path $TaskTemp "rclone-v$Version-windows-amd64"
    $Bin = Join-Path $ProjectRoot 'bin'
    New-Item -ItemType Directory -Path $Bin -Force | Out-Null
    Copy-Item (Join-Path $Extracted 'rclone.exe') $Bin -Force
    $License = Join-Path $Extracted 'COPYING'
    if (-not (Test-Path $License)) {
        # O ZIP oficial Windows 1.76.0 nao inclui COPYING. O texto MIT oficial
        # dessa versao acompanha nossos fontes, sem depender de outro download.
        $License = Join-Path $ProjectRoot 'licenses\RCLONE-LICENSE.txt'
    }
    if (-not (Test-Path $License)) { throw 'A licenca MIT do Rclone nao foi encontrada.' }
    Copy-Item $License (Join-Path $Bin 'RCLONE-LICENSE.txt') -Force
    if (-not $SkipExecutionCheck) {
        & (Join-Path $Bin 'rclone.exe') version
        if ($LASTEXITCODE -ne 0) { throw 'O binario Rclone nao iniciou.' }
    }
    Write-Host 'Rclone verificado e instalado em bin/.'
} finally {
    Remove-Item $TaskTemp -Recurse -Force -ErrorAction SilentlyContinue
}
