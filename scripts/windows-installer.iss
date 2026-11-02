; ============================================================================
; LocalRouter - Windows installer (Inno Setup 6)
;
; Compiled automatically by scripts\build-windows-installer.ps1, or directly:
;
;   ISCC scripts\windows-installer.iss /DAppVersion=2.0.6 /DStageDir=release-stage
;
; Defines:
;   AppVersion    - semantic version shown in Add/Remove Programs (default 0.0.0)
;   StageDir      - staging dir produced by build-windows-installer.ps1
;                   containing LocalRouter.exe, apirouter.exe, run.bat,
;                   frontend\dist
;   SetupIconFile - optional path to a .ico used for setup/shortcut icons
;
; Install layout (per-user, no admin required):
;   %LOCALAPPDATA%\Programs\LocalRouter\LocalRouter.exe   desktop app (window)
;   %LOCALAPPDATA%\Programs\LocalRouter\apirouter.exe     server mode (console)
;   %LOCALAPPDATA%\Programs\LocalRouter\frontend\dist\...
; The frozen exes keep all runtime state (sqlite db, backups, logs, .env)
; next to themselves, so the install dir must stay user-writable - that is
; why PrivilegesRequired=lowest + {autopf} (= %LOCALAPPDATA%\Programs here).
; Uninstall only removes files the installer created; runtime data in
; {app}\data survives and is never touched.
; ============================================================================

#define MyAppName "LocalRouter"
#define MyAppExeName "LocalRouter.exe"
#define MyAppPort "12002"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef StageDir
  #define StageDir "release-stage"
#endif

#ifdef SetupIconFile
  #define ShortcutIcon 'IconFilename: "{app}\app.ico"; '
#else
  #define ShortcutIcon ''
#endif

[Setup]
AppId={{8F7A2C64-1B3D-4E5A-9C6F-2D8B0A4E7C31}}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=LocalRouter-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
#ifdef SetupIconFile
SetupIconFile={#SetupIconFile}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#StageDir}\LocalRouter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\apirouter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\run.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\frontend\dist\*"; DestDir: "{app}\frontend\dist"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\.env.example"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
#ifdef SetupIconFile
Source: "{#SetupIconFile}"; DestDir: "{app}"; DestName: "app.ico"; Flags: ignoreversion
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; {#ShortcutIcon}Comment: "LocalRouter - LLM API router desktop app (http://127.0.0.1:{#MyAppPort})"
Name: "{group}\{#MyAppName} (Server)"; Filename: "{app}\run.bat"; WorkingDir: "{app}"; {#ShortcutIcon}Comment: "Run LocalRouter as a console server without the desktop window"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; {#ShortcutIcon}Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
FinishedLabelNoIcons=Setup has finished installing {#MyAppName}.%n%nStart it from the Start Menu (or the desktop shortcut if selected) - it opens a desktop window with the Web UI inside.%n%nWeb UI and API:  http://127.0.0.1:{#MyAppPort}%n%n"LocalRouter (Server)" in the Start Menu runs it as a console server instead. To change HOST or BACKEND_PORT, copy {app}\.env.example to {app}\.env and edit it.%n%nUninstalling keeps your data ({app}\data), so reinstalling or upgrading will not lose your configuration.
FinishedLabel=Setup has finished installing {#MyAppName}.%n%nThe desktop window opens with the Web UI inside.%n%nWeb UI and API:  http://127.0.0.1:{#MyAppPort}%n%n"LocalRouter (Server)" in the Start Menu runs it as a console server instead. To change HOST or BACKEND_PORT, copy {app}\.env.example to {app}\.env and edit it.%n%nUninstalling keeps your data ({app}\data), so reinstalling or upgrading will not lose your configuration.
