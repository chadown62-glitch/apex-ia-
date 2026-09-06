@echo off
setlocal EnableExtensions
rem EnableDelayedExpansion est DELIBEREMENT desactive : ce mode fait
rem disparaitre silencieusement tout caractere `!` present dans la ligne de
rem commande, y compris a l'interieur des arguments transmis par
rem l'utilisateur (%*) - inacceptable pour une transmission fidele des
rem arguments (contrainte 5). Rien ci-dessous n'a besoin de `!variable!`.
rem =============================================================================
rem APEX - point d'entree Windows.
rem
rem DELEGATEUR PUR : ce fichier ne contient AUCUNE logique metier APEX (pas de
rem Docker, pas de Doctor, pas de Gate, pas de gestion .env/PID/signal). Toute
rem cette logique reste exclusivement dans scripts/apex-launch.sh, execute a
rem l'interieur de WSL. Ce script se contente de :
rem   1. resoudre son propre emplacement (jamais un chemin utilisateur en dur) ;
rem   2. verifier que wsl.exe est disponible ;
rem   3. convertir ce chemin en chemin WSL via `wslpath` (utilitaire WSL natif,
rem      jamais reimplemente ici) ;
rem   4. transmettre TOUS les arguments recus (%*) a scripts/apex-launch.sh a
rem      l'interieur de WSL, sans les faire transiter par un `bash -c` ou tout
rem      autre re-assemblage de chaine : `wsl.exe -- <script> %*` transmet
rem      chaque argument comme un token distinct de bout en bout (parsing
rem      Windows standard puis exec direct cote Linux) - aucune commande shell
rem      n'est jamais construite par concatenation, donc aucune injection via
rem      un argument n'est possible ;
rem   5. relayer fidelement le code de sortie du script Linux.
rem
rem Portable par construction : la racine du projet est resolue depuis
rem l'emplacement de CE fichier (%~dp0), jamais depuis %USERPROFILE%, un
rem chemin code en dur, ou le repertoire courant. Fonctionne quel que soit
rem l'endroit ou le depot est clone/monte (disque Windows ou \\wsl$\...).
rem
rem Distribution WSL ciblee : celle par defaut du systeme. Optionnellement
rem surchargeable via la variable d'environnement APEX_WSL_DISTRO (jamais une
rem valeur codee en dur dans ce fichier).
rem
rem Interdits, appliques strictement ici (les memes que scripts/apex-launch.sh,
rem jamais dupliques - juste respectes une seconde fois au niveau du point
rem d'entree) :
rem   - n'installe jamais WSL, Docker, Ollama, ni quoi que ce soit d'autre ;
rem   - ne modifie jamais Windows, WSL, le PATH systeme, ni .env ;
rem   - ne lance jamais Docker ou Ollama directement ;
rem   - ne contourne jamais Doctor/Gate (aucune option comme
rem     --allow-container-exec n'est ajoutee ici) ;
rem   - n'expose jamais APEX sur le reseau.
rem
rem Codes de sortie PROPRES a ce wrapper (jamais ceux de scripts/apex-launch.sh,
rem qui documente sa propre table 0-5/130 et dont le code est toujours relaye
rem tel quel des qu'il a effectivement demarre) :
rem   10 - wsl.exe introuvable (WSL2 non installe ou hors PATH)
rem   11 - scripts/apex-launch.sh introuvable a cote de ce fichier
rem   12 - la conversion de chemin Windows -> WSL a echoue
rem =============================================================================

set "APEX_WIN_ROOT=%~dp0"
if "%APEX_WIN_ROOT:~-1%"=="\" set "APEX_WIN_ROOT=%APEX_WIN_ROOT:~0,-1%"

where wsl.exe >nul 2>&1
if errorlevel 1 (
    echo [apex.bat] ERREUR : wsl.exe introuvable - WSL2 n'est pas installe ou n'est pas dans le PATH. 1>&2
    echo [apex.bat] remediation : installer WSL2 vous-meme ^(documentation Microsoft^) - ce script n'installe rien. 1>&2
    exit /b 10
)

if not exist "%APEX_WIN_ROOT%\scripts\apex-launch.sh" (
    echo [apex.bat] ERREUR : scripts\apex-launch.sh introuvable a cote de ce fichier ^(%APEX_WIN_ROOT%^). 1>&2
    echo [apex.bat] ce fichier doit rester a la racine du depot APEX, a cote du dossier scripts\. 1>&2
    exit /b 11
)

set "APEX_WSL_ROOT="
if defined APEX_WSL_DISTRO (
    for /f "usebackq delims=" %%P in (`wsl.exe -d "%APEX_WSL_DISTRO%" wslpath -a "%APEX_WIN_ROOT%" 2^>nul`) do set "APEX_WSL_ROOT=%%P"
) else (
    for /f "usebackq delims=" %%P in (`wsl.exe wslpath -a "%APEX_WIN_ROOT%" 2^>nul`) do set "APEX_WSL_ROOT=%%P"
)

if not defined APEX_WSL_ROOT (
    echo [apex.bat] ERREUR : impossible de convertir "%APEX_WIN_ROOT%" en chemin WSL. 1>&2
    echo [apex.bat] verifier que WSL2 et une distribution sont correctement installes. 1>&2
    exit /b 12
)

set "APEX_WSL_SCRIPT=%APEX_WSL_ROOT%/scripts/apex-launch.sh"

rem `bash "<script>" %*` plutot qu'une execution directe du script : memes
rem arguments un a un jusqu'a apex-launch.sh (bash ne fait que l'exec, il ne
rem redecoupe pas %* en une chaine shell), mais sans dependre de la
rem persistance du bit executable sur le fichier apres clonage/transfert.
if defined APEX_WSL_DISTRO (
    wsl.exe -d "%APEX_WSL_DISTRO%" -- bash "%APEX_WSL_SCRIPT%" %*
) else (
    wsl.exe -- bash "%APEX_WSL_SCRIPT%" %*
)
set "APEX_EXIT=%ERRORLEVEL%"

exit /b %APEX_EXIT%
