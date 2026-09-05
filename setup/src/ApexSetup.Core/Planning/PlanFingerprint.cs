using System.Text;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Integrity;

namespace ApexSetup.Core.Planning;

/// <summary>
/// Empreinte déterministe d'un plan — utilisée par
/// <c>Pipeline.InstallationState</c> pour détecter si le plan a changé
/// entre une exécution interrompue et une tentative de reprise (TOCTOU au
/// niveau du plan lui-même, voir revue adversariale §5). Deux plans avec
/// les mêmes artefacts/hash/destination produisent la même empreinte,
/// indépendamment de l'ordre d'énumération.
/// </summary>
public static class PlanFingerprint
{
    public static string Compute(InstallationPlan plan)
    {
        var sb = new StringBuilder();
        sb.Append(plan.TargetApexVersion).Append('|').Append(plan.SelectedProfile).Append('|').Append(plan.Destination);
        foreach (var artifact in plan.RequiredArtifacts.OrderBy(a => a.ArtifactId, StringComparer.Ordinal))
        {
            sb.Append('|').Append(artifact.ArtifactId).Append(':').Append(artifact.Sha256);
        }
        return Sha256Verifier.ComputeSha256(Encoding.UTF8.GetBytes(sb.ToString()));
    }
}
