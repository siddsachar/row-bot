; =============================================================================
; Row-Bot v5.3.1 - Inno Setup Script
; Self-contained per-user installer: bundles embedded Python with all pip
; packages pre-installed. No internet downloads at install time.
; =============================================================================
;
; Installs into %LOCALAPPDATA%\Programs\Row-Bot without administrator rights:
;   Row-Bot.exe                     starts the active version (installer\windows)
;   state.json                      names the active and previous versions
;   versions\<version>\app          the app payload
;   versions\<version>\python       embedded Python with the locked packages
;   versions\<version>\version.json
; See src/row_bot/install_layout.py for the layout contract.
;
; Prerequisites (placed in installer\build\ by build_installer.ps1):
;   build\python\       Embedded Python with all packages pre-installed
;   build\app\          The staged app payload with the client and compiled bytecode
;   build\Row-Bot.exe    The launcher, built with MSVC
;   build\version.json
;
; Compile through build_installer.ps1, which stages and validates the payload.
#ifndef AppStageDir
  #error AppStageDir is required. Run installer\build_installer.ps1 to stage and validate the app before compiling.
#endif

#ifnexist "build\python\Lib\site-packages\sentence_transformers\__init__.py"
  #error Embedded Python is missing sentence_transformers. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_huggingface\__init__.py"
  #error Embedded Python is missing langchain_huggingface. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\transformers\__init__.py"
  #error Embedded Python is missing transformers. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\torch\__init__.py"
  #error Embedded Python is missing torch. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\httpx\__init__.py"
  #error Embedded Python is missing httpx. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\keyring\__init__.py"
  #error Embedded Python is missing keyring. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_ollama\__init__.py"
  #error Embedded Python is missing langchain_ollama. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_openai\__init__.py"
  #error Embedded Python is missing langchain_openai. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_anthropic\__init__.py"
  #error Embedded Python is missing langchain_anthropic. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_google_genai\__init__.py"
  #error Embedded Python is missing langchain_google_genai. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_openrouter\__init__.py"
  #error Embedded Python is missing langchain_openrouter. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_xai\__init__.py"
  #error Embedded Python is missing langchain_xai. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\google\genai\__init__.py"
  #error Embedded Python is missing google.genai. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\openai\__init__.py"
  #error Embedded Python is missing openai. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\mcp\__init__.py"
  #error Embedded Python is missing mcp. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\youtube_search\__init__.py"
  #error Embedded Python is missing youtube_search. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\youtube_transcript_api\__init__.py"
  #error Embedded Python is missing youtube_transcript_api. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\Row-Bot.exe"
  #error Row-Bot.exe is missing. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\version.json"
  #error build\version.json is missing. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#define MyAppName      "Row-Bot"
#define MyAppVersion   "5.3.1"
#define MyAppPublisher "Row-Bot"
#define MyAppURL       "https://row-bot.ai"
#define MyAppUserModelID "ai.row-bot.desktop"
; Each version lives in its own folder; state.json at the root names the active one.
#define VersionDir     "{app}\versions\" + MyAppVersion
; The AppId of the 5.x installers, which installed for all users (Program Files, HKLM).
#define LegacyUninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per user, no administrator rights: %LOCALAPPDATA%\Programs\Row-Bot.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
; No wizard pages except the progress page.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
ShowLanguageDialog=no
OutputDir=..\dist
OutputBaseFilename=Row-Bot-{#MyAppVersion}-Windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\row-bot.ico
UninstallDisplayIcon={app}\Row-Bot.exe
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; Setup starts Row-Bot itself when it is done (see [Run]).
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; The progress page says what it is doing in a few plain steps, not every file it copies (B356).
StatusClosingApplications=Closing Row-Bot...
StatusCreateDirs=Copying Row-Bot...
StatusExtractFiles=Copying Row-Bot...
StatusCreateIcons=Creating shortcuts...
StatusCreateIniEntries=Finishing up...
StatusCreateRegistryEntries=Finishing up...
StatusRegisterFiles=Finishing up...
StatusSavingUninstall=Finishing up...
StatusRollback=Undoing the changes...

[InstallDelete]
; Reinstalling a version replaces its folder whole, so nothing a build no
; longer ships stays behind. User data lives in the profile folder, never
; under {app}.
Type: filesandordirs; Name: "{app}\versions\{#MyAppVersion}"
; Row-Bot.exe copies renamed by an earlier install while Row-Bot was running.
Type: files; Name: "{app}\Row-Bot.exe.*.old"

[Files]
Source: "build\Row-Bot.exe";      DestDir: "{app}"; Flags: ignoreversion
; App payload staged from scripts/app_payload_manifest.py, with the client and compiled bytecode.
Source: "{#AppStageDir}\*";       DestDir: "{#VersionDir}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
; Embedded Python with all packages pre-installed.
Source: "build\python\*";         DestDir: "{#VersionDir}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\version.json";     DestDir: "{#VersionDir}"; Flags: ignoreversion
; Runs the server beside python.exe, so Windows Firewall names Row-Bot, not Python (B355).
Source: "build\Row-Bot Server.exe"; DestDir: "{#VersionDir}\python"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\Row-Bot.exe"; AppUserModelID: "{#MyAppUserModelID}"; Comment: "Launch Row-Bot"
; The desktop shortcut comes with the first install only: an upgrade never brings back one the user removed.
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\Row-Bot.exe"; AppUserModelID: "{#MyAppUserModelID}"; Comment: "Launch Row-Bot"; Check: IsFirstInstall

[Run]
; Starts Row-Bot when Setup is done, also after /SILENT and /VERYSILENT (the
; 5.1 updater runs this installer silently). /NOLAUNCH skips it (CI smokes).
Filename: "{app}\Row-Bot.exe"; StatusMsg: "Starting Row-Bot..."; Flags: nowait runasoriginaluser; Check: ShouldLaunch

[UninstallDelete]
; The whole install root: every version, state.json and Row-Bot.exe. Never the
; user's data in %USERPROFILE%\.row-bot.
Type: filesandordirs; Name: "{app}\versions"
; Background updates keep their downloads and engine state here (src/row_bot/updates).
Type: filesandordirs; Name: "{app}\.updates"
Type: files; Name: "{app}\state.json"
Type: files; Name: "{app}\.state.*.tmp"
Type: files; Name: "{app}\Row-Bot.exe.*.old"
Type: dirifempty; Name: "{app}"

[Code]
const
  MOVEFILE_REPLACE_EXISTING = $1;
  MOVEFILE_WRITE_THROUGH = $8;

function MoveFileEx(ExistingFileName, NewFileName: string; Flags: Cardinal): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';

var
  FirstInstall: Boolean;
  LegacyInstallPending: Boolean;
  { An earlier Row-Bot (this user's, or a 5.x install for all users) is being replaced. }
  Updating: Boolean;

function HasParam(const Name: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function ShouldLaunch: Boolean;
begin
  Result := not HasParam('/NOLAUNCH');
end;

function IsFirstInstall: Boolean;
begin
  Result := FirstInstall;
end;

function LegacyInstallPresent: Boolean;
begin
  Result := RegKeyExists(HKLM64, '{#LegacyUninstallKey}') or RegKeyExists(HKLM32, '{#LegacyUninstallKey}');
end;

function LegacyUninstaller: string;
begin
  if not RegQueryStringValue(HKLM64, '{#LegacyUninstallKey}', 'UninstallString', Result) then
    if not RegQueryStringValue(HKLM32, '{#LegacyUninstallKey}', 'UninstallString', Result) then
      Result := '';
  Result := RemoveQuotes(Result);
end;

{ Removes a Row-Bot installed for all users by an earlier installer, with one
  administrator prompt, so Apps & features does not list Row-Bot twice. Returns
  False when it is still there: the prompt was declined, or the user is not an
  administrator. The per-user install goes ahead anyway; state.json records
  legacy_install_pending so the app can offer to remove it later. }
function RemoveLegacyInstall: Boolean;
var
  Uninstaller: string;
  ResultCode, Waited: Integer;
  Started: Boolean;
begin
  Result := True;
  if not LegacyInstallPresent then
    Exit;
  Uninstaller := LegacyUninstaller;
  Log('Removing the Row-Bot installed for all users: ' + Uninstaller);
  if (Uninstaller <> '') and FileExists(Uninstaller) then
    Started := ShellExec('runas', Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)
  else
    { Its folder is gone already: only the Apps & features entry is left. }
    Started := ShellExec('runas', ExpandConstant('{sys}\reg.exe'),
      'delete "HKLM\{#LegacyUninstallKey}" /f /reg:64', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if not Started then
  begin
    { A [Code] line must not start with "[": Inno Setup would read it as a section. }
    Log(Format('Could not remove the earlier Row-Bot (%d: %s); installing for this user anyway', [ResultCode,
      SysErrorMessage(ResultCode)]));
    Result := False;
    Exit;
  end;
  { The uninstaller hands over to a copy of itself, so wait until its entry is gone. }
  Waited := 0;
  while LegacyInstallPresent and (Waited < 180000) do
  begin
    Sleep(500);
    Waited := Waited + 500;
  end;
  Result := not LegacyInstallPresent;
  if Result then
    Log('The Row-Bot installed for all users was removed.')
  else
    Log('The Row-Bot installed for all users is still there; installing for this user anyway');
end;

{ A running Row-Bot.exe cannot be replaced, but it can be renamed; the new one
  then starts the new version and the old copy is deleted by the next install. }
procedure MoveRunningLauncherAside;
var
  Launcher: string;
begin
  Launcher := ExpandConstant('{app}\Row-Bot.exe');
  if FileExists(Launcher) and not DeleteFile(Launcher) then
    if MoveFileEx(Launcher, Launcher + '.' + GetDateTimeString('yyyymmddhhnnsszzz', #0, #0) + '.old',
      MOVEFILE_WRITE_THROUGH) then
      Log('Moved the running Row-Bot.exe aside')
    else
      Log('Could not move the running Row-Bot.exe aside');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  FirstInstall := not FileExists(ExpandConstant('{app}\state.json')) and
    not FileExists(ExpandConstant('{app}\Row-Bot.exe'));
  Updating := not FirstInstall or LegacyInstallPresent;
  LegacyInstallPending := not RemoveLegacyInstall;
  MoveRunningLauncherAside;
end;

{ state.json: this version becomes active and the one it replaces stays as
  previous (src/row_bot/install_layout.py, run by the version just installed).
  Older versions are deleted. }
procedure RecordInstall;
var
  Python, Params, State, Pending: string;
  ResultCode: Integer;
begin
  Python := ExpandConstant('{#VersionDir}\python\python.exe');
  Params := '-I -S "' + ExpandConstant('{#VersionDir}\app\src\row_bot\install_layout.py') +
    '" record-install --root "' + ExpandConstant('{app}') + '" --kind windows --version {#MyAppVersion} --prune';
  Pending := '';
  if LegacyInstallPending then
  begin
    Params := Params + ' --legacy-install-pending';
    Pending := ', "legacy_install_pending": true';
  end;
  if Exec(Python, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    Log('Recorded the install in state.json');
    Exit;
  end;
  Log(Format('install_layout record-install failed (%d); writing a minimal state.json', [ResultCode]));
  State := ExpandConstant('{app}\state.json');
  if SaveStringToFile(State + '.setup.tmp', '{"schema": 1, "active": "{#MyAppVersion}", "previous": null, ' +
    '"ready": null, "pending_verification": false, "unverified_launches": 0, "last_rollback": null' + Pending + '}' + #10,
    False) then
    MoveFileEx(State + '.setup.tmp', State, MOVEFILE_REPLACE_EXISTING or MOVEFILE_WRITE_THROUGH);
end;

procedure InitializeWizard;
begin
  { One status line, no file names (B356). }
  WizardForm.FilenameLabel.Visible := False;
end;

{ An update run by an earlier Row-Bot (5.0 and 5.1 run this with /SILENT and quit) says that Row-Bot
  closed on purpose and comes back by itself (B358). }
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID <> wpInstalling then
    Exit;
  if Updating then
  begin
    WizardForm.PageNameLabel.Caption := 'Updating Row-Bot';
    WizardForm.PageDescriptionLabel.Caption :=
      'Row-Bot closed to install the update. It opens again by itself when this finishes.';
  end
  else
  begin
    WizardForm.PageNameLabel.Caption := 'Installing Row-Bot';
    WizardForm.PageDescriptionLabel.Caption := 'Row-Bot opens by itself when this finishes.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Getting Row-Bot ready...';
    RecordInstall;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  while not UninstallSilent and CheckForMutexes('RowBotLauncher') do
    if MsgBox('Row-Bot is running. Quit it from its icon in the notification area, then click OK.',
      mbInformation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;
