using ApexSetup.Core.Contracts;
using ApexSetup.Core.Preflight;

namespace ApexSetup.Application.Requests;

/// <summary>
/// Entrée commune à <c>check</c>/<c>plan</c>/<c>install</c> : tout ce qu'il
/// faut pour reconstruire un <c>InstallationPlan</c> déterministe (voir
/// <c>Core.Planning.InstallationPlanBuilder</c>) sans jamais toucher la
/// machine. <see cref="ProfileOverride"/> absent (null) signifie « utiliser
/// la RECOMMANDATION de <c>HardwareProfileEngine</c> » — jamais une
/// autorisation, Gate reste seul juge (voir §4 du plan Phase 3).
/// </summary>
public sealed record SetupPlanRequest
{
    public required string TargetApexVersion { get; init; }
    public required string Destination { get; init; }
    public required string ManifestPath { get; init; }
    public HardwareProfileTier? ProfileOverride { get; init; }
    public string MinimumApexVersion { get; init; } = PreflightChecker.DefaultMinimumApexVersion;
}

/// <summary>Entrée de <c>install</c> — le plan de base plus le répertoire de travail (téléchargements/fichiers temporaires, jamais la destination finale).</summary>
public sealed record SetupInstallRequest
{
    public required SetupPlanRequest Plan { get; init; }
    public required string WorkingDirectory { get; init; }
}
