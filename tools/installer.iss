#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif
[Setup]
AppId={{918AF946-B705-4FE2-8A4E-997E5D7EAE37}
AppName=Driver Downloader
AppVersion={#AppVersion}
AppPublisher=Driver Downloader
; Preserve o diretório de instalação anterior durante a atualização (mesmo AppId).
DefaultDirName={localappdata}\Programs\MeuXodoDrive
DefaultGroupName=Driver Downloader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=DriverDownloader-Setup-x64
SetupIconFile={#ProjectRoot}\assets\icons\driver_downloader_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\DriverDownloader.exe
CloseApplications=yes
RestartApplications=no
LicenseFile={#ProjectRoot}\LICENSE
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na area de trabalho"; GroupDescription: "Atalhos:"
[Files]
Source: "{#ProjectRoot}\dist\DriverDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Driver Downloader"; Filename: "{app}\DriverDownloader.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Driver Downloader"; Filename: "{app}\DriverDownloader.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\DriverDownloader.exe"; Description: "Abrir Driver Downloader"; Flags: nowait postinstall skipifsilent
