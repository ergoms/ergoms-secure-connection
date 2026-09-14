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
CloseApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
UsePreviousTasks=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "removeold"; Description: "Удалить предыдущую версию"; GroupDescription: "Обновление:"; Flags: checkedonce
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: checkedonce
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

procedure SetWizardStatus(const Title, Detail: String);
begin
  if WizardSilent then
    Exit;
  WizardForm.StatusLabel.Caption := Title;
  WizardForm.FilenameLabel.Caption := Detail;
  WizardForm.Update;
end;

procedure KillOne(const Image: String);
var
  ResultCode: Integer;
begin
  { No /T: tree-kill waits forever if a child is stuck or elevated. }
  Exec(ExpandConstant('{sys}\cmd.exe'),
    '/C taskkill /F /IM "' + Image + '" >nul 2>&1',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure KillAppProcesses;
begin
  KillOne('{#AppExeName}');
  KillOne('sing-box.exe');
  KillOne('ergoms-tun.exe');
  KillOne('ergoms-tun-awg.exe');
end;

procedure DeleteRunValue(const Name: String);
begin
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', Name);
end;

procedure WipeLeftovers;
var
  ResultCode: Integer;
  InstallDir: String;
begin
  { Data dir only. Do not delete Local\Programs\app when it is DestDir. }
  DelTree(ExpandConstant('{localappdata}\{#AppName}'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\ERGOMS VPN'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\ops-content'), True, True, True);
  InstallDir := ExpandConstant('{localappdata}\Programs\{#AppName}');
  if CompareText(InstallDir, ExpandConstant('{app}')) <> 0 then
    DelTree(InstallDir, True, True, True);
  Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="ERGOMS SECURE CONNECTION (sing-box)"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "ERGOMS SECURE CONNECTION" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "ERGOMS SECURE CONNECTION (автозапуск)" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  DeleteRunValue('{#AppName}');
  DeleteRunValue('ERGOMS VPN');
  DeleteRunValue('ops-content');
end;

function ExtractUninstallExe(const Raw: String): String;
var
  S: String;
  P: Integer;
begin
  S := Trim(Raw);
  if (Length(S) > 0) and (S[1] = '"') then
  begin
    Delete(S, 1, 1);
    P := Pos('"', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end
  else
  begin
    P := Pos(' /', S);
    if P > 0 then
      S := Copy(S, 1, P - 1);
  end;
  Result := Trim(S);
end;

procedure UninstallPrevious;
var
  Uninst: String;
  ResultCode: Integer;
begin
  Uninst := ExtractUninstallExe(GetUninstallString());
  if Uninst = '' then
    Exit;
  Exec(Uninst, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function WantRemoveOld(): Boolean;
begin
  Result := WizardIsTaskSelected('removeold') and (GetUninstallString() <> '');
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := '';
  if WantRemoveOld() then
    Result := Result + 'Удаление предыдущей версии' + NewLine +
      Space + 'Сначала в этом окне пойдёт удаление, затем установка.' + NewLine + NewLine
  else if GetUninstallString() <> '' then
    Result := Result + 'Предыдущая версия не удаляется — файлы будут обновлены.' + NewLine + NewLine;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssInstall then
    Exit;
  SetWizardStatus('Остановка запущенной программы…', '{#AppName}');
  KillAppProcesses;
  if not WantRemoveOld() then
  begin
    SetWizardStatus('Установка файлов…', '');
    Exit;
  end;
  SetWizardStatus('Удаление предыдущей версии…', 'Это может занять несколько секунд');
  UninstallPrevious;
  SetWizardStatus('Очистка данных предыдущей версии…', '');
  WipeLeftovers;
  SetWizardStatus('Установка файлов…', '');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    WipeLeftovers;
end;
