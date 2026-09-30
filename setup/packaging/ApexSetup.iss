; Installateur Windows du Panel APEX (APEX.exe) — construit par setup/packaging/build_desktop_release.sh.
;
; Ce que fait cet installateur, et seulement cela :
;   - installe APEX.exe (Panel, .NET autonome multi-fichiers) POUR L'UTILISATEUR COURANT, sans droits
;     administrateur, dans %LOCALAPPDATA%\Programs\APEX ;
;   - crée le raccourci Bureau « APEX » et l'entrée du menu Démarrer (icône APEX réelle) ;
;   - vérifie l'installation (APEX.exe --version) et propose de lancer le Panel ;
;   - s'enregistre dans « Applications installées » avec un désinstalleur.
; Ce qu'il ne fait JAMAIS : installer ou démarrer WSL, Docker, le backend, Ollama ; lire ou écrire
; dans une distribution WSL ; demander un chemin, un port ou un jeton.
;
; Mise à jour : relancer un installateur plus récent (même AppId) remplace le programme dans le même
; dossier et conserve la configuration et les journaux (hors du dossier d'installation). Un Panel en
; cours d'exécution est détecté (AppMutex) et doit être fermé avant remplacement. Aucun
; [InstallDelete] : il effacerait aussi le journal de désinstallation (unins000.dat) que la mise à
; jour réutilise ; un fichier d'une version antérieure resté dans le dossier n'est pas chargé
; (.NET charge ce que liste APEX.deps.json).
;
; Désinstallation — politique (voir docs/DESKTOP_DISTRIBUTION.md) :
;   SUPPRIMÉ  : le dossier d'installation (fichiers installés), le raccourci Bureau, le menu Démarrer,
;               l'entrée « Applications installées » ;
;   CONSERVÉ  : %APPDATA%\APEX (paramètres), %LOCALAPPDATA%\APEX\logs (journaux), et TOUT ce qui vit
;               dans WSL (dépôt APEX, .apex/, jeton, volumes Docker, cerveau) — jamais touché.
;
; Compilation : ISCC.exe (Inno Setup 6.4.3). Sous Linux, via Wine (voir build_desktop_release.sh).
;   ISCC /DApexVersion=0.2.2 /DPublishDir=<dossier publié> /DIconFile=<apex.ico> /O<sortie> ApexSetup.iss

#ifndef ApexVersion
  #error ApexVersion doit être fourni (/DApexVersion=x.y.z) — jamais une version implicite.
#endif
#ifndef PublishDir
  #error PublishDir doit être fourni (/DPublishDir=...) — dossier produit par publish_dashboard.sh.
#endif
#ifndef IconFile
  #define IconFile "..\src\ApexSetup.Dashboard\Assets\apex.ico"
#endif

#define ApexPublisher "APEX"
#define ApexExeName "APEX.exe"
; Même nom que SingleInstanceGuard.MutexName côté Panel.
#define ApexMutex "APEX.Panel.SingleInstance"

[Setup]
AppId={{6C6B2B0E-3E0C-4E2E-9D6A-3B1E7B7B6B10}
AppName=APEX
AppVersion={#ApexVersion}
AppVerName=APEX {#ApexVersion}
AppPublisher={#ApexPublisher}
VersionInfoVersion={#ApexVersion}.0
VersionInfoProductName=APEX
VersionInfoDescription=Installateur du Panel APEX
; Par utilisateur, sans élévation, dans %LOCALAPPDATA%\Programs\APEX — écrit explicitement plutôt que
; {autopf} : même dossier sous Windows (FOLDERID_UserProgramFiles), mais {autopf} n'est pas résolu par
; Wine (SHGetKnownFolderPath 0x80070057, « Failed to expand shell folder constant autopf ») et
; l'installateur ne pourrait pas y être testé.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\APEX
DisableDirPage=yes
DefaultGroupName=APEX
DisableProgramGroupPage=yes
DisableWelcomePage=no
AppMutex={#ApexMutex}
CloseApplications=yes
RestartApplications=no
; WSL 2 exige Windows 10 1903 (build 18362) ou plus récent.
MinVersion=10.0.18362
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#ApexExeName}
UninstallDisplayName=APEX
OutputBaseFilename=APEX-Setup-x64-v{#ApexVersion}
Compression=lzma2/max
SolidCompression=yes
; Journal d'installation : %TEMP%\Setup Log AAAA-MM-JJ #NNN.txt (diagnostic d'une installation).
SetupLogging=yes
; Aucun certificat de signature disponible : binaire non signé (voir docs/PACKAGING.md).

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Le dossier publié entier (Panel .NET autonome, multi-fichiers).
Source: "{#PublishDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\APEX"; Filename: "{app}\{#ApexExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#ApexExeName}"; Comment: "Panel APEX"
Name: "{autoprograms}\APEX"; Filename: "{app}\{#ApexExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#ApexExeName}"; Comment: "Panel APEX"

[Run]
Filename: "{app}\{#ApexExeName}"; Description: "{cm:LaunchProgram,APEX}"; Flags: nowait postinstall skipifsilent

[Code]
{ Validation de l'installation : le programme installé doit réellement démarrer et répondre. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if Exec(ExpandConstant('{app}\{#ApexExeName}'), '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       and (ResultCode = 0) then
      Log('APEX: validation OK (APEX.exe --version)')
    else
    begin
      Log(Format('APEX: VALIDATION EN ÉCHEC (APEX.exe --version, code %d)', [ResultCode]));
      SuppressibleMsgBox('APEX est installé mais ne démarre pas correctement (vérification « APEX.exe --version » en échec). ' +
        'Consultez le journal d''installation dans %TEMP%.', mbError, MB_OK, IDOK);
    end;
  end;
end;
