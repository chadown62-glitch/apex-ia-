using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Détection WSL — mission §7 : « WSL installé, distribution disponible,
/// distribution active, projet APEX accessible ». Rien de ceci n'existait
/// avant (confirmé par audit : <c>ApexSetup.Core.Preflight</c> ne sonde que
/// le disque/les permissions pour l'INSTALLATION de Setup, jamais l'état de
/// WSL lui-même) — code neuf légitime, pas une duplication.
/// </summary>
public interface IWslProbe
{
    /// <param name="preferredDistro">Distribution demandée par les Paramètres (§7 : jamais codée en dur) — <c>null</c>/vide = distribution par défaut du système.</param>
    Task<ComponentState> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default);

    /// <summary>Vérifie EMPIRIQUEMENT (jamais supposé) qu'un chemin existe à l'intérieur de la distribution WSL désignée — utilisé par les Paramètres avant d'accepter un chemin de projet.</summary>
    Task<bool> PathExistsAsync(string? distro, string wslPath, CancellationToken cancellationToken = default);
}
