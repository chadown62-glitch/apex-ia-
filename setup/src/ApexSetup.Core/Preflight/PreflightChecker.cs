using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Core.Preflight;

public sealed record PreflightCheckResult
{
    public required string Name { get; init; }
    public required bool Passed { get; init; }
    public required bool Blocking { get; init; }
    public required string Detail { get; init; }
}

/// <summary>
/// Rapport agrégé. <see cref="HasBlockingFailure"/> est la seule question
/// que le pipeline pose avant de commencer — « aucune installation ne
/// doit commencer si une condition bloquante échoue » (règle explicite).
/// </summary>
public sealed record PreflightReport
{
    public required IReadOnlyList<PreflightCheckResult> Results { get; init; }
    public bool HasBlockingFailure => Results.Any(r => r.Blocking && !r.Passed);
    public bool AllPassed => Results.All(r => r.Passed);
}

/// <summary>
/// Les 11 vérifications préalables exigées, exécutées avant toute
/// modification d'état. Chacune est indépendante et continue de
/// s'exécuter même si une précédente a échoué — le rapport doit être
/// COMPLET (jamais s'arrêter à la première panne), pour qu'un utilisateur
/// voie tout ce qui bloque en une fois plutôt qu'un problème à la fois.
/// </summary>
public static class PreflightChecker
{
    public const string DefaultMinimumApexVersion = "0.1.0";

    public static async Task<PreflightReport> RunAsync(
        Core.RuntimeAdapter.RuntimeAdapter runtimeAdapter,
        InstallationPlan plan,
        IEnvironmentProbe environmentProbe,
        string minimumApexVersion = DefaultMinimumApexVersion,
        CancellationToken cancellationToken = default)
    {
        var results = new List<PreflightCheckResult>();

        var versionResult = await runtimeAdapter.GetVersionAsync(cancellationToken).ConfigureAwait(false);
        results.Add(CheckRuntimeAvailable(versionResult));
        results.Add(CheckVersionCompatible(versionResult, minimumApexVersion));

        var doctorResult = await runtimeAdapter.GetDoctorAsync(cancellationToken).ConfigureAwait(false);
        results.Add(CheckDoctorExploitable(doctorResult));

        var gateResult = await runtimeAdapter.GetGateAsync(cancellationToken).ConfigureAwait(false);
        results.Add(CheckGateExploitable(gateResult));

        results.Add(CheckHardwareProfileDetermined(plan));
        results.Add(CheckDiskSpace(environmentProbe, plan));
        results.Add(CheckPermissions(environmentProbe, plan));
        results.Add(CheckDestinationValid(plan));
        results.Add(CheckManifestValid(plan));
        results.Add(CheckRequiredModelsKnown(plan));
        results.Add(CheckDependenciesKnown(plan, gateResult));

        return new PreflightReport { Results = results };
    }

    private static PreflightCheckResult CheckRuntimeAvailable(RuntimeCallResult<RuntimeVersionResult> versionResult) => new()
    {
        Name = "runtime.available",
        Passed = versionResult.IsSuccess,
        Blocking = true,
        Detail = versionResult.IsSuccess
            ? $"Runtime {versionResult.Value!.Version} joignable"
            : $"Runtime injoignable : {versionResult.Outcome} — {versionResult.ErrorDetail}",
    };

    private static PreflightCheckResult CheckVersionCompatible(RuntimeCallResult<RuntimeVersionResult> versionResult, string minimum)
    {
        if (!versionResult.IsSuccess)
        {
            return new PreflightCheckResult
            {
                Name = "runtime.version_compatible", Passed = false, Blocking = true,
                Detail = "version Runtime inconnue (Runtime injoignable)",
            };
        }
        var observed = versionResult.Value!.Version;
        var compatible = ApexVersionComparer.IsAtLeast(observed, minimum);
        return new PreflightCheckResult
        {
            Name = "runtime.version_compatible",
            Passed = compatible,
            Blocking = true,
            Detail = compatible
                ? $"{observed} >= {minimum}"
                : $"{observed} < {minimum} minimum requis",
        };
    }

    private static PreflightCheckResult CheckDoctorExploitable(RuntimeCallResult<RuntimeDoctorResult> doctorResult) => new()
    {
        Name = "runtime.doctor_exploitable",
        // "Exploitable" = un contrat structuré a été reçu et parsé — un
        // statut FAIL mesuré par Doctor est une donnée exploitable, pas
        // un échec de cette vérification (distinct de doctor.status).
        Passed = doctorResult.IsSuccess,
        Blocking = true,
        Detail = doctorResult.IsSuccess
            ? $"Doctor exploitable, statut mesuré : {doctorResult.Value!.Status}"
            : $"Doctor inexploitable : {doctorResult.Outcome} — {doctorResult.ErrorDetail}",
    };

    private static PreflightCheckResult CheckGateExploitable(RuntimeCallResult<RuntimeGateResult> gateResult) => new()
    {
        Name = "runtime.gate_exploitable",
        Passed = gateResult.IsSuccess,
        Blocking = true,
        Detail = gateResult.IsSuccess
            ? $"Gate exploitable, statut mesuré : {gateResult.Value!.Status}"
            : $"Gate inexploitable : {gateResult.Outcome} — {gateResult.ErrorDetail}",
    };

    private static PreflightCheckResult CheckHardwareProfileDetermined(InstallationPlan plan) => new()
    {
        Name = "hardware.profile_determined",
        // Le plan porte déjà un SelectedProfile — cette vérification
        // s'assure qu'il correspond à une entrée réelle du catalogue,
        // jamais une valeur d'enum orpheline construite ailleurs.
        Passed = HardwareProfileCatalog.Profiles.ContainsKey(plan.SelectedProfile),
        Blocking = true,
        Detail = $"profil sélectionné : {plan.SelectedProfile}",
    };

    private static PreflightCheckResult CheckDiskSpace(IEnvironmentProbe probe, InstallationPlan plan)
    {
        long available;
        try
        {
            available = probe.GetAvailableDiskBytes(plan.Destination);
        }
        catch (Exception ex)
        {
            return new PreflightCheckResult
            {
                Name = "disk.sufficient_space", Passed = false, Blocking = true,
                Detail = $"impossible de mesurer l'espace disque : {ex.Message}",
            };
        }
        var sufficient = available >= plan.RequiredDiskBytes;
        return new PreflightCheckResult
        {
            Name = "disk.sufficient_space",
            Passed = sufficient,
            Blocking = true,
            Detail = $"{available:N0} octets disponibles, {plan.RequiredDiskBytes:N0} requis",
        };
    }

    private static PreflightCheckResult CheckPermissions(IEnvironmentProbe probe, InstallationPlan plan)
    {
        var canWrite = probe.CanWriteTo(plan.Destination);
        return new PreflightCheckResult
        {
            Name = "fs.write_permission",
            Passed = canWrite,
            Blocking = true,
            Detail = canWrite ? $"écriture possible dans {plan.Destination}" : $"écriture REFUSÉE dans {plan.Destination}",
        };
    }

    private static PreflightCheckResult CheckDestinationValid(InstallationPlan plan)
    {
        var valid = !string.IsNullOrWhiteSpace(plan.Destination) && Path.IsPathRooted(plan.Destination);
        if (valid)
        {
            var full = Path.GetFullPath(plan.Destination);
            var root = Path.GetPathRoot(full);
            // Jamais installer directement à la racine d'un volume.
            valid = !string.Equals(full.TrimEnd(Path.DirectorySeparatorChar), root?.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);
        }
        return new PreflightCheckResult
        {
            Name = "destination.valid",
            Passed = valid,
            Blocking = true,
            Detail = valid ? $"destination valide : {plan.Destination}" : $"destination invalide : « {plan.Destination} »",
        };
    }

    private static PreflightCheckResult CheckManifestValid(InstallationPlan plan)
    {
        var invalid = plan.RequiredModels.Where(m => m.Sha256.Length != 64 || m.SizeBytes <= 0).ToList();
        return new PreflightCheckResult
        {
            Name = "manifest.valid",
            Passed = invalid.Count == 0,
            Blocking = true,
            Detail = invalid.Count == 0
                ? $"{plan.RequiredModels.Count} entrée(s) de manifeste valides"
                : $"entrée(s) invalide(s) : {string.Join(", ", invalid.Select(m => m.ModelId))}",
        };
    }

    private static PreflightCheckResult CheckRequiredModelsKnown(InstallationPlan plan)
    {
        var profile = HardwareProfileCatalog.Profiles[plan.SelectedProfile];
        var needed = new List<string> { profile.PrimaryModelId, profile.EmbeddingModelId };
        if (profile.FallbackModelId is not null)
        {
            needed.Add(profile.FallbackModelId);
        }
        var known = plan.RequiredModels.Select(m => m.ModelId).ToHashSet();
        var missing = needed.Where(id => !known.Contains(id)).ToList();
        return new PreflightCheckResult
        {
            Name = "models.required_known",
            Passed = missing.Count == 0,
            Blocking = true,
            Detail = missing.Count == 0
                ? $"{needed.Count} modèle(s) requis tous décrits dans le manifeste"
                : $"modèle(s) requis absent(s) du manifeste : {string.Join(", ", missing)}",
        };
    }

    private static PreflightCheckResult CheckDependenciesKnown(InstallationPlan plan, RuntimeCallResult<RuntimeGateResult> gateResult)
    {
        if (!gateResult.IsSuccess)
        {
            return new PreflightCheckResult
            {
                Name = "dependencies.known", Passed = false, Blocking = true,
                Detail = "dépendances inconnues — Gate inexploitable",
            };
        }
        var unclassified = gateResult.Value!.MissingDependencies
            .Where(d => !plan.Dependencies.TryGetValue(d.Name, out var req) || req == DependencyRequirement.Unknown)
            .Select(d => d.Name)
            .ToList();
        return new PreflightCheckResult
        {
            Name = "dependencies.known",
            Passed = unclassified.Count == 0,
            Blocking = true,
            Detail = unclassified.Count == 0
                ? "toutes les dépendances manquantes sont classifiées"
                : $"dépendance(s) non classifiée(s) : {string.Join(", ", unclassified)}",
        };
    }
}
