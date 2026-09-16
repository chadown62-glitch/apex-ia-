; N+30 — Installateur Windows d'APEX.exe (le Dashboard, mission §18).
;
; NE PAS CONFONDRE avec ApexSetup.Gui.exe (l'assistant d'installation
; existant, qui provisionne l'environnement APEX lui-même — WSL/Docker/
; Ollama/modèles — voir setup/docs/GUI_ARCHITECTURE.md). Ce script produit
; un SECOND artefact, distinct et complémentaire : un installateur natif
; Windows classique qui copie APEX.exe (le tableau de bord quotidien) dans
; Program Files, avec raccourcis et désinstalleur — jamais une troisième
; réimplémentation de la logique d'installation d'APEX elle-même.
;
; COMPILATION : nécessite Inno Setup (ISCC.exe), disponible UNIQUEMENT sous
; Windows (ou via Wine) — non disponible dans le bac à sable Linux où ce
; dépôt est développé. Commande exacte, à exécuter sur une machine Windows
; avec Inno Setup 6 installé, après avoir publié APEX.exe :
;
;   setup\packaging\publish_dashboard.sh   (ou son équivalent .bat/CI)
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup\packaging\ApexSetup.iss
;
; Produit : setup\dist\installer\APEX-Setup.exe
;
; AUCUNE opération destructive : cet installateur ne supprime jamais les
; données APEX (.apex/, volumes Docker) — seul le contenu qu'IL a lui-même
; installé (APEX.exe et ses fichiers) est retiré à la désinstallation,
; jamais les données utilisateur qui vivent dans WSL2.

#define ApexVersion "0.1.0"
#define ApexPublisher "APEX"
#define ApexExeName "APEX.exe"
; Chemin de publication attendu — produit par publish_dashboard.sh.
#define PublishDir "..\dist\apex-dashboard-" + ApexVersion + "-win-x64"

[Setup]
AppId={{6C6B2B0E-3E0C-4E2E-9D6A-3B1E7B7B6B10}
AppName=APEX
AppVersion={#ApexVersion}
AppPublisher={#ApexPublisher}
DefaultDirName={autopf}\APEX
DefaultGroupName=APEX
DisableProgramGroupPage=yes
; Élévation seulement pour l'INSTALLATION (Program Files) — jamais pour
; APEX.exe lui-même à chaque lancement (voir app.manifest, asInvoker).
PrivilegesRequired=admin
OutputDir=..\dist\installer
OutputBaseFilename=APEX-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#ApexExeName}
; Aucun certificat de signature disponible dans ce dépôt — même limite
; assumée que packaging/publish.sh pour Cli/Gui/Launcher, voir docs/PACKAGING.md.

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Décoché par défaut — mission §18 : raccourci bureau OPTIONNEL, jamais imposé.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Décoché par défaut — mission §17 : démarrage automatique JAMAIS activé sans consentement explicite.
Name: "startupicon"; Description: "Lancer APEX au démarrage de Windows"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PublishDir}\{#ApexExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\APEX"; Filename: "{app}\{#ApexExeName}"
Name: "{group}\{cm:UninstallProgram,APEX}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\APEX"; Filename: "{app}\{#ApexExeName}"; Tasks: desktopicon
Name: "{userstartup}\APEX"; Filename: "{app}\{#ApexExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#ApexExeName}"; Description: "{cm:LaunchProgram,APEX}"; Flags: nowait postinstall skipifsilent

; Désinstallation propre : Inno Setup supprime par défaut UNIQUEMENT les
; fichiers listés dans [Files] (ici : APEX.exe seul) — jamais les données
; utilisateur (paramètres du Dashboard dans %AppData%\Apex\, .apex/ côté
; WSL, volumes Docker). Aucune section [UninstallDelete] destructive
; n'est ajoutée : une désinstallation propre retire le programme, jamais
; les données APEX.
