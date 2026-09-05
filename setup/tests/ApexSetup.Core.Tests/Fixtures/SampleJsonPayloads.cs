namespace ApexSetup.Core.Tests.Fixtures;

/// <summary>
/// Charges JSON représentatives des contrats RÉELS observés en exécutant
/// <c>apex version|doctor|gate --json</c> contre le Runtime pendant cette
/// session (voir docs/JSON_CONTRACTS.md côté DEV) — pas des exemples
/// inventés, reproduisant la forme exacte des champs.
/// </summary>
internal static class SampleJsonPayloads
{
    public const string ValidVersion = """
        {
          "version": "0.1.2",
          "phase": 1,
          "phase_name": "Dépôt, configuration, conteneurs, doctor",
          "python": "3.12.3",
          "platform": "Linux 6.18.44-fc-v24",
          "executable": "/usr/bin/python3"
        }
        """;

    public const string ValidDoctorWithSchema = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "status": "fail",
          "counts": {"pass": 7, "warn": 4, "fail": 6, "skip": 4},
          "duration_ms": 849.7,
          "results": [
            {
              "name": "python.version",
              "category": "environnement",
              "severity": "pass",
              "message": "Python 3.12.3",
              "remediation": "",
              "facts": {"version": "3.12.3"},
              "duration_ms": 0.4
            },
            {
              "name": "docker.present",
              "category": "conteneurs",
              "severity": "fail",
              "message": "démon Docker injoignable",
              "remediation": "sudo service docker start",
              "facts": {},
              "duration_ms": 492.8
            }
          ]
        }
        """;

    public const string ValidDoctorLegacyNoSchema = """
        {
          "status": "pass",
          "counts": {"pass": 10, "warn": 0, "fail": 0, "skip": 2},
          "duration_ms": 120.0,
          "results": []
        }
        """;

    public const string ValidGateFail = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "probe": "system",
          "machine_label": null,
          "measured_on_target_machine": true,
          "gate_status": "FAIL",
          "phase_2_authorized": false,
          "fingerprint": "abc123",
          "counts": {"PASS": 5, "FAIL": 2, "DEGRADED": 0, "UNKNOWN": 1, "SIMULATED": 0},
          "capabilities": {"sandbox": "namespaced"},
          "hardware": {
            "distro_name": "Ubuntu 24.04",
            "kernel": "6.18.44-fc-v24",
            "cpu_model": "unknown",
            "cpu_threads": 8,
            "ram_total_gb": 32.0,
            "disk_free_gb": 29.0,
            "gpus": [],
            "driver_version": null,
            "cuda_version": null,
            "windows_version": null
          },
          "blocking": ["gate_09_vram"],
          "unresolved": [],
          "missing_dependencies": [
            {
              "name": "docker",
              "why": "isolation sandbox",
              "install": "apt install docker.io",
              "required_from_phase": 3,
              "fallback": "aucun",
              "check_id": "gate_17_docker"
            }
          ],
          "checks": [
            {
              "check_id": "gate_09_vram",
              "title": "VRAM disponible",
              "status": "FAIL",
              "message": "VRAM insuffisante",
              "facts": {"vram_gb": 8.0},
              "remediation": "Utiliser un GPU avec plus de VRAM.",
              "blocks_phases": [2, 3],
              "to_validate_on_target": false,
              "unknown_reason": ""
            }
          ]
        }
        """;

    public const string ModelManifestValid = """
        [
          {
            "model_id": "devstral-2-22b",
            "version": "22b",
            "quantization": "q4_k_m",
            "served_model": "devstral-2:22b-q4_K_M",
            "size_bytes": 15000000000,
            "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "required_vram_gb": 14.0,
            "required_ram_gb": 16,
            "max_context": 32768,
            "capability_class": "coder",
            "supported_profiles": ["STANDARD", "PERFORMANCE", "HIGH_END"],
            "fallback_model_id": "gemma4-12b",
            "source": "ollama-registry"
          }
        ]
        """;
}
