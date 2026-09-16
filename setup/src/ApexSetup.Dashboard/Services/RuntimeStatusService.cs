using ApexSetup.Application;
using ApexSetup.Core.Contracts;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Consomme <see cref="ISetupApplicationService.GetDoctorDetailAsync"/> —
/// jamais <c>ApexSetup.Core</c> directement (frontière GUI/Core respectée,
/// voir <c>ApexSetup.Dashboard.csproj</c>). Chaque ligne du tableau est
/// dérivée d'un check Doctor réel et NOMMÉ, identifié par audit du dépôt
/// Runtime (<c>apex/doctor/checks/*.py</c>) — jamais une invention : les
/// noms exacts (<c>docker.present</c>, <c>service.postgres</c>, etc.) sont
/// ceux réellement émis par <c>DoctorReport.to_dict()["results"][i]["name"]</c>.
/// </summary>
public sealed class RuntimeStatusService(ISetupApplicationService service) : IRuntimeStatusService
{
    private static readonly (ComponentKey Key, string Label, string CheckName)[] DoctorRows =
    [
        (ComponentKey.Docker, "Docker", "docker.present"),
        (ComponentKey.Postgres, "PostgreSQL", "service.postgres"),
        (ComponentKey.Redis, "Redis", "service.redis"),
        (ComponentKey.MinIO, "MinIO", "service.minio"),
        (ComponentKey.Ollama, "Ollama", "service.ollama"),
        (ComponentKey.Gpu, "GPU", "gpu.present"),
    ];

    public async Task<IReadOnlyList<ComponentState>> GetComponentStatesAsync(CancellationToken cancellationToken = default)
    {
        var doctorResult = await service.GetDoctorDetailAsync(cancellationToken).ConfigureAwait(false);
        if (!doctorResult.IsSuccess || doctorResult.Value is null)
        {
            var detail = doctorResult.ErrorDetail ?? $"apex doctor --json inexploitable ({doctorResult.Outcome})";
            return DoctorRows.Select(row => ComponentState.Unknown(row.Key, row.Label, detail)).ToList();
        }

        var byName = doctorResult.Value.Results.ToLookup(r => r.Name);
        return DoctorRows.Select(row => MapRow(row.Key, row.Label, row.CheckName, byName[row.CheckName].FirstOrDefault())).ToList();
    }

    /// <summary>
    /// Mapping DoctorSeverity -> ComponentStatus. Le vocabulaire Doctor
    /// (Pass/Warn/Fail/Skip) n'a pas de correspondance 1-pour-1 avec les
    /// 7 valeurs exigées par la mission — décisions explicites, jamais
    /// arbitraires :
    /// - Pass -> Running (mesure positive réelle).
    /// - Fail -> Error (mesure négative réelle).
    /// - Warn -> Error : ce dépôt n'utilise WARN que pour un problème RÉEL
    ///   non bloquant (ex. `service.postgres.auth` sur une cause de
    ///   connexion autre que le mot de passe) — jamais un simple
    ///   avertissement cosmétique. Le présenter comme "Running" masquerait
    ///   un problème mesuré ; la mission n'offre pas de 8e état "Warning".
    /// - Skip sur `docker.present`/`gpu.present` spécifiquement -> le check
    ///   lui-même documente son absence comme "non installé"/"non
    ///   applicable" plutôt qu'un échec — NotInstalled pour Docker (rien
    ///   ne peut fonctionner sans lui), Unavailable pour GPU (une machine
    ///   CPU-only est un état légitime, jamais une erreur).
    /// - Skip sur les autres (`service.*`) -> Unavailable (check non exécuté,
    ///   raison dans `Message`/`Remediation`), jamais interprété comme Stopped
    ///   (on ne sait PAS si le service tourne, seulement que la mesure n'a pas eu lieu).
    /// - Check absent du rapport (jamais émis) -> Unknown, jamais une valeur positive par défaut.
    /// </summary>
    private static ComponentState MapRow(ComponentKey key, string label, string checkName, DoctorCheckResult? check)
    {
        if (check is null)
        {
            return ComponentState.Unknown(key, label, $"aucun check « {checkName} » dans le rapport Doctor");
        }

        var status = check.Severity switch
        {
            DoctorSeverity.Pass => ComponentStatus.Running,
            DoctorSeverity.Fail => ComponentStatus.Error,
            DoctorSeverity.Warn => ComponentStatus.Error,
            DoctorSeverity.Skip when key is ComponentKey.Docker => ComponentStatus.NotInstalled,
            DoctorSeverity.Skip when key is ComponentKey.Gpu => ComponentStatus.Unavailable,
            DoctorSeverity.Skip => ComponentStatus.Unavailable,
            _ => ComponentStatus.Unknown,
        };

        return new ComponentState(key, label, status, check.Message);
    }
}
