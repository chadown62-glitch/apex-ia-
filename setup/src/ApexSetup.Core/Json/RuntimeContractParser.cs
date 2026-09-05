using System.Text.Json;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.RuntimeAdapter;
using static ApexSetup.Core.Json.JsonHelpers;

namespace ApexSetup.Core.Json;

/// <summary>
/// Parseurs stricts des 3 contrats Runtime consommés par Setup Phase 1.
/// Policy sur les champs inconnus : IGNORÉS (compatibilité ascendante —
/// même règle que documentée côté Runtime dans docs/JSON_CONTRACTS.md :
/// « un consommateur correctement écrit ignore les clés qu'il ne connaît
/// pas »). Seuls les champs absents ou mal typés parmi ceux CONNUS de ce
/// Setup font échouer le parsing.
/// </summary>
public static class RuntimeContractParser
{
    // -- version --------------------------------------------------------

    public static RuntimeVersionResult ParseVersion(JsonElement root) => new()
    {
        Version = RequireString(root, "version"),
        Phase = RequireInt(root, "phase"),
        PhaseName = RequireString(root, "phase_name"),
        Python = RequireString(root, "python"),
        Platform = RequireString(root, "platform"),
        Executable = RequireString(root, "executable"),
    };

    // -- doctor -----------------------------------------------------------

    private static readonly IReadOnlyDictionary<string, DoctorSeverity> DoctorSeverityValues = new Dictionary<string, DoctorSeverity>
    {
        ["pass"] = DoctorSeverity.Pass,
        ["warn"] = DoctorSeverity.Warn,
        ["fail"] = DoctorSeverity.Fail,
        ["skip"] = DoctorSeverity.Skip,
    };

    private static DoctorSeverity ParseDoctorSeverity(string raw, string fieldName)
    {
        if (DoctorSeverityValues.TryGetValue(raw, out var severity))
        {
            return severity;
        }
        throw new ContractValidationException(
            RuntimeCallOutcome.InvalidEnumValue,
            $"« {fieldName} » : valeur d'énumération inconnue « {raw} » (attendu pass|warn|fail|skip)");
    }

    public static RuntimeDoctorResult ParseDoctor(JsonElement root)
    {
        // schema_version/apex_version/generated_at sont OPTIONNELS —
        // absents d'un Runtime construit avant l'enrichissement Phase 1
        // du contrat Doctor (voir RuntimeDoctorResult).
        var schemaVersion = OptionalInt(root, "schema_version");
        if (schemaVersion is int observed && !RuntimeSchemaPolicy.IsKnown(observed, RuntimeSchemaPolicy.KnownDoctorSchemaVersion))
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.IncompatibleSchemaVersion,
                $"schema_version Doctor {observed} > connu de ce Setup ({RuntimeSchemaPolicy.KnownDoctorSchemaVersion})");
        }

        var results = new List<DoctorCheckResult>();
        foreach (var item in RequireArray(root, "results").EnumerateArray())
        {
            results.Add(new DoctorCheckResult
            {
                Name = RequireString(item, "name"),
                Category = RequireString(item, "category"),
                Severity = ParseDoctorSeverity(RequireString(item, "severity"), "results[].severity"),
                Message = RequireString(item, "message"),
                Remediation = RequireString(item, "remediation"),
                Facts = ToFreeFormDictionary(RequireObject(item, "facts")),
                DurationMs = RequireDouble(item, "duration_ms"),
            });
        }

        return new RuntimeDoctorResult
        {
            SchemaVersion = schemaVersion,
            ApexVersion = OptionalString(root, "apex_version"),
            GeneratedAt = OptionalString(root, "generated_at"),
            Status = ParseDoctorSeverity(RequireString(root, "status"), "status"),
            Counts = RequireIntDictionary(root, "counts"),
            DurationMs = RequireDouble(root, "duration_ms"),
            Results = results,
        };
    }

    // -- gate ---------------------------------------------------------

    private static readonly IReadOnlyDictionary<string, GateStatus> GateStatusValues = new Dictionary<string, GateStatus>
    {
        ["PASS"] = GateStatus.Pass,
        ["FAIL"] = GateStatus.Fail,
        ["DEGRADED"] = GateStatus.Degraded,
        ["UNKNOWN"] = GateStatus.Unknown,
        ["SIMULATED"] = GateStatus.Simulated,
    };

    private static GateStatus ParseGateStatus(string raw, string fieldName)
    {
        if (GateStatusValues.TryGetValue(raw, out var status))
        {
            return status;
        }
        throw new ContractValidationException(
            RuntimeCallOutcome.InvalidEnumValue,
            $"« {fieldName} » : valeur d'énumération inconnue « {raw} » (attendu PASS|FAIL|DEGRADED|UNKNOWN|SIMULATED)");
    }

    private static RuntimeDependencyInfo ParseDependency(JsonElement item) => new()
    {
        Name = RequireString(item, "name"),
        Why = RequireString(item, "why"),
        Install = RequireString(item, "install"),
        RequiredFromPhase = RequireInt(item, "required_from_phase"),
        Fallback = RequireString(item, "fallback"),
        CheckId = RequireString(item, "check_id"),
    };

    private static GateCheckResult ParseGateCheck(JsonElement item) => new()
    {
        CheckId = RequireString(item, "check_id"),
        Title = RequireString(item, "title"),
        Status = ParseGateStatus(RequireString(item, "status"), "checks[].status"),
        Message = RequireString(item, "message"),
        Facts = ToFreeFormDictionary(RequireObject(item, "facts")),
        Remediation = RequireString(item, "remediation"),
        BlocksPhases = RequireArray(item, "blocks_phases").EnumerateArray()
            .Select(e => e.GetInt32()).ToList(),
        ToValidateOnTarget = RequireBool(item, "to_validate_on_target"),
        UnknownReason = RequireString(item, "unknown_reason"),
    };

    private static RuntimeHardwareFacts ParseHardware(JsonElement hardware) => new()
    {
        DistroName = OptionalString(hardware, "distro_name"),
        Kernel = OptionalString(hardware, "kernel"),
        CpuModel = OptionalString(hardware, "cpu_model"),
        CpuThreads = OptionalInt(hardware, "cpu_threads"),
        RamTotalGb = OptionalDouble(hardware, "ram_total_gb"),
        DiskFreeGb = OptionalDouble(hardware, "disk_free_gb"),
        Gpus = hardware.TryGetProperty("gpus", out var gpus) && gpus.ValueKind == JsonValueKind.Array
            ? gpus.EnumerateArray().Select(ToFreeFormDictionary).ToList()
            : null,
        DriverVersion = OptionalString(hardware, "driver_version"),
        CudaVersion = OptionalString(hardware, "cuda_version"),
        WindowsVersion = OptionalString(hardware, "windows_version"),
    };

    public static RuntimeGateResult ParseGate(JsonElement root)
    {
        var schemaVersion = RequireInt(root, "schema_version");
        if (!RuntimeSchemaPolicy.IsKnown(schemaVersion, RuntimeSchemaPolicy.KnownGateSchemaVersion))
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.IncompatibleSchemaVersion,
                $"schema_version Gate {schemaVersion} > connu de ce Setup ({RuntimeSchemaPolicy.KnownGateSchemaVersion})");
        }

        return new RuntimeGateResult
        {
            SchemaVersion = schemaVersion,
            ApexVersion = RequireString(root, "apex_version"),
            GeneratedAt = RequireString(root, "generated_at"),
            Probe = RequireString(root, "probe"),
            MachineLabel = OptionalString(root, "machine_label"),
            MeasuredOnTargetMachine = RequireBool(root, "measured_on_target_machine"),
            Status = ParseGateStatus(RequireString(root, "gate_status"), "gate_status"),
            Phase2Authorized = RequireBool(root, "phase_2_authorized"),
            Fingerprint = RequireString(root, "fingerprint"),
            Counts = RequireIntDictionary(root, "counts"),
            Capabilities = ToFreeFormDictionary(RequireObject(root, "capabilities")),
            Hardware = ParseHardware(RequireObject(root, "hardware")),
            Blocking = RequireArray(root, "blocking").EnumerateArray().Select(e => e.GetString()!).ToList(),
            Unresolved = RequireArray(root, "unresolved").EnumerateArray().Select(e => e.GetString()!).ToList(),
            MissingDependencies = RequireArray(root, "missing_dependencies").EnumerateArray()
                .Select(ParseDependency).ToList(),
            Checks = RequireArray(root, "checks").EnumerateArray().Select(ParseGateCheck).ToList(),
        };
    }
}
