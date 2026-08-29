"""Regression d'encodage — incident machine #1.

Contexte reel : premiere execution du Gate sur une machine physique (WSL2,
Ubuntu 26.04, Python 3.14.4). GATE-01 a echoue sur

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x82 in position 59

en appelant `cmd.exe /c ver`. Cause : `cmd.exe` ecrit dans la page de code OEM
de la console Windows (0x82 = « e accent aigu » en CP850), pas en UTF-8, et
`subprocess.run(text=True)` decode en UTF-8 strict.

Le defaut n'etait pas dans GATE-01 mais dans la sonde : tout binaire Windows
appele via l'interop WSL aurait produit le meme crash. Ces tests couvrent le
correctif au niveau ou il a ete fait.
"""

from __future__ import annotations

import unittest

from apex.util.probe import OEM_CODEPAGES, SystemProbe, decode_process_output

#: Sortie francaise typique de `cmd.exe /c ver`, contenant l'octet fautif.
WINDOWS_VER_FR = (
    "\r\nMicrosoft Windows [version 10.0.26100.4061]\r\n"
    "(c) Microsoft Corporation. Tous droits réservés.\r\n"
)


class TestDecodeProcessOutput(unittest.TestCase):
    def test_the_exact_failing_byte_is_handled(self) -> None:
        """0x82 isole : invalide en UTF-8, valide en CP850."""
        raw = b"version 10.0.26100 r\x82serv\x82"
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")
        text, encoding = decode_process_output(raw)
        self.assertIn(encoding, OEM_CODEPAGES)
        self.assertIn("10.0.26100", text)

    def test_french_cmd_output_is_decoded(self) -> None:
        text, encoding = decode_process_output(WINDOWS_VER_FR.encode("cp850"))
        self.assertEqual(encoding, "cp850")
        self.assertIn("10.0.26100.4061", text)
        self.assertIn("réservés", text)

    def test_utf8_remains_the_normal_path(self) -> None:
        text, encoding = decode_process_output("Docker version 27.1.1".encode("utf-8"))
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(text, "Docker version 27.1.1")

    def test_utf8_wins_over_oem_when_both_are_valid(self) -> None:
        """L'ordre compte : une sortie UTF-8 accentuee ne doit pas etre lue en CP850."""
        text, encoding = decode_process_output("modèle : é".encode("utf-8"))
        self.assertEqual(encoding, "utf-8")
        self.assertEqual(text, "modèle : é")

    def test_utf16_from_cmd_slash_u(self) -> None:
        for encoding_used in ("utf-16", "utf-16-le"):
            text, detected = decode_process_output("Windows [version 10.0.1]".encode(encoding_used))
            self.assertTrue(detected.startswith("utf-16"), detected)
            self.assertIn("10.0.1", text)

    def test_arbitrary_bytes_never_raise(self) -> None:
        """Le dernier recours garantit qu'aucune sonde ne plante sur un encodage."""
        text, encoding = decode_process_output(bytes(range(256)))
        self.assertIsInstance(text, str)
        self.assertTrue(encoding)

    def test_empty_output(self) -> None:
        self.assertEqual(decode_process_output(b""), ("", "utf-8"))

    def test_encoding_is_reported_not_hidden(self) -> None:
        """Un decodage approximatif doit rester visible dans le rapport."""
        _, encoding = decode_process_output(b"r\x82serv\x82")
        self.assertNotEqual(encoding, "utf-8")


class TestSystemProbeDecoding(unittest.TestCase):
    """La sonde reelle ne doit plus jamais lever sur une sortie non UTF-8."""

    def test_non_utf8_stdout_does_not_raise(self) -> None:
        probe = SystemProbe()
        script = "import sys; sys.stdout.buffer.write(b'r\\x82serv\\x82')"
        result = probe.run(["python3", "-c", script], timeout_s=10)
        self.assertTrue(result.ok)
        self.assertIn(result.encoding, OEM_CODEPAGES)
        self.assertTrue(result.stdout)

    def test_non_utf8_stderr_does_not_raise(self) -> None:
        probe = SystemProbe()
        script = "import sys; sys.stderr.buffer.write(b'\\x82chec'); sys.exit(1)"
        result = probe.run(["python3", "-c", script], timeout_s=10)
        self.assertFalse(result.ok)
        self.assertTrue(result.stderr)

    def test_utf8_output_still_reported_as_utf8(self) -> None:
        result = SystemProbe().run(["python3", "-c", "print('ok')"], timeout_s=10)
        self.assertEqual(result.encoding, "utf-8")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_missing_binary_still_returns_cleanly(self) -> None:
        result = SystemProbe().run(["binaire-inexistant-apex"], timeout_s=5)
        self.assertEqual(result.returncode, 127)
        self.assertFalse(result.ok)


class TestGate01WithWindowsEncoding(unittest.TestCase):
    """GATE-01 de bout en bout sur la sortie qui avait fait echouer la machine #1."""

    def _context(self, ver_output: str):
        from apex.config.loader import parse_config
        from apex.gate.checks import GateContext
        from apex.util.probe import CommandResult, FakeProbe
        from tests.test_config import config as base_config

        probe = FakeProbe(
            programs={"cmd.exe": "/mnt/c/Windows/System32/cmd.exe"},
            commands={
                ("cmd.exe", "/c", "ver"): CommandResult(
                    ("cmd.exe",), 0, ver_output, "", encoding="cp850"
                )
            },
            # Interop ACTIVE : c'etait le cas sur la machine #1.
            files={"/etc/wsl.conf": "[boot]\nsystemd=true\n"},
            environ={"WSL_DISTRO_NAME": "Ubuntu"},
        )
        return GateContext(probe=probe, config=parse_config(base_config()), timeout_s=1.0)

    def test_accented_output_now_passes(self) -> None:
        from apex.gate.checks import gate_01_windows_host
        from apex.gate.model import GateStatus

        outcome = gate_01_windows_host(self._context(WINDOWS_VER_FR))
        self.assertIs(outcome.status, GateStatus.PASS)
        self.assertEqual(outcome.facts["windows_version"], "10.0.26100.4061")
        self.assertEqual(outcome.facts["output_encoding"], "cp850")

    def test_unparseable_output_degrades_without_inventing_a_version(self) -> None:
        from apex.gate.checks import gate_01_windows_host
        from apex.gate.model import GateStatus

        outcome = gate_01_windows_host(self._context("sortie illisible"))
        self.assertIs(outcome.status, GateStatus.DEGRADED)
        self.assertNotIn(".", str(outcome.facts["windows_version"]))
        self.assertIn("winver", outcome.remediation)


if __name__ == "__main__":
    unittest.main()
