using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Mission §11 : ne jamais ouvrir le navigateur avant qu'une requête HTTP réelle confirme que le Web Panel répond.</summary>
public interface IWebPanelChecker
{
    Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default);
}
