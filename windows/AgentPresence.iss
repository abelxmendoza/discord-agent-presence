; Inno Setup script for AgentPresence-Windows-Setup.exe. Built by
; windows\build_installer.ps1, which defines AppVersion, BuildDir and OutDir.
; Installs for the current user only (no admin prompt), with its own Python.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\build\windows"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{793F8D73-3D6C-48C5-81E1-1EC9F2A6E0E3}
AppName=Agent Presence
AppVersion={#AppVersion}
AppVerName=Agent Presence {#AppVersion}
AppPublisher=Abel Mendoza
AppPublisherURL=https://abelxmendoza.github.io/discord-agent-presence/
AppSupportURL=https://github.com/abelxmendoza/discord-agent-presence/issues
AppUpdatesURL=https://github.com/abelxmendoza/discord-agent-presence/releases/latest
DefaultDirName={localappdata}\Programs\Agent Presence
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=Agent Presence
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile={#BuildDir}\AgentPresence.ico
UninstallDisplayIcon={app}\AgentPresence.ico
UninstallDisplayName=Agent Presence
WizardStyle=modern
ChangesEnvironment=yes
OutputDir={#OutDir}
OutputBaseFilename=AgentPresence-Windows-Setup
Compression=lzma2/max
SolidCompression=yes

[Messages]
WelcomeLabel2=This installs Agent Presence {#AppVersion}: Discord Rich Presence for Claude, ChatGPT / Codex, Cursor, VS Code and other AI coding tools.%n%nIt runs quietly in the background and starts every time you log in. Python is included, so there's nothing else to set up.
FinishedHeadingLabel=Agent Presence is running
FinishedLabelNoIcons=Open the Discord desktop app and start coding. Your card shows up within a few seconds.%n%nIn a new terminal, "agent-presence doctor" checks what was found and "agent-presence detect" previews your cards.
FinishedLabel=Open the Discord desktop app and start coding. Your card shows up within a few seconds.%n%nIn a new terminal, "agent-presence doctor" checks what was found and "agent-presence detect" previews your cards.

[Files]
Source: "{#BuildDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildDir}\AgentPresence.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Agent Presence status"; Filename: "{cmd}"; Parameters: "/k ""{app}\agent-presence.cmd"" doctor"; IconFilename: "{app}\AgentPresence.ico"; Comment: "Check Discord, window detection and which apps are found"
Name: "{group}\Restart Agent Presence"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\ai_presence.py"" setup --quiet"; IconFilename: "{app}\AgentPresence.ico"
Name: "{group}\Uninstall Agent Presence"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\python\python.exe"; Parameters: """{app}\ai_presence.py"" setup --quiet"; Flags: runhidden; StatusMsg: "Starting Agent Presence..."
Filename: "{cmd}"; Parameters: "/k ""{app}\agent-presence.cmd"" doctor"; Description: "Show the status check (Discord and the apps it found)"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\ai_presence.py"" uninstall"; Flags: runhidden; RunOnceId: "StopAgentPresence"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function PathList(): string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Result) then
    Result := '';
end;

function NeedsAddPath(Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(PathList()) + ';') = 0;
end;

procedure RemovePath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  Paths := ';' + PathList() + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Paths);
end;

// An upgrade can't replace python files while the old copy is running.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  if FileExists(ExpandConstant('{app}\python\python.exe')) then
    Exec(ExpandConstant('{app}\python\python.exe'),
         '"' + ExpandConstant('{app}\ai_presence.py') + '" uninstall',
         '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Old: string;
begin
  // A PowerShell (install.ps1) install put its own agent-presence.cmd on PATH
  // first; point it here so both names run this copy.
  Old := ExpandConstant('{localappdata}\discord-agent-presence\agent-presence.cmd');
  if (CurStep = ssPostInstall) and FileExists(Old) then
    SaveStringToFile(Old, '@"' + ExpandConstant('{app}\agent-presence.cmd') + '" %*' + #13#10, False);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePath(ExpandConstant('{app}'));
end;
