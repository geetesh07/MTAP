; MTAP_Setup.iss — Inno Setup 6 installer script
;
; HOW TO BUILD:
;   Run  build_installer.bat  — or open this file in Inno Setup and press F9.
;   Output: installer\MTAP_Setup_0.1.0.exe  (~150 MB, self-contained)

#define AppName      "MTAP"
#define AppVersion   "0.1.0"
#define AppPublisher "NTS"
#define AppFullName  "Machine Tool Automation Program"
#define ExeName      "MTAP.exe"

[Setup]
AppId={{A7B3C4D5-E6F7-4A8B-9C0D-1E2F3A4B5C6D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppFullName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=MTAP_Setup_{#AppVersion}
SetupIconFile=assets\icons\mtap.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppFullName}
Compression=lzma2/ultra64
SolidCompression=yes
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardResizable=yes
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\{#ExeName}";      DestDir: "{app}";           Flags: ignoreversion
Source: "dist\_internal\*";     DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppFullName}";       Filename: "{app}\{#ExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "&Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\output"
Type: filesandordirs; Name: "{app}\logs"

[Code]
function InitializeSetup(): Boolean;
begin
  if not IsWin64 then begin
    MsgBox('MTAP requires a 64-bit version of Windows 10 or later.', mbError, MB_OK);
    Result := False;
  end else
    Result := True;
end;
