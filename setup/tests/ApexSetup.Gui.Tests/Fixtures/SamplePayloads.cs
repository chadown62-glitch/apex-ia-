namespace ApexSetup.Gui.Tests.Fixtures;

internal static class SamplePayloads
{
    public const string Version = """
        {"version":"0.1.2","phase":1,"phase_name":"Test","python":"3.12.3","platform":"Linux","executable":"/usr/bin/python3"}
        """;

    public const string DoctorPass = """
        {"schema_version":1,"apex_version":"0.1.2","generated_at":"2026-09-04T00:00:00Z","status":"pass","counts":{"pass":1,"warn":0,"fail":0,"skip":0},"duration_ms":1.0,"results":[]}
        """;

    public const string GatePassAuthorized = """
        {"schema_version":1,"apex_version":"0.1.2","generated_at":"2026-09-04T00:00:00Z","probe":"system","machine_label":null,
         "measured_on_target_machine":true,"gate_status":"PASS","phase_2_authorized":true,"fingerprint":"abc",
         "counts":{"PASS":1,"FAIL":0,"DEGRADED":0,"UNKNOWN":0,"SIMULATED":0},"capabilities":{},
         "hardware":{"distro_name":"Ubuntu","kernel":"6.18","cpu_model":"x","cpu_threads":8,"ram_total_gb":32.0,"disk_free_gb":200.0,
         "gpus":[],"driver_version":null,"cuda_version":null,"windows_version":null},
         "blocking":[],"unresolved":[],"missing_dependencies":[],"checks":[]}
        """;

    public const string GateFailBlocking = """
        {"schema_version":1,"apex_version":"0.1.2","generated_at":"2026-09-04T00:00:00Z","probe":"system","machine_label":null,
         "measured_on_target_machine":true,"gate_status":"FAIL","phase_2_authorized":false,"fingerprint":"abc",
         "counts":{"PASS":0,"FAIL":1,"DEGRADED":0,"UNKNOWN":0,"SIMULATED":0},"capabilities":{},
         "hardware":{"distro_name":"Ubuntu","kernel":"6.18","cpu_model":"x","cpu_threads":8,"ram_total_gb":8.0,"disk_free_gb":50.0,
         "gpus":[],"driver_version":null,"cuda_version":null,"windows_version":null},
         "blocking":["gate_09"],"unresolved":[],"missing_dependencies":[],"checks":[]}
        """;
}
