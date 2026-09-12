#ifndef AppVersion
#define AppVersion "1.0.0"
#endif

#define AppName "ERGOMS SECURE CONNECTION"
#define AppExeName "ERGOMS SECURE CONNECTION.exe"
#define AppIdGuid "{8C3E2A71-9B54-4F0E-9D6A-1E7C4B8F2D90}"

[Setup]
AppId={{8C3E2A71-9B54-4F0E-9D6A-1E7C4B8F2D90}
AppName={#AppName}
AppVerName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ERGOMS
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=ERGOMS SECURE CONNECTION-Setup
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
Source: "..\dist\ERGOMS SECURE CONNECTION\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""ERGOMS SECURE CONNECTION (sing-box)"""; Flags: runhidden waituntilterminated; RunOnceId: "ErgomsFw"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#AppName}"
Type: filesandordirs; Name: "{localappdata}\ERGOMS VPN"
Type: filesandordirs; Name: "{localappdata}\ops-content"

[Code]
function UninstallKey(): String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdGuid}_is1';
end;

function GetUninstallString(): String;
var
  Uninst: String;
begin
  Uninst := '';
  if not RegQueryStringValue(HKCU, UninstallKey(), 'UninstallString', Uninst) then
    RegQueryStringValue(HKLM, UninstallKey(), 'UninstallString', Uninst);
  Result := Uninst;
end;

procedure KillAppProcesses;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#AppExeName}" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "sing-box.exe" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "ergoms-tun.exe" /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DeleteRunValue(const Name: String);
begin
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', Name);
end;

procedure WipeLeftovers;
var
  ResultCode: Integer;
begin
  DelTree(ExpandConstant('{localappdata}\{#AppName}'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\ERGOMS VPN'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\ops-content'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\Programs\{#AppName}'), True, True, True);
  Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="ERGOMS SECURE CONNECTION (sing-box)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "ERGOMS SECURE CONNECTION" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "ERGOMS SECURE CONNECTION (автозапуск)" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  DeleteRunValue('{#AppName}');
  DeleteRunValue('ERGOMS VPN');
  DeleteRunValue('ops-content');
end;

procedure UninstallPrevious;
var
  Uninst: String;
  ResultCode: Integer;
begin
  Uninst := GetUninstallString();
  if Uninst = '' then
    Exit;
  StringChangeEx(Uninst, '"', '', True);
  Exec(Uninst, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := '';
  KillAppProcesses;
  UninstallPrevious;
  WipeLeftovers;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    WipeLeftovers;
end;
