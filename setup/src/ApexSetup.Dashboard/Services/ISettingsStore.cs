using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Mission §16 — persistance des Paramètres, jamais de secret dedans (voir <see cref="DashboardSettings"/>).</summary>
public interface ISettingsStore
{
    Task<DashboardSettings> LoadAsync(CancellationToken cancellationToken = default);

    Task SaveAsync(DashboardSettings settings, CancellationToken cancellationToken = default);
}
