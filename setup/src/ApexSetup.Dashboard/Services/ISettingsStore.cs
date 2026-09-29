using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Paramètres chargés, et la raison si le fichier existant n'a pas pu être utilisé (valeurs par défaut appliquées).</summary>
public sealed record SettingsLoad(DashboardSettings Settings, string? Problem);

/// <summary>Persistance des paramètres — jamais de secret dedans (voir <see cref="DashboardSettings"/>).</summary>
public interface ISettingsStore
{
    Task<SettingsLoad> LoadAsync(CancellationToken cancellationToken = default);

    Task SaveAsync(DashboardSettings settings, CancellationToken cancellationToken = default);
}
