; Inno Setup 6 — ops-content Windows installer
; Built by build-installer.ps1 after PyInstaller produces dist\OpsContent.exe

#define MyAppName "ops-content"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ops-content"
#define MyAppExeName "OpsContent.exe"
#define MyAppId "{{A7C3E91F-2B4D-4F18-9E6A-0D8C5B1A2F34}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ops-content
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=OpsContent-Setup-{#MyAppVersion}
SetupIconFile=..\desktop\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
InfoBeforeFile=
LicenseFile=
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать при входе в Windows"; GroupDescription: "Автозапуск:"; Flags: unchecked

[Files]
Source: "..\dist\OpsContent.exe"; DestDir: "{app}"; Flags: ignoreversion
; Marker so the app stores config under %LOCALAPPDATA%\ops-content
Source: "installed.flag"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить ops-content"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  // Leave %LOCALAPPDATA%\ops-content (config, keys, logs) intact on uninstall
end;
