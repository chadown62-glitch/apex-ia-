using ApexSetup.Application;
using ApexSetup.Application.Contracts;

namespace ApexSetup.Cli;

/// <summary>
/// Traduit une réponse applicative en <see cref="SetupExitCode"/> — SEUL
/// endroit du CLI qui fait cette traduction, pour que chaque commande
/// utilise exactement la même règle (voir docs/CLI.md, table des codes de
/// sortie). Lit uniquement des champs déjà structurés (jamais de texte
/// libre) — aucune logique métier ici, seulement un mapping déterministe.
/// </summary>
internal static class ExitCodeMapper
{
    public static SetupExitCode For(VersionResponse response) => SetupExitCode.Success;

    public static SetupExitCode For(DetectResponse response) =>
        response.Success ? SetupExitCode.Success : SetupExitCode.InternalError;

    public static SetupExitCode For(CheckResponse response)
    {
        if (response.Errors.Any(e => e.Code == "manifest_load_failed" || e.Code == "missing_source_url"))
        {
            return SetupExitCode.InvalidArguments;
        }
        if (!response.HasBlockingFailure)
        {
            return SetupExitCode.Success;
        }
        if (response.Checks.Any(c => c.Name.StartsWith("runtime.", StringComparison.Ordinal) && !c.Passed))
        {
            return SetupExitCode.RuntimeUnavailable;
        }
        if (response.Checks.Any(c => c.Name == "gate.phase2_authorized" && !c.Passed))
        {
            return SetupExitCode.GateBlocking;
        }
        return SetupExitCode.PreflightBlocking;
    }

    public static SetupExitCode For(PlanResponse response) =>
        response.Success ? SetupExitCode.Success : SetupExitCode.InvalidArguments;

    public static SetupExitCode For(InstallResponse response)
    {
        if (response.Success)
        {
            return SetupExitCode.Success;
        }
        var code = response.Errors.Count > 0 ? response.Errors[0].Code : null;
        return code switch
        {
            "plan_build_failed" => SetupExitCode.InvalidArguments,
            "runtime_unavailable" => SetupExitCode.RuntimeUnavailable,
            "gate_blocking" => SetupExitCode.GateBlocking,
            "preflight_blocking" => SetupExitCode.PreflightBlocking,
            "concurrency_refused" => SetupExitCode.ConcurrencyRefused,
            "download_failed" => SetupExitCode.DownloadFailed,
            "integrity_failed" => SetupExitCode.IntegrityFailed,
            "installation_failed" => SetupExitCode.InstallationFailed,
            "rollback_failed" => SetupExitCode.RollbackFailed,
            "validation_failed" => SetupExitCode.ValidationFailed,
            _ => SetupExitCode.InternalError,
        };
    }

    public static SetupExitCode For(RecoverResponse response)
    {
        if (response.Success)
        {
            return response.ActionTaken == "rollback_performed" ? SetupExitCode.Success : SetupExitCode.Success;
        }
        var code = response.Errors.Count > 0 ? response.Errors[0].Code : null;
        return code switch
        {
            "concurrency_refused" => SetupExitCode.ConcurrencyRefused,
            "state_inconsistent" => SetupExitCode.InconsistentState,
            _ => SetupExitCode.InternalError,
        };
    }

    public static SetupExitCode For(ValidateResponse response)
    {
        if (response.Success)
        {
            return SetupExitCode.Success;
        }
        if (!response.RuntimeReachable)
        {
            return SetupExitCode.RuntimeUnavailable;
        }
        return SetupExitCode.ValidationFailed;
    }

    public static SetupExitCode For(StatusResponse response)
    {
        // `State` est du texte snake_case (voir `SetupApplicationService.Wire`),
        // jamais le nom brut de l'enum C# — comparaison alignée sur la même convention.
        return response.State switch
        {
            "recovery_needed" => SetupExitCode.RecoveryRequired,
            "inconsistent" => SetupExitCode.InconsistentState,
            _ => SetupExitCode.Success,
        };
    }
}
