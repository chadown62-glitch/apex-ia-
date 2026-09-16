using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Projette <c>apex doctor --json</c>/<c>apex gate --json</c> (déjà
/// entièrement mesurés côté Runtime, jamais recalculés ici) vers les
/// lignes Docker/PostgreSQL/Redis/MinIO/Ollama/GPU du tableau « System
/// Status » — voir <see cref="RuntimeStatusService"/> pour le mapping
/// exact check -> <see cref="ComponentStatus"/>.
/// </summary>
public interface IRuntimeStatusService
{
    Task<IReadOnlyList<ComponentState>> GetComponentStatesAsync(CancellationToken cancellationToken = default);
}
