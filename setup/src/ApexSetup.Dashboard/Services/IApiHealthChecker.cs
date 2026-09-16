using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Mission §10 : « API APEX » — critique. Le token est lu directement
/// depuis <c>.apex/api-token</c> côté WSL (voir <c>apex/api/auth.py</c>,
/// audit N+30) : <c>/health</c> exige désormais une authentification
/// Bearer comme toute autre route — jamais une exemption "route anodine".
/// Ce jeton n'est JAMAIS écrit dans les paramètres persistés du Dashboard
/// (mission §16 : ne jamais exposer de secret).
/// </summary>
public interface IApiHealthChecker
{
    Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default);
}
