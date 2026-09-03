"""Tests du point d'entrée Windows (`apex.bat`).

Commit 5 : `apex.bat` est un DÉLÉGATEUR PUR vers `scripts/apex-launch.sh` à
l'intérieur de WSL — aucune logique métier APEX n'y est dupliquée (voir son
en-tête). Ce fichier ne peut pas être exécuté dans ce sandbox Linux (ni
`cmd.exe` ni WSL n'y existent) : les tests sont donc des assertions
STATIQUES sur sa structure — même discipline que les tests « motifs
interdits » de `tests/test_launcher.py` pour le script Linux (précédent
déjà établi dans ce projet pour ce type de contrainte). Ils vérifient la
présence des bonnes pratiques et l'ABSENCE des constructions dangereuses,
pas le résultat d'une exécution réelle — une vérification manuelle sur une
vraie machine Windows/WSL reste nécessaire avant un usage en production
(voir le rapport de ce commit).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

BAT = Path(__file__).resolve().parent.parent / "apex.bat"


def code_only(text: str) -> str:
    """Retire les lignes de commentaire (`rem ...`) — le fichier DOCUMENTE
    volontairement les constructions interdites dans son en-tête (ex.
    "sans les faire transiter par un `bash -c`"), seule une occurrence en
    code réel serait un défaut."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.strip().lower().startswith("rem")
    )


class TestFileExistsAndIsWellFormed(unittest.TestCase):
    def test_apex_bat_exists_at_repo_root(self) -> None:
        self.assertTrue(BAT.is_file(), "apex.bat doit exister à la racine du dépôt")

    def test_starts_with_echo_off(self) -> None:
        text = BAT.read_text()
        self.assertTrue(text.lstrip().startswith("@echo off"))

    def test_uses_crlf_or_lf_consistently_and_is_ascii_safe(self) -> None:
        """cmd.exe tolère LF, mais un fichier mixte est un piège classique —
        et des caractères non-ASCII dans un .bat sans BOM/codepage déclaré
        peuvent se corrompre selon la console. On reste en ASCII pur."""
        raw = BAT.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail(f"apex.bat doit rester en ASCII pur (cmd.exe/codepages) : {exc}")


class TestNoHardcodedUserPaths(unittest.TestCase):
    """Contrainte 1 : portable, aucun chemin utilisateur codé en dur."""

    def test_no_hardcoded_windows_user_path(self) -> None:
        text = BAT.read_text()
        forbidden_patterns = (
            r"C:\\Users\\", r"C:\\Users/", r"%USERPROFILE%\\.*apex",
            r"/home/[a-zA-Z0-9_-]+/", r"/mnt/c/Users",
        )
        for pattern in forbidden_patterns:
            self.assertIsNone(
                re.search(pattern, text, re.IGNORECASE),
                f"chemin utilisateur potentiellement codé en dur : motif {pattern!r}",
            )

    def test_resolves_root_from_own_location_not_cwd_or_profile(self) -> None:
        text = BAT.read_text()
        self.assertIn("%~dp0", text, "la racine doit être résolue depuis %~dp0 (emplacement du script)")
        self.assertNotIn("%CD%", text, "ne doit jamais dépendre du répertoire courant")

    def test_no_hardcoded_distro_name(self) -> None:
        """La distribution WSL ne doit jamais être un nom codé en dur — soit
        celle par défaut du système, soit APEX_WSL_DISTRO (variable)."""
        text = BAT.read_text()
        # Aucun -d suivi d'un littéral autre que la variable d'environnement.
        for match in re.finditer(r"-d\s+\"([^\"]*)\"", text):
            value = match.group(1)
            msg = f"nom de distribution codé en dur détecté : {value!r}"
            self.assertIn("%APEX_WSL_DISTRO%", value, msg)


class TestNoUnsafeShellConstruction(unittest.TestCase):
    """Contrainte 7 : jamais de concaténation naïve, jamais d'eval, jamais
    d'injection possible via un argument utilisateur."""

    def test_never_wraps_forwarded_args_in_bash_dash_c(self) -> None:
        """`bash -c "... %*"` reconstruirait une commande shell à partir du
        texte des arguments — c'est exactement la construction dangereuse à
        proscrire. `wsl.exe -- bash <script> %*` transmet chaque argument
        comme un token direct, sans jamais le faire passer par un parseur
        shell supplémentaire — c'est la forme attendue ici."""
        text = code_only(BAT.read_text())
        self.assertNotIn("-c \"", text)
        self.assertNotIn("bash -c", text)

    def test_never_uses_eval(self) -> None:
        text = code_only(BAT.read_text())
        self.assertNotRegex(text, r"\beval\b")

    def test_delegates_via_wsl_exe_double_dash_separator(self) -> None:
        """`wsl.exe -- <cible> %*` (le `--` sépare les options de wsl.exe de
        la commande à exécuter) est la forme sûre : la cible et les
        arguments restent des tokens Windows distincts jusqu'à l'exec côté
        Linux, jamais réassemblés en une chaîne interprétée deux fois."""
        text = BAT.read_text()
        self.assertRegex(text, r"wsl\.exe\s+(-d\s+\"[^\"]*\"\s+)?--\s+bash\s+\"[^\"]*\"\s+%\*")

    def test_forwards_all_arguments_via_percent_star(self) -> None:
        text = BAT.read_text()
        self.assertIn("%*", text)
        # Jamais une reconstruction manuelle argument par argument (%1 %2 %3...)
        # qui perdrait des arguments au-delà de %9 ou modifierait leur forme.
        self.assertNotRegex(text, r"%1\s+%2\s+%3")

    def test_delayed_expansion_is_not_enabled(self) -> None:
        """EnableDelayedExpansion ferait disparaître silencieusement tout
        caractère `!` dans les arguments transmis — inacceptable pour une
        transmission fidèle (contrainte 5)."""
        text = code_only(BAT.read_text())
        self.assertNotIn("EnableDelayedExpansion", text)


class TestNoUnauthorizedInstallOrSystemModification(unittest.TestCase):
    """Contrainte 4 : aucune installation, aucune modification système."""

    def test_no_install_or_download_commands(self) -> None:
        text = BAT.read_text()
        forbidden = (
            "wsl --install", "wsl.exe --install",
            "winget install", "choco install", "chocolatey",
            "Invoke-WebRequest", "curl ", "iwr ",
            "ollama pull", "ollama serve",
            "docker run", "docker compose", "docker-compose",
            "reg add", "reg.exe", "setx ",  # modification du registre / PATH système
            "New-Item -ItemType SymbolicLink",
        )
        for pattern in forbidden:
            self.assertNotIn(pattern, text, f"motif interdit trouvé dans apex.bat : {pattern!r}")

    def test_never_modifies_env_file(self) -> None:
        text = BAT.read_text()
        self.assertNotRegex(text, r"[>]\s*\.env\b")

    def test_never_adds_gate_bypass_flags(self) -> None:
        text = code_only(BAT.read_text())
        self.assertNotIn("--allow-container-exec", text)
        self.assertNotIn("--allow-network-probe", text)


class TestWslDetectionAndErrorHandling(unittest.TestCase):
    """Contrainte 7 : vérifie les erreurs de wsl.exe, comportement explicite
    si WSL est absent."""

    def test_checks_wsl_exe_availability_before_use(self) -> None:
        text = BAT.read_text()
        self.assertIn("where wsl.exe", text)
        # Le check doit être suivi d'une branche d'erreur explicite avant
        # toute autre INVOCATION de wsl.exe plus bas dans le fichier — la
        # recherche du "prochain wsl.exe" ignore le message d'erreur
        # lui-même (qui contient littéralement le mot "wsl.exe").
        where_pos = text.index("where wsl.exe")
        rest = text[where_pos + len("where wsl.exe"):]
        next_real_invocation = re.search(r"wsl\.exe\s+(-d|wslpath|--)", rest)
        self.assertIsNotNone(next_real_invocation, "aucune invocation ultérieure de wsl.exe trouvée")
        branch_end = where_pos + len("where wsl.exe") + next_real_invocation.start()
        branch = text[where_pos:branch_end]
        self.assertIn("errorlevel 1", branch)
        self.assertIn("exit /b", branch)

    def test_checks_linux_launcher_script_exists_before_delegating(self) -> None:
        text = BAT.read_text()
        self.assertIn("scripts\\apex-launch.sh", text)
        self.assertIn("if not exist", text)

    def test_checks_wslpath_conversion_succeeded(self) -> None:
        text = BAT.read_text()
        self.assertIn("wslpath", text)
        self.assertIn("if not defined APEX_WSL_ROOT", text)

    def test_distinct_exit_codes_for_each_windows_side_failure(self) -> None:
        """Les échecs propres au wrapper (WSL absent, script absent,
        conversion de chemin échouée) doivent avoir des codes de sortie
        EXPLICITES et DISTINCTS les uns des autres."""
        text = BAT.read_text()
        codes = re.findall(r"exit /b (\d+)", text)
        # Les trois codes documentés (10, 11, 12) doivent apparaître, chacun
        # une fois comme échec dédié (le relais final `%APEX_EXIT%` n'est
        # pas un littéral et n'est donc pas capturé par ce regex).
        for code in ("10", "11", "12"):
            self.assertIn(code, codes, f"code de sortie {code} attendu et documenté en en-tête")
        msg = "les codes de sortie propres au wrapper doivent être uniques"
        self.assertEqual(len(codes), len(set(codes)), msg)


class TestExitCodePropagation(unittest.TestCase):
    """Contrainte 6 : le code de sortie du processus WSL/Linux est relayé
    fidèlement, jamais réinterprété."""

    def test_captures_errorlevel_immediately_after_wsl_invocation(self) -> None:
        """Aucune commande ne doit s'intercaler entre l'invocation de
        wsl.exe (dans l'une ou l'autre branche du if/else selon
        APEX_WSL_DISTRO) et la capture d'ERRORLEVEL — seule la fermeture
        syntaxique `)` du bloc if/else est tolérée entre les deux, jamais
        une commande qui écraserait ERRORLEVEL avant sa capture."""
        text = BAT.read_text()
        capture_pos = text.index('set "APEX_EXIT=%ERRORLEVEL%"')
        before_lines = text[:capture_pos].splitlines()
        last_non_blank = next(line.strip() for line in reversed(before_lines) if line.strip())
        msg = f"une commande s'intercale avant la capture : {last_non_blank!r}"
        self.assertEqual(last_non_blank, ")", msg)

        last_wsl_call = text.rindex("wsl.exe", 0, capture_pos)
        between = text[last_wsl_call:capture_pos]
        lines_between = [line.strip() for line in between.splitlines()[1:] if line.strip()]
        msg2 = f"contenu inattendu entre l'appel et la capture : {lines_between}"
        self.assertEqual(lines_between, [")"], msg2)

    def test_exits_with_captured_code_as_final_action(self) -> None:
        text = BAT.read_text()
        stripped = text.rstrip().splitlines()
        last_meaningful = next(line.strip() for line in reversed(stripped) if line.strip())
        self.assertEqual(last_meaningful, "exit /b %APEX_EXIT%")


class TestNoDuplicatedBusinessLogic(unittest.TestCase):
    """Contrainte 3 : aucune logique Docker/Doctor/Gate/PID/.env dupliquée —
    apex.bat doit rester un pur délégateur."""

    def test_never_parses_or_writes_env_content(self) -> None:
        text = BAT.read_text()
        self.assertNotIn("POSTGRES_", text)
        self.assertNotIn("APEX_GPU_", text)

    def test_never_mentions_doctor_or_gate_logic_directly(self) -> None:
        """apex.bat ne doit connaître ni les noms des vérifications du
        Doctor/Gate, ni leurs seuils — seulement déléguer."""
        text = BAT.read_text()
        for token in ("vram", "cuda", "gpu_model", "healthcheck", "GATE-", "doctor.gpu"):
            self.assertNotIn(token, text.lower() if token.islower() else text)

    def test_never_references_pid_tracking_state_file(self) -> None:
        """Le suivi d'ownership de l'infrastructure (commit 4,
        launcher-compose-state.json) est une responsabilité exclusive du
        script Linux — jamais dupliquée ni lue depuis Windows."""
        text = BAT.read_text()
        self.assertNotIn("launcher-compose-state", text)
        self.assertNotIn(".apex\\", text)


class TestDocumentationMentionsUsage(unittest.TestCase):
    def test_readme_documents_windows_entry_point(self) -> None:
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        self.assertIn("apex.bat", readme)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
