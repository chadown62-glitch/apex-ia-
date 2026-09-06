using ApexSetup.Application.Contracts;
using ApexSetup.Core.Contracts;

namespace ApexSetup.Gui.Services;

/// <summary>
/// État partagé de l'assistant — traverse les 9 écrans. Volontairement un
/// simple porte-données (aucune logique métier ici : chaque écran appelle
/// <c>ISetupApplicationService</c> lui-même et y dépose son résultat).
/// <c>HardwareProfileTier</c> est le SEUL type <c>ApexSetup.Core</c>
/// référencé ici — un type de données pur (énumération à 4 valeurs), déjà
/// exposé publiquement par <c>ISetupApplicationService</c>
/// (<c>SetupPlanRequest.ProfileOverride</c>) : le retenir tel quel évite
/// de dupliquer un enum équivalent rien que pour le GUI ; cela ne crée
/// aucune dépendance à un détail d'implémentation Core (stages, pipeline,
/// PathSafety, etc. restent inconnus du GUI).
/// </summary>
public sealed class SetupSession
{
    public string ApexVersion { get; set; } = "0.1.2";
    public string Destination { get; set; } = "";
    public string ManifestPath { get; set; } = "";
    public HardwareProfileTier? ProfileOverride { get; set; }
    public string WorkingDirectory { get; set; } = "";

    public DetectResponse? LastDetect { get; set; }
    public CheckResponse? LastCheck { get; set; }
    public PlanResponse? LastPlan { get; set; }
    public InstallResponse? LastInstall { get; set; }
    public RecoverResponse? LastRecover { get; set; }
    public ValidateResponse? LastValidate { get; set; }
    public StatusResponse? LastStatus { get; set; }
}
