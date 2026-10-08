; Installateur Windows d'APEX (APEX.exe) — construit par setup/packaging/build_desktop_release.sh.
;
; Ce que fait cet installateur, et seulement cela :
;   - installe APEX.exe (Panel, .NET autonome multi-fichiers) POUR L'UTILISATEUR COURANT, sans droits
;     administrateur, dans %LOCALAPPDATA%\Programs\APEX ;
;   - installe à côté, dans app\, l'application APEX elle-même — l'artefact APEX Runtime (runtime_build :
;     Runtime seul, sans l'extension Recherche), son interface web et la description FIGÉE de son runtime
;     (Python officiel par empreinte SHA-256, bibliothèques par empreintes) : le Panel passe alors en mode
;     « Windows natif » (APEX autonome, sans WSL ni Docker) ;
;   - crée le raccourci Bureau « APEX » et l'entrée du menu Démarrer (icône APEX réelle) ;
;   - vérifie l'installation (APEX.exe --version, application présente) et propose de lancer APEX ;
;   - s'enregistre dans « Applications installées » avec un désinstalleur.
; Ce qu'il ne fait JAMAIS : télécharger quoi que ce soit, démarrer le backend, installer WSL, Docker ou
; Ollama, demander un chemin, un port ou un jeton. Python, les bibliothèques et le modèle sont téléchargés
; et vérifiés par le Panel (« Préparer APEX », « Installer le modèle »), sur un clic de l'utilisateur.
;
; Mise à jour : relancer un installateur plus récent (même AppId) remplace le programme dans le même
; dossier et conserve la configuration et les journaux (hors du dossier d'installation). Un Panel en
; cours d'exécution est détecté (AppMutex) et doit être fermé avant remplacement. Le dossier du
; Panel n'est jamais vidé (un [InstallDelete] global effacerait aussi le journal de désinstallation
; unins000.dat que la mise à jour réutilise ; un fichier .NET d'une version antérieure n'est pas chargé :
; .NET charge ce que liste APEX.deps.json). Seul app\ (l'application Python) est remplacé ENTIÈREMENT :
; un module Python resté d'une version antérieure, lui, serait importable. Un APEX en cours d'exécution
; est d'abord arrêté proprement (APEX.exe --stop de la version installée), jamais remplacé sous ses pieds.
;
; Désinstallation — politique (voir docs/DESKTOP_DISTRIBUTION.md) :
;   ARRÊTÉ    : APEX s'il tourne (APEX.exe --stop : arrêt propre par l'API) avant toute suppression ;
;   SUPPRIMÉ  : le dossier d'installation (fichiers installés), le runtime préparé par le Panel
;               (%LOCALAPPDATA%\APEX\python : Python et bibliothèques, re-téléchargeables), le raccourci Bureau,
;               le menu Démarrer, l'entrée « Applications installées » ;
;   CONSERVÉ  : les DONNÉES de l'utilisateur — %LOCALAPPDATA%\APEX\data (base, espaces de travail),
;               %LOCALAPPDATA%\APEX\models (modèle téléchargé), %APPDATA%\APEX (paramètres),
;               %LOCALAPPDATA%\APEX\logs (journaux) — et TOUT ce qui vit dans WSL (jamais touché).
;
; Compilation : ISCC.exe (Inno Setup 6.4.3). Sous Linux, via Wine (voir build_desktop_release.sh).
;   ISCC /DApexVersion=0.3.0 /DPublishDir=<dossier publié> /DAppDir=<application APEX> /DIconFile=<apex.ico> /O<sortie> ApexSetup.iss

#ifndef ApexVersion
  #error ApexVersion doit être fourni (/DApexVersion=x.y.z) — jamais une version implicite.
#endif
#ifndef PublishDir
  #error PublishDir doit être fourni (/DPublishDir=...) — dossier produit par publish_dashboard.sh.
#endif
#ifndef AppDir
  #error AppDir doit être fourni (/DAppDir=...) — application APEX (artefact Runtime + runtime\) assemblée par build_desktop_release.sh.
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
VersionInfoDescription=Installateur d'APEX
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
; L'application APEX (artefact Runtime vérifié + description figée du runtime) : mode Windows natif du Panel.
Source: "{#AppDir}\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Une mise à jour remplace l'application ENTIÈRE : un module retiré d'APEX ne doit jamais rester importable.
Type: filesandordirs; Name: "{app}\app"

[UninstallDelete]
; Runtime préparé par le Panel (Python et bibliothèques, re-téléchargeables) — jamais les données ni le modèle.
Type: filesandordirs; Name: "{localappdata}\APEX\python"
Type: filesandordirs; Name: "{localappdata}\APEX\downloads"

[Icons]
Name: "{autodesktop}\APEX"; Filename: "{app}\{#ApexExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#ApexExeName}"; Comment: "APEX"
Name: "{autoprograms}\APEX"; Filename: "{app}\{#ApexExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#ApexExeName}"; Comment: "APEX"

[Run]
Filename: "{app}\{#ApexExeName}"; Description: "{cm:LaunchProgram,APEX}"; Flags: nowait postinstall skipifsilent

[Code]
{ Validation de l'installation : le programme installé doit réellement démarrer et répondre, et l'application
  APEX (mode Windows natif) doit être complète. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not FileExists(ExpandConstant('{app}\app\apex\standalone\__init__.py'))
       or not FileExists(ExpandConstant('{app}\app\runtime\python-runtime.json'))
       or not FileExists(ExpandConstant('{app}\app\runtime\requirements-windows.lock')) then
    begin
      Log('APEX: VALIDATION EN ÉCHEC (application APEX incomplète dans {app}\app)');
      SuppressibleMsgBox('L''application APEX est incomplète après installation (dossier app). Relancez l''installateur.',
        mbError, MB_OK, IDOK);
    end;
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

{ Mise à jour : un APEX en cours d'exécution est arrêté proprement (par la version installée) avant remplacement. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\{#ApexExeName}')) then
  begin
    if Exec(ExpandConstant('{app}\{#ApexExeName}'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log(Format('APEX: arrêt avant mise à jour (APEX.exe --stop, code %d)', [ResultCode]))
    else
      Log('APEX: APEX.exe --stop non exécuté avant mise à jour');
  end;
end;

{ Désinstallation : APEX est d'abord arrêté proprement (ses fichiers ne sont plus utilisés), puis supprimé. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    if Exec(ExpandConstant('{app}\{#ApexExeName}'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log(Format('APEX: arrêt avant désinstallation (APEX.exe --stop, code %d)', [ResultCode]))
    else
      Log('APEX: APEX.exe --stop non exécuté avant désinstallation');
  end;
end;
