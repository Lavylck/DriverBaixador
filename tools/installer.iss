#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
#ifndef ProjectRoot
  #define ProjectRoot ".."
#endif
[Setup]
AppId={{918AF946-B705-4FE2-8A4E-997E5D7EAE37}
AppName=MeuXodo Drive
AppVersion={#AppVersion}
AppPublisher=MeuXodo Drive
DefaultDirName={localappdata}\Programs\MeuXodoDrive
DefaultGroupName=MeuXodo Drive
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#ProjectRoot}\dist
OutputBaseFilename=MeuXodoDrive-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\MeuXodoDrive.exe
CloseApplications=yes
RestartApplications=no
LicenseFile={#ProjectRoot}\LICENSE
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na area de trabalho"; GroupDescription: "Atalhos:"
[Files]
Source: "{#ProjectRoot}\dist\MeuXodoDrive\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\MeuXodo Drive"; Filename: "{app}\MeuXodoDrive.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\MeuXodo Drive"; Filename: "{app}\MeuXodoDrive.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\MeuXodoDrive.exe"; Description: "Abrir MeuXodo Drive"; Flags: nowait postinstall skipifsilent
