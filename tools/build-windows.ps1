param([switch]$SkipTests, [switch]$PortableOnly)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'O executavel Windows deve ser compilado no Windows.' }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if (-not [Environment]::Is64BitProcess) { throw 'Use Python e PowerShell de 64 bits.' }
if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Testes falharam. Nenhuma distribuicao sera produzida.' }
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot 'download-rclone.ps1')
& python -m PyInstaller --noconfirm --clean --windowed --onedir --name DriverDownloader --icon assets/icons/driver_downloader_icon.ico --add-data "assets/icons/driver_downloader_icon.png;assets/icons" --manifest tools/windows.manifest main.py
if ($LASTEXITCODE -ne 0) { throw 'Falha no PyInstaller.' }
$Package = Join-Path $ProjectRoot 'dist\DriverDownloader'
New-Item -ItemType Directory -Force (Join-Path $Package 'bin') | Out-Null
Copy-Item .\bin\rclone.exe, .\bin\RCLONE-LICENSE.txt (Join-Path $Package 'bin') -Force
Copy-Item .\README.md, .\LICENSE, .\THIRD_PARTY.md $Package -Force
Copy-Item .\licenses (Join-Path $Package 'licenses') -Recurse -Force
& python .\tools\verify_distribution.py $Package --smoke-test --report .\dist\windows-build-report.json
if ($LASTEXITCODE -ne 0) { throw 'Falhou a verificacao do executavel ou da inicializacao.' }
$Portable = Join-Path $ProjectRoot 'dist\DriverDownloader-Windows-x64.zip'
if (Test-Path $Portable) { Remove-Item $Portable -Force }
Compress-Archive -Path $Package -DestinationPath $Portable -CompressionLevel Optimal
if (-not $PortableOnly) {
    $ISCC = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $ISCC) {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) { $ISCC = $Command.Source }
    }
    if (-not $ISCC) {
        $ISCC = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Inno Setup*\ISCC.exe", "$env:ProgramFiles\Inno Setup*\ISCC.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $ISCC) { throw 'Inno Setup 6 nao encontrado. Instale-o para gerar o Setup, ou use -PortableOnly.' }
    & $ISCC '/DAppVersion=2.0.0' "/DProjectRoot=$ProjectRoot" .\tools\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o instalador.' }
    $InstallerCheck = @($Package, '--smoke-test', '--installer', '.\dist\DriverDownloader-Setup-x64.exe', '--report', '.\dist\windows-build-report.json')
    # O mesmo AppId atualiza a instalação existente: nunca executar o teste de instalação sobre ela.
    if (-not (Test-Path (Join-Path $env:LOCALAPPDATA 'Programs\MeuXodoDrive'))) {
        $InstallerCheck += '--install-smoke-test'
    } else {
        Write-Warning 'Instalacao anterior detectada: teste de instalacao isolada reservado ao GitHub Actions.'
    }
    & python .\tools\verify_distribution.py @InstallerCheck
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao verificar o instalador.' }
}
$Assets = @('DriverDownloader-Setup-x64.exe', 'DriverDownloader-Windows-x64.zip', 'windows-build-report.json') |
    ForEach-Object { Get-Item (Join-Path $ProjectRoot "dist\$_") -ErrorAction SilentlyContinue }
$Lines = foreach ($File in $Assets) {
    '{0}  {1}' -f (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $File.Name
}
[IO.File]::WriteAllLines((Join-Path $ProjectRoot 'dist\SHA256SUMS.txt'), $Lines, [Text.UTF8Encoding]::new($false))
Write-Host 'Distribuicao verificada disponivel em dist/.'
