; NewAl installer (Inno Setup 6). Installs for the current user: no admin rights needed.
#define AppVersion GetEnv("NEWAL_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E1B2B2A-4C1D-4E5E-9C1F-4E657741C001}
AppName=NewAl
AppVersion={#AppVersion}
AppPublisher=Musab
DefaultDirName={localappdata}\Programs\NewAl
DefaultGroupName=NewAl
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=NewAl-Setup
SetupIconFile=assets\newal.ico
UninstallDisplayIcon={app}\NewAl.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\NewAl\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NewAl"; Filename: "{app}\NewAl.exe"
Name: "{group}\NewAl Code"; Filename: "{app}\code\NewAlCode.exe"; Check: FileExists(ExpandConstant('{app}\code\NewAlCode.exe'))
Name: "{autodesktop}\NewAl Code"; Filename: "{app}\code\NewAlCode.exe"; Tasks: desktopicon; Check: FileExists(ExpandConstant('{app}\code\NewAlCode.exe'))
Name: "{autodesktop}\NewAl"; Filename: "{app}\NewAl.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\NewAl.exe"; Description: "{cm:LaunchProgram,NewAl}"; Flags: nowait postinstall skipifsilent
; An update from inside NewAl installs silently: start the new version when it is done.
Filename: "{app}\NewAl.exe"; Flags: nowait; Check: WizardSilent

; Models, conversations and settings live in %USERPROFILE%\NewAl and are kept on uninstall.
