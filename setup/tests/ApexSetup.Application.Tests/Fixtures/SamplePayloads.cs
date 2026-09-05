namespace ApexSetup.Application.Tests.Fixtures;

/// <summary>Charges JSON minimales mais structurellement valides pour version/doctor/gate — voir docs/JSON_CONTRACTS.md côté DEV pour la forme exacte attendue.</summary>
internal static class SamplePayloads
{
    public const string Version = """
        {
          "version": "0.1.2",
          "phase": 1,
          "phase_name": "Dépôt, configuration, conteneurs, doctor",
          "python": "3.12.3",
          "platform": "Linux 6.18.44-fc-v24",
          "executable": "/usr/bin/python3"
        }
        """;

    public const string DoctorPass = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "status": "pass",
          "counts": {"pass": 10, "warn": 0, "fail": 0, "skip": 0},
          "duration_ms": 42.0,
          "results": []
        }
        """;

    public const string DoctorFail = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "status": "fail",
          "counts": {"pass": 5, "warn": 1, "fail": 3, "skip": 0},
          "duration_ms": 42.0,
          "results": []
        }
        """;

    public const string GatePassAuthorized = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "probe": "system",
          "machine_label": null,
          "measured_on_target_machine": true,
          "gate_status": "PASS",
          "phase_2_authorized": true,
          "fingerprint": "abc123",
          "counts": {"PASS": 5, "FAIL": 0, "DEGRADED": 0, "UNKNOWN": 0, "SIMULATED": 0},
          "capabilities": {},
          "hardware": {
            "distro_name": "Ubuntu 24.04", "kernel": "6.18", "cpu_model": "unknown", "cpu_threads": 8,
            "ram_total_gb": 32.0, "disk_free_gb": 200.0, "gpus": [], "driver_version": null, "cuda_version": null, "windows_version": null
          },
          "blocking": [],
          "unresolved": [],
          "missing_dependencies": [],
          "checks": []
        }
        """;

    /// <summary>
    /// Gate DEGRADED, PAS FAIL, mais phase_2_authorized=false — cas RÉEL
    /// et documenté (voir docs/JSON_CONTRACTS.md côté DEV : DEGRADED est
    /// une valeur de statut distincte de FAIL) où Gate refuse malgré tout
    /// la phase suivante. Utilisé pour prouver qu'un contrôle basé
    /// uniquement sur `gate_status != FAIL` (au lieu de
    /// `phase_2_authorized`) laisserait passer un refus réel de Gate.
    /// </summary>
    public const string GateDegradedNotAuthorized = """
        {
          "schema_version": 1,
          "apex_version": "0.1.2",
          "generated_at": "2026-09-04T05:00:00Z",
          "probe": "system",
          "machine_label": null,
          "measured_on_target_machine": true,
          "gate_status": "DEGRADED",
          "phase_2_authorized": false,
          "fingerprint": "abc123",
          "counts": {"PASS": 4, "FAIL": 0, "DEGRADED": 1, "UNKNOWN": 0, "SIMULATED": 0},
          "capabilities": {},
          "hardware": {
            "distro_name": "Ubuntu 24.04", "kernel": "6.18", "cpu_model": "unknown", "cpu_threads": 8,
            "ram_total_gb": 16.0, "disk_free_gb": 100.0, "gpus": [], "driver_version": null, "cuda_version": null, "windows_version": null
          },
          "blocking": ["gate_09_vram"],
          "unresolved": [],
          "missing_dependencies": [],
          "checks": []
        }
        """;

    public const string GateFailBlocking = """
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
          "counts": {"PASS": 3, "FAIL": 2, "DEGRADED": 0, "UNKNOWN": 1, "SIMULATED": 0},
          "capabilities": {},
          "hardware": {
            "distro_name": "Ubuntu 24.04", "kernel": "6.18", "cpu_model": "unknown", "cpu_threads": 8,
            "ram_total_gb": 8.0, "disk_free_gb": 50.0, "gpus": [], "driver_version": null, "cuda_version": null, "windows_version": null
          },
          "blocking": ["gate_09_vram"],
          "unresolved": ["gate_17_docker"],
          "missing_dependencies": [
            {
              "name": "docker", "why": "isolation sandbox", "install": "apt install docker.io",
              "required_from_phase": 3, "fallback": "aucun", "check_id": "gate_17_docker"
            },
            {
              "name": "nvidia-driver", "why": "GPU", "install": "voir doc",
              "required_from_phase": 2, "fallback": "aucun", "check_id": "gate_09_vram"
            }
          ],
          "checks": []
        }
        """;
}
