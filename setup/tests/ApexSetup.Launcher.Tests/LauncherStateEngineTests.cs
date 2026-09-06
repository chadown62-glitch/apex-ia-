using ApexSetup.Application.Contracts;
using Xunit;

namespace ApexSetup.Launcher.Tests;

public class LauncherStateEngineTests
{
    private static StatusResponse Status(string state, bool success = true, IReadOnlyList<CliError>? errors = null) => new()
    {
        Operation = "status", Success = success, State = state,
        Errors = errors ?? [], Warnings = [],
        LockCurrentlyHeld = false, PersistedState = null, Detail = "test",
    };

    private static ValidateResponse Validate(
        bool runtimeReachable = true, string? doctorStatus = "pass",
        bool phase2Authorized = true, IReadOnlyList<string>? blocking = null, string? version = "0.5.0") => new()
    {
        Operation = "validate", Success = true, Errors = [], Warnings = [],
        RuntimeReachable = runtimeReachable,
        Version = version is null ? null : new RuntimeVersionSummary
        {
            Version = version, Phase = 1, PhaseName = "test", Python = "3.12", Platform = "linux",
        },
        Doctor = doctorStatus is null ? null : new DoctorSummary { Status = doctorStatus, Counts = new Dictionary<string, int>(), ResultsCount = 0 },
        Gate = new GateSummary
        {
            Status = phase2Authorized ? "pass" : "fail",
            Phase2Authorized = phase2Authorized,
            Blocking = blocking ?? [],
            Unresolved = [],
        },
    };

    [Fact]
    public void No_installation_yields_NeedsSetup()
    {
        var decision = LauncherStateEngine.Derive(Status("no_installation"), null, null);
        Assert.Equal(LauncherOutcome.NeedsSetup, decision.Outcome);
        Assert.False(decision.AutoRecoverable);
    }

    [Fact]
    public void Rollback_performed_yields_NeedsSetup()
    {
        var decision = LauncherStateEngine.Derive(Status("rollback_performed"), null, null);
        Assert.Equal(LauncherOutcome.NeedsSetup, decision.Outcome);
    }

    [Fact]
    public void In_progress_yields_NeedsSetup_not_recovery()
    {
        // Une installation ACTIVEMENT en cours n'est pas encore "recovery" —
        // personne n'a encore abandonné quoi que ce soit (voir docstring
        // LauncherStateEngine).
        var decision = LauncherStateEngine.Derive(Status("in_progress"), null, null);
        Assert.Equal(LauncherOutcome.NeedsSetup, decision.Outcome);
        Assert.False(decision.AutoRecoverable);
    }

    [Fact]
    public void Recovery_needed_yields_NeedsRecovery_and_is_auto_recoverable()
    {
        var decision = LauncherStateEngine.Derive(Status("recovery_needed"), null, null);
        Assert.Equal(LauncherOutcome.NeedsRecovery, decision.Outcome);
        Assert.True(decision.AutoRecoverable);
    }

    [Fact]
    public void Inconsistent_yields_InvalidInstall_never_auto_recoverable()
    {
        var decision = LauncherStateEngine.Derive(Status("inconsistent"), null, null);
        Assert.Equal(LauncherOutcome.InvalidInstall, decision.Outcome);
        Assert.False(decision.AutoRecoverable);
    }

    [Fact]
    public void Completed_with_full_validation_yields_Ready()
    {
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(), null);
        Assert.Equal(LauncherOutcome.Ready, decision.Outcome);
    }

    [Fact]
    public void Completed_without_validation_data_yields_UnknownError()
    {
        // ValidateAsync suppose une installation terminée — l'absence de
        // données de validation pour un état "completed" est une
        // incohérence de l'appelant, jamais silencieusement traitée comme Ready.
        var decision = LauncherStateEngine.Derive(Status("completed"), null, null);
        Assert.Equal(LauncherOutcome.UnknownError, decision.Outcome);
    }

    [Fact]
    public void Runtime_unreachable_yields_RuntimeError()
    {
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(runtimeReachable: false), null);
        Assert.Equal(LauncherOutcome.RuntimeError, decision.Outcome);
    }

    [Fact]
    public void Doctor_fail_yields_RuntimeError_even_if_runtime_reachable()
    {
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(doctorStatus: "fail"), null);
        Assert.Equal(LauncherOutcome.RuntimeError, decision.Outcome);
    }

    [Fact]
    public void Gate_not_authorized_yields_Blocked_regardless_of_status_text()
    {
        // Règle absolue #7 : on lit Phase2Authorized tel quel — jamais
        // recalculé — même si Status affiche autre chose que "fail".
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(phase2Authorized: false), null);
        Assert.Equal(LauncherOutcome.Blocked, decision.Outcome);
    }

    [Fact]
    public void Gate_with_blocking_items_yields_Blocked_even_if_phase2_authorized_flag_is_true()
    {
        var decision = LauncherStateEngine.Derive(
            Status("completed"), Validate(phase2Authorized: true, blocking: ["GATE-05"]), null);
        Assert.Equal(LauncherOutcome.Blocked, decision.Outcome);
    }

    [Fact]
    public void Version_below_minimum_yields_NeedsUpdate()
    {
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(version: "0.1.0"), "0.5.0");
        Assert.Equal(LauncherOutcome.NeedsUpdate, decision.Outcome);
    }

    [Fact]
    public void Version_at_or_above_minimum_yields_Ready()
    {
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(version: "0.5.0"), "0.5.0");
        Assert.Equal(LauncherOutcome.Ready, decision.Outcome);
    }

    [Fact]
    public void Null_minimum_version_never_triggers_NeedsUpdate()
    {
        // Aucune source fiable de plancher de version n'existe dans ce
        // dépôt — désactiver honnêtement la vérification (voir docstring
        // Derive) plutôt que d'inventer une valeur.
        var decision = LauncherStateEngine.Derive(Status("completed"), Validate(version: "0.0.1"), null);
        Assert.Equal(LauncherOutcome.Ready, decision.Outcome);
    }

    [Fact]
    public void Unrecognized_lifecycle_state_yields_UnknownError_never_a_crash()
    {
        var decision = LauncherStateEngine.Derive(Status("some_future_state_this_engine_does_not_know"), null, null);
        Assert.Equal(LauncherOutcome.UnknownError, decision.Outcome);
    }

    [Fact]
    public void Missing_state_string_yields_UnknownError()
    {
        var decision = LauncherStateEngine.Derive(Status(null!), null, null);
        Assert.Equal(LauncherOutcome.UnknownError, decision.Outcome);
    }

    [Fact]
    public void Status_failure_with_errors_yields_UnknownError()
    {
        var decision = LauncherStateEngine.Derive(
            Status("no_installation", success: false, errors: [new CliError { Code = "x", Message = "boom" }]),
            null, null);
        Assert.Equal(LauncherOutcome.UnknownError, decision.Outcome);
    }
}
