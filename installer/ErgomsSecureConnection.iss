#ifndef AppVersion
#define AppVersion "1.2.10"
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
Name: "wipeconfigs"; Description: "Удалить сохранённые конфиги"; GroupDescription: "Обновление:"; Flags: unchecked; Check: HasUserData
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
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#AppName}\var"
Type: filesandordirs; Name: "{localappdata}\{#AppName}\logs"
Type: filesandordirs; Name: "{localappdata}\{#AppName}\tools"
Type: filesandordirs; Name: "{localappdata}\ERGOMS VPN\var"
Type: filesandordirs; Name: "{localappdata}\ERGOMS VPN\logs"
Type: filesandordirs; Name: "{localappdata}\ops-content\var"
Type: filesandordirs; Name: "{localappdata}\ops-content\logs"

[Code]
#ifdef UNICODE
  #define AW "W"
#else
  #define AW "A"
#endif

type
  TMsg = record
    hwnd: HWND;
    message: UINT;
    wParam: Longint;
    lParam: Longint;
    time: DWORD;
    pt: TPoint;
  end;

function PeekMessage(var lpMsg: TMsg; hWnd: HWND; wMsgFilterMin, wMsgFilterMax, wRemoveMsg: UINT): BOOL;
  external 'PeekMessage{#AW}@user32.dll stdcall';
function TranslateMessage(const lpMsg: TMsg): BOOL;
  external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(const lpMsg: TMsg): Longint;
  external 'DispatchMessage{#AW}@user32.dll stdcall';

const
  PM_REMOVE = 1;

var
  ExecSeq: Integer;
  DeleteConfigs: Boolean;

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

procedure ProcessMessages;
var
  Msg: TMsg;
begin
  while PeekMessage(Msg, 0, 0, 0, PM_REMOVE) do
  begin
    TranslateMessage(Msg);
    DispatchMessage(Msg);
  end;
end;

{ Wait without blocking the wizard thread — ewWaitUntilTerminated freezes UI. }
function ExecPumped(const Filename, Params: String; TimeoutMs: Integer): Integer;
var
  ResultCode: Integer;
  Marker: String;
  Elapsed: Integer;
  Cmd: String;
begin
  Result := -1;
  ExecSeq := ExecSeq + 1;
  Marker := ExpandConstant('{tmp}\ergoms-exec-') + IntToStr(ExecSeq) + '.done';
  DeleteFile(Marker);
  Cmd := '/d /s /c ""' + Filename + '" ' + Params + ' & (echo 1>"' + Marker + '")"';
  if not Exec(ExpandConstant('{sys}\cmd.exe'), Cmd, '', SW_HIDE, ewNoWait, ResultCode) then
    Exit;
  Elapsed := 0;
  while not FileExists(Marker) do
  begin
    ProcessMessages;
    Sleep(50);
    Elapsed := Elapsed + 50;
    if (TimeoutMs > 0) and (Elapsed >= TimeoutMs) then
    begin
      Result := -2;
      Exit;
    end;
  end;
  DeleteFile(Marker);
  Result := 0;
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

function GetPreviousInstallDir(): String;
var
  Dir, Uninst: String;
begin
  Dir := '';
  if not RegQueryStringValue(HKCU, UninstallKey(), 'InstallLocation', Dir) then
    RegQueryStringValue(HKLM, UninstallKey(), 'InstallLocation', Dir);
  Dir := RemoveBackslash(Trim(Dir));
  if (Dir <> '') and DirExists(Dir) then
  begin
    Result := Dir;
    Exit;
  end;
  Uninst := ExtractUninstallExe(GetUninstallString());
  if Uninst <> '' then
    Result := RemoveBackslash(ExtractFileDir(Uninst))
  else
    Result := '';
end;

procedure KillOne(const Image: String);
begin
  { No /T: tree-kill waits forever if a child is stuck or elevated. }
  ExecPumped(ExpandConstant('{sys}\taskkill.exe'),
    '/F /IM "' + Image + '"', 8000);
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

procedure WipeRuntimeDir(const Root: String);
begin
  if Root = '' then
    Exit;
  DelTree(Root + '\var', True, True, True);
  ProcessMessages;
  DelTree(Root + '\logs', True, True, True);
  ProcessMessages;
  DelTree(Root + '\tools', True, True, True);
end;

procedure WipeConfigsDir(const Root: String);
begin
  if Root = '' then
    Exit;
  DeleteFile(Root + '\config.json');
  DeleteFile(Root + '\amneziawg.conf');
  DeleteFile(Root + '\amneziawg.conf.name');
  DeleteFile(Root + '\.env');
  DelTree(Root + '\creds', True, True, True);
  ProcessMessages;
end;

procedure WipeLeftovers;
var
  InstallDir, DataDir: String;
begin
  { Runtime only. Keep loaded config.json / AmneziaWG / creds. }
  DataDir := ExpandConstant('{localappdata}\{#AppName}');
  WipeRuntimeDir(DataDir);
  WipeRuntimeDir(ExpandConstant('{localappdata}\ERGOMS VPN'));
  WipeRuntimeDir(ExpandConstant('{localappdata}\ops-content'));
  InstallDir := ExpandConstant('{localappdata}\Programs\{#AppName}');
  if CompareText(InstallDir, ExpandConstant('{app}')) <> 0 then
    DelTree(InstallDir, True, True, True);
  ExecPumped(ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule name="ERGOMS SECURE CONNECTION (sing-box)"', 15000);
  ExecPumped(ExpandConstant('{sys}\schtasks.exe'),
    '/Delete /TN "ERGOMS SECURE CONNECTION" /F', 10000);
  ExecPumped(ExpandConstant('{sys}\schtasks.exe'),
    '/Delete /TN "ERGOMS SECURE CONNECTION (автозапуск)" /F', 10000);
  DeleteRunValue('{#AppName}');
  DeleteRunValue('ERGOMS VPN');
  DeleteRunValue('ops-content');
end;

procedure RemovePreviousFiles;
var
  OldDir, DestDir: String;
begin
  OldDir := GetPreviousInstallDir();
  DestDir := ExpandConstant('{app}');
  if OldDir = '' then
    OldDir := DestDir;
  if not DirExists(OldDir) then
    Exit;
  DelTree(OldDir, True, True, True);
  ProcessMessages;
  if CompareText(OldDir, DestDir) = 0 then
    ForceDirectories(DestDir);
end;

function HasPreviousInstall(): Boolean;
begin
  Result := GetUninstallString() <> '';
end;

function HasUserData(): Boolean;
begin
  Result := HasPreviousInstall() or
    DirExists(ExpandConstant('{localappdata}\{#AppName}')) or
    DirExists(ExpandConstant('{localappdata}\ERGOMS VPN')) or
    DirExists(ExpandConstant('{localappdata}\ops-content'));
end;

function WantRemoveOld(): Boolean;
begin
  Result := HasPreviousInstall();
end;

function WantWipeConfigs(): Boolean;
begin
  Result := WizardIsTaskSelected('wipeconfigs');
end;

procedure WipeAllConfigs;
begin
  WipeConfigsDir(ExpandConstant('{localappdata}\{#AppName}'));
  WipeConfigsDir(ExpandConstant('{localappdata}\ERGOMS VPN'));
  WipeConfigsDir(ExpandConstant('{localappdata}\ops-content'));
end;

procedure ProgressStep(Page: TOutputProgressWizardPage; Step, MaxSteps: Integer;
  const Title, Detail: String);
begin
  if Page = nil then
    Exit;
  Page.SetText(Title, Detail);
  Page.SetProgress(Step, MaxSteps);
  ProcessMessages;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Page: TOutputProgressWizardPage;
begin
  Result := '';
  NeedsRestart := False;
  Page := nil;
  (* Do not run old unins000.exe: same AppId mutex + it launches exe off
     (slow frozen boot / UAC) while this wizard waits on the UI thread. *)
  if WantRemoveOld() or WantWipeConfigs() then
  begin
    Page := CreateOutputProgressPage(
      'Подготовка к установке',
      'Сначала очистка, затем установка новых файлов.');
    if not WizardSilent then
      Page.Show;
  end;
  try
    ProgressStep(Page, 0, 3, 'Остановка запущенной программы…', '{#AppName}');
    KillAppProcesses;
    if not WantRemoveOld() and not WantWipeConfigs() then
      Exit;
    ProgressStep(Page, 1, 3, 'Очистка данных…',
      'Профили, правила брандмауэра, задачи');
    if WantRemoveOld() then
      WipeLeftovers;
    if WantWipeConfigs() then
      WipeAllConfigs;
    if WantRemoveOld() then
    begin
      ProgressStep(Page, 2, 3, 'Удаление файлов предыдущей версии…',
        GetPreviousInstallDir());
      RemovePreviousFiles;
    end;
    ProgressStep(Page, 3, 3, 'Готово', 'Переход к установке…');
  finally
    if (Page <> nil) and not WizardSilent then
      Page.Hide;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := '';
  if WantRemoveOld() then
    Result := Result + 'Удаление предыдущей версии' + NewLine +
      Space + 'Сначала в этом окне пойдёт удаление, затем установка.' + NewLine + NewLine;
  if WantWipeConfigs() then
    Result := Result + 'Сохранённые конфиги будут удалены.' + NewLine + NewLine
  else if HasUserData() then
    Result := Result + 'Сохранённые конфиги остаются.' + NewLine + NewLine;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo;
end;

function InitializeUninstall(): Boolean;
begin
  DeleteConfigs := False;
  if not UninstallSilent then
  begin
    if MsgBox('Удалить сохранённые конфиги?'#13#10#13#10
         + 'По умолчанию config.json, AmneziaWG и ключи остаются.',
         mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DeleteConfigs := True;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    KillAppProcesses;
  if CurUninstallStep = usPostUninstall then
  begin
    WipeLeftovers;
    if DeleteConfigs then
      WipeAllConfigs;
  end;
end;
