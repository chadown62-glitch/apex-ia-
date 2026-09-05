using ApexSetup.Core.RuntimeAdapter;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.RuntimeAdapter;

public class RuntimeAdapterTests
{
    private static Core.RuntimeAdapter.RuntimeAdapter MakeAdapter(FakeRuntimeProcessRunner runner) =>
        new(runner, "apex", Array.Empty<string>());

    // -- version : chemin heureux ------------------------------------------

    [Fact]
    public async Task GetVersionAsync_parses_a_valid_contract()
    {
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidVersion);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetVersionAsync();

        Assert.Equal(RuntimeCallOutcome.Success, result.Outcome);
        Assert.Equal("0.1.2", result.Value!.Version);
        Assert.Equal(1, result.Value.Phase);
    }

    [Fact]
    public async Task GetVersionAsync_invokes_with_the_json_flag_never_relying_on_human_text()
    {
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidVersion);
        var adapter = MakeAdapter(runner);

        await adapter.GetVersionAsync();

        Assert.Equal(["version", "--json"], runner.CapturedArguments);
    }

    // -- doctor : chemin heureux, avec et sans enrichissement Phase 1 -------

    [Fact]
    public async Task GetDoctorAsync_parses_the_enriched_phase1_contract()
    {
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidDoctorWithSchema, exitCode: 1);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.True(result.IsSuccess);
        Assert.Equal(1, result.Value!.SchemaVersion);
        Assert.Equal("0.1.2", result.Value.ApexVersion);
        Assert.Equal(Contracts.DoctorSeverity.Fail, result.Value.Status);
        Assert.Equal(2, result.Value.Results.Count);
    }

    [Fact]
    public async Task GetDoctorAsync_accepts_the_legacy_contract_without_schema_version()
    {
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidDoctorLegacyNoSchema);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.True(result.IsSuccess);
        Assert.Null(result.Value!.SchemaVersion);
        Assert.Null(result.Value.ApexVersion);
    }

    [Fact]
    public async Task GetDoctorAsync_treats_a_nonzero_exit_code_with_valid_json_as_a_measured_state_not_an_error()
    {
        // Doctor renvoie exit_code=1 quand le pire statut est FAIL — un
        // état RÉEL mesuré, jamais une erreur de communication.
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidDoctorWithSchema, exitCode: 1);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.True(result.IsSuccess);
        Assert.Equal(Contracts.DoctorSeverity.Fail, result.Value!.Status);
    }

    // -- gate : chemin heureux, y compris gate_status FAIL -------------------

    [Fact]
    public async Task GetGateAsync_parses_a_valid_contract_including_a_fail_status()
    {
        // gate_status FAIL est une VALEUR DE CONTRAT valide, pas une
        // erreur de communication — distinction essentielle.
        var runner = FakeRuntimeProcessRunner.Success(SampleJsonPayloads.ValidGateFail, exitCode: 1);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetGateAsync();

        Assert.True(result.IsSuccess);
        Assert.Equal(Contracts.GateStatus.Fail, result.Value!.Status);
        Assert.False(result.Value.Phase2Authorized);
        Assert.Single(result.Value.MissingDependencies);
        Assert.Equal("docker", result.Value.MissingDependencies[0].Name);
        Assert.Single(result.Value.Checks);
    }

    // -- négatif : Runtime absent --------------------------------------------

    [Fact]
    public async Task GetVersionAsync_reports_ProcessNotFound_when_the_runtime_binary_is_absent()
    {
        var runner = FakeRuntimeProcessRunner.NotFound();
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetVersionAsync();

        Assert.Equal(RuntimeCallOutcome.ProcessNotFound, result.Outcome);
        Assert.Null(result.Value);
    }

    // -- négatif : timeout ----------------------------------------------------

    [Fact]
    public async Task GetDoctorAsync_reports_Timeout_when_the_process_does_not_return_in_time()
    {
        var runner = FakeRuntimeProcessRunner.TimedOut();
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.Equal(RuntimeCallOutcome.Timeout, result.Outcome);
    }

    // -- négatif : mauvais code de sortie, stdout vide -----------------------

    [Fact]
    public async Task GetGateAsync_reports_NonZeroExitCode_when_stdout_is_empty_and_exit_code_nonzero()
    {
        var runner = FakeRuntimeProcessRunner.NonZeroExit(stdout: "", stderr: "crash", exitCode: 2);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetGateAsync();

        Assert.Equal(RuntimeCallOutcome.NonZeroExitCode, result.Outcome);
        Assert.Equal(2, result.ExitCode);
    }

    // -- négatif : stdout vide, exit 0 -----------------------------------------

    [Fact]
    public async Task GetVersionAsync_reports_EmptyStdout_when_nothing_is_printed_despite_success_exit_code()
    {
        var runner = FakeRuntimeProcessRunner.Success(stdout: "   ", exitCode: 0);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetVersionAsync();

        Assert.Equal(RuntimeCallOutcome.EmptyStdout, result.Outcome);
    }

    // -- négatif : JSON invalide ------------------------------------------------

    [Fact]
    public async Task GetDoctorAsync_reports_InvalidJson_for_garbage_output()
    {
        var runner = FakeRuntimeProcessRunner.Success("ceci n'est pas du JSON {{{");
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.Equal(RuntimeCallOutcome.InvalidJson, result.Outcome);
    }

    // -- négatif : schema_version incompatible -----------------------------

    [Fact]
    public async Task GetGateAsync_reports_IncompatibleSchemaVersion_for_a_future_schema()
    {
        var futureGate = SampleJsonPayloads.ValidGateFail.Replace("\"schema_version\": 1,", "\"schema_version\": 99,");
        var runner = FakeRuntimeProcessRunner.Success(futureGate);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetGateAsync();

        Assert.Equal(RuntimeCallOutcome.IncompatibleSchemaVersion, result.Outcome);
    }

    [Fact]
    public async Task GetDoctorAsync_reports_IncompatibleSchemaVersion_for_a_future_schema()
    {
        var futureDoctor = SampleJsonPayloads.ValidDoctorWithSchema.Replace("\"schema_version\": 1,", "\"schema_version\": 42,");
        var runner = FakeRuntimeProcessRunner.Success(futureDoctor);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.Equal(RuntimeCallOutcome.IncompatibleSchemaVersion, result.Outcome);
    }

    // -- négatif : champ obligatoire absent -----------------------------------

    [Fact]
    public async Task GetGateAsync_reports_MissingRequiredField_when_fingerprint_is_absent()
    {
        var broken = SampleJsonPayloads.ValidGateFail.Replace("\"fingerprint\": \"abc123\",", "");
        var runner = FakeRuntimeProcessRunner.Success(broken);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetGateAsync();

        Assert.Equal(RuntimeCallOutcome.MissingRequiredField, result.Outcome);
        Assert.Contains("fingerprint", result.ErrorDetail);
    }

    // -- négatif : valeur d'énumération invalide -------------------------------

    [Fact]
    public async Task GetGateAsync_reports_InvalidEnumValue_for_an_unrecognized_gate_status()
    {
        var broken = SampleJsonPayloads.ValidGateFail.Replace("\"gate_status\": \"FAIL\",", "\"gate_status\": \"MAYBE\",");
        var runner = FakeRuntimeProcessRunner.Success(broken);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetGateAsync();

        Assert.Equal(RuntimeCallOutcome.InvalidEnumValue, result.Outcome);
    }

    [Fact]
    public async Task GetDoctorAsync_reports_InvalidEnumValue_for_an_unrecognized_severity_including_uppercase_gate_style()
    {
        // Preuve que la casse compte : "PASS" (style Gate) n'est jamais
        // accepté pour Doctor ("pass" attendu) — aucune tolérance
        // heuristique entre les deux vocabulaires.
        var broken = SampleJsonPayloads.ValidDoctorWithSchema.Replace("\"severity\": \"pass\",", "\"severity\": \"PASS\",");
        var runner = FakeRuntimeProcessRunner.Success(broken);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetDoctorAsync();

        Assert.Equal(RuntimeCallOutcome.InvalidEnumValue, result.Outcome);
    }

    // -- négatif : type de champ inattendu -------------------------------------

    [Fact]
    public async Task GetVersionAsync_reports_UnexpectedFieldType_when_phase_is_not_a_number()
    {
        var broken = SampleJsonPayloads.ValidVersion.Replace("\"phase\": 1,", "\"phase\": \"one\",");
        var runner = FakeRuntimeProcessRunner.Success(broken);
        var adapter = MakeAdapter(runner);

        var result = await adapter.GetVersionAsync();

        Assert.Equal(RuntimeCallOutcome.UnexpectedFieldType, result.Outcome);
    }
}
