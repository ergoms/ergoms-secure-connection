#ifndef AppVersion
#define AppVersion "1.0.0"
#endif

#define AppName "ERGOMS SECURE CONNECTION"
#define AppExeName "ErgomsSecureConnection.exe"

[Setup]
AppId={{8C3E2A71-9B54-4F0E-9D6A-1E7C4B8F2D90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ERGOMS
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ErgomsSecureConnection-Setup-{#AppVersion}
SetupIconFile=..\desktop\app_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked
Name: "autostart"; Description: "Автозапуск при входе в Windows"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\ErgomsSecureConnection\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"" --autostart"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "off"; Flags: runhidden waituntilterminated; RunOnceId: "ErgomsOff"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ERGOMS SECURE CONNECTION"" /F"; Flags: runhidden waituntilterminated; RunOnceId: "ErgomsTaskDemand"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ERGOMS SECURE CONNECTION (автозапуск)"" /F"; Flags: runhidden waituntilterminated; RunOnceId: "ErgomsTaskAutostart"
