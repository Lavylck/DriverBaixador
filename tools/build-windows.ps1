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
& python -m PyInstaller --noconfirm --clean --windowed --onedir --name MeuXodoDrive --manifest tools/windows.manifest main.py
if ($LASTEXITCODE -ne 0) { throw 'Falha no PyInstaller.' }
$Package = Join-Path $ProjectRoot 'dist\MeuXodoDrive'
New-Item -ItemType Directory -Force (Join-Path $Package 'bin') | Out-Null
Copy-Item .\bin\rclone.exe, .\bin\RCLONE-LICENSE.txt (Join-Path $Package 'bin') -Force
Copy-Item .\README.md, .\LICENSE, .\THIRD_PARTY.md $Package -Force
Copy-Item .\licenses (Join-Path $Package 'licenses') -Recurse -Force
& python .\tools\verify_distribution.py $Package --smoke-test --report .\dist\windows-build-report.json
if ($LASTEXITCODE -ne 0) { throw 'Falhou a verificacao do executavel ou da inicializacao.' }
$Portable = Join-Path $ProjectRoot 'dist\MeuXodoDrive-Windows-x64.zip'
if (Test-Path $Portable) { Remove-Item $Portable -Force }
Compress-Archive -Path $Package -DestinationPath $Portable -CompressionLevel Optimal
if (-not $PortableOnly) {
    $ISCC = @(
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
    & python .\tools\verify_distribution.py $Package --smoke-test --installer .\dist\MeuXodoDrive-Setup-x64.exe --install-smoke-test --report .\dist\windows-build-report.json
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao verificar o instalador.' }
}
$Assets = @(Get-ChildItem .\dist -File | Where-Object { $_.Name -match '\.(exe|zip|json)$' })
$Lines = foreach ($File in $Assets) {
    '{0}  {1}' -f (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $File.Name
}
[IO.File]::WriteAllLines((Join-Path $ProjectRoot 'dist\SHA256SUMS.txt'), $Lines, [Text.UTF8Encoding]::new($false))
Write-Host 'Distribuicao verificada disponivel em dist/.'
