using System.Text.Json;
using System.Text.Json.Serialization;
using ApexSetup.Application.Contracts;
using ApexSetup.Core.Preflight;

namespace ApexSetup.Launcher;

/// <summary>
/// Dérive l'un des 8 <see cref="LauncherOutcome"/> à partir des contrats
/// Setup EXISTANTS — <c>StatusResponse</c> (état d'installation persisté)
/// et <c>ValidateResponse</c> (Runtime/Doctor/Gate) — SANS jamais
/// réinterpréter la logique métier de Gate ou du Doctor : Gate reste seul
/// juge de <c>Phase2Authorized</c>/<c>Blocking</c> (règle absolue #7 de la
/// mission), ce moteur ne fait que LIRE ce verdict déjà calculé par
/// <c>ISetupApplicationService.ValidateAsync</c>.
///
/// Fonction pure — aucune E/S, aucun appel réseau/processus, entièrement
/// testable sans Windows ni sous-processus Runtime. C'est délibéré : c'est
/// la seule pièce de <c>ApexSetup.Launcher</c> qui porte de la décision, et
/// elle doit rester vérifiable par de simples tests unitaires (voir
/// ApexSetup.Launcher.Tests/LauncherStateEngineTests.cs).
/// </summary>
public static class LauncherStateEngine
{
    private static readonly JsonSerializerOptions EnumOptions = new()
    {
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
    };

    /// <param name="status">Résultat de <c>ISetupApplicationService.StatusAsync</c> — lecture pure de l'état d'installation persisté.</param>
    /// <param name="validate">
    /// Résultat de <c>ISetupApplicationService.ValidateAsync</c> — <c>null</c>
    /// uniquement si l'installation n'existe pas encore ou est incohérente
    /// (auquel cas valider le Runtime n'a pas de sens : il n'y a rien à
    /// valider). Ne JAMAIS appeler Validate avant d'avoir confirmé
    /// <c>Completed</c> côté <see cref="StatusResponse"/> — Validate suppose
    /// une installation terminée.
    /// </param>
    /// <param name="minimumRuntimeVersion">
    /// Plancher de version optionnel. <c>null</c> désactive honnêtement la
    /// détection <see cref="LauncherOutcome.NeedsUpdate"/> — la mission ne
    /// spécifie aucune source pour ce plancher (pas de service de mise à
    /// jour distant dans ce dépôt) ; fabriquer une valeur inventée serait
    /// pire qu'admettre l'absence de vérification tant qu'aucune source
    /// fiable n'existe.
    /// </param>
    public static LauncherDecision Derive(
        StatusResponse status, ValidateResponse? validate, string? minimumRuntimeVersion)
    {
        if (!status.Success && status.Errors.Count > 0)
        {
            return Unknown($"status a échoué : {string.Join("; ", status.Errors.Select(e => e.Message))}");
        }

        var lifecycle = ParseLifecycle(status.State);
        if (lifecycle is null)
        {
            return Unknown($"état de cycle de vie non reconnu : « {status.State ?? "(absent)"} »");
        }

        switch (lifecycle.Value)
        {
            case SetupLifecycleState.NoInstallation:
            case SetupLifecycleState.RollbackPerformed:
                return new LauncherDecision
                {
                    Outcome = LauncherOutcome.NeedsSetup,
                    Detail = $"aucune installation exploitable (état : {status.State})",
                    AutoRecoverable = false,
                };

            case SetupLifecycleState.RecoveryNeeded:
                return new LauncherDecision
                {
                    Outcome = LauncherOutcome.NeedsRecovery,
                    Detail = "installation interrompue — réparation automatique disponible via RecoverAsync",
                    AutoRecoverable = true,
                };

            case SetupLifecycleState.Inconsistent:
                return new LauncherDecision
                {
                    Outcome = LauncherOutcome.InvalidInstall,
                    Detail = "état transactionnel incohérent — nécessite une intervention humaine (GUI Setup)",
                    AutoRecoverable = false,
                };

            case SetupLifecycleState.InProgress:
                // Une installation est ACTIVEMENT en cours (verrou tenu par
                // un autre processus, voir InstallationLock) — pas encore
                // « recovery » (personne n'a encore rien abandonné), donc
                // toujours NeedsSetup : le launcher ne doit pas démarrer
                // APEX pendant qu'une installation écrit sur le disque.
                return new LauncherDecision
                {
                    Outcome = LauncherOutcome.NeedsSetup,
                    Detail = "une installation est actuellement en cours (verrou tenu)",
                    AutoRecoverable = false,
                };

            case SetupLifecycleState.Completed:
                return DeriveFromValidation(validate, minimumRuntimeVersion);

            default:
                return Unknown($"valeur SetupLifecycleState non gérée : {lifecycle.Value}");
        }
    }

    private static LauncherDecision DeriveFromValidation(ValidateResponse? validate, string? minimumRuntimeVersion)
    {
        if (validate is null)
        {
            return Unknown("installation complète mais aucune donnée de validation Runtime/Gate fournie");
        }

        if (!validate.RuntimeReachable)
        {
            return new LauncherDecision
            {
                Outcome = LauncherOutcome.RuntimeError,
                Detail = "le Runtime ne répond pas (RuntimeReachable == false)",
                AutoRecoverable = false,
            };
        }

        if (validate.Doctor is { Status: "fail" })
        {
            return new LauncherDecision
            {
                Outcome = LauncherOutcome.RuntimeError,
                Detail = "apex doctor rapporte un échec bloquant",
                AutoRecoverable = false,
            };
        }

        // Règle absolue #7 : Gate reste seul juge de l'autorisation. On lit
        // Phase2Authorized/Blocking TELS QUE calculés par le Runtime — on ne
        // les recalcule jamais ici.
        if (validate.Gate is { } gate && (!gate.Phase2Authorized || gate.Blocking.Count > 0))
        {
            var reason = gate.Blocking.Count > 0 ? string.Join(", ", gate.Blocking) : gate.Status;
            return new LauncherDecision
            {
                Outcome = LauncherOutcome.Blocked,
                Detail = $"Gate refuse l'autorisation : {reason}",
                AutoRecoverable = false,
            };
        }

        if (minimumRuntimeVersion is not null
            && validate.Version is { Version: var observed }
            && !ApexVersionComparer.IsAtLeast(observed, minimumRuntimeVersion))
        {
            return new LauncherDecision
            {
                Outcome = LauncherOutcome.NeedsUpdate,
                Detail = $"version Runtime observée « {observed} » < plancher requis « {minimumRuntimeVersion} »",
                AutoRecoverable = false,
            };
        }

        return new LauncherDecision
        {
            Outcome = LauncherOutcome.Ready,
            Detail = "installation complète, Runtime joignable, Gate autorise",
            AutoRecoverable = false,
        };
    }

    private static SetupLifecycleState? ParseLifecycle(string? wireValue)
    {
        if (string.IsNullOrEmpty(wireValue))
        {
            return null;
        }

        try
        {
            return JsonSerializer.Deserialize<SetupLifecycleState>($"\"{wireValue}\"", EnumOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static LauncherDecision Unknown(string detail) => new()
    {
        Outcome = LauncherOutcome.UnknownError,
        Detail = detail,
        AutoRecoverable = false,
    };
}
