using System.Runtime.InteropServices;

namespace ApexSetup.Application.Hardware;

/// <summary>
/// Détection RÉELLE, mais délibérément LIMITÉE aux faits que .NET expose
/// nativement et de façon multiplateforme, sans exécuter aucun outil
/// externe (jamais <c>wmic</c>/<c>nvidia-smi</c>/<c>lspci</c> — ce serait
/// construire une dépendance à un sous-processus supplémentaire, hors
/// périmètre Phase 3, et une source d'erreurs de parsing de sortie texte
/// qui contredirait directement §2 « jamais demander au GUI de parser du
/// texte humain »).
///
/// GPU/VRAM ne sont PAS détectés ici : une mesure fiable exigerait WMI
/// (Windows) ou l'énumération PCI (Linux), tous deux hors périmètre —
/// voir <c>Core.Contracts.DetectedHardware</c>, déjà documenté comme
/// « natif Windows, hors périmètre de cette phase » depuis la Phase 1.
/// Exposer <c>null</c> plutôt qu'une fausse valeur — même discipline que
/// <c>RuntimeHardwareFacts</c> côté Runtime (tous les champs matériels y
/// sont déjà nullables pour la même raison).
///
/// RAM totale : <c>GC.GetGCMemoryInfo().TotalAvailableMemoryBytes</c> —
/// approximation documentée (peut refléter une limite de conteneur/cgroup
/// plutôt que la RAM physique totale de la machine hôte), jamais présentée
/// comme une mesure Gate.
/// </summary>
public sealed class RealHardwareDetector : IHardwareDetector
{
    public DetectedMachine Detect()
    {
        var memoryInfo = GC.GetGCMemoryInfo();
        var ramBytes = memoryInfo.TotalAvailableMemoryBytes;

        return new DetectedMachine
        {
            Os = RuntimeInformation.OSDescription,
            Architecture = RuntimeInformation.OSArchitecture.ToString(),
            RamTotalGb = Math.Round(ramBytes / 1_000_000_000.0, 2),
            CpuThreads = System.Environment.ProcessorCount,
            Gpu = null,
            VramTotalGb = null,
            Environment = RuntimeInformation.OSDescription,
        };
    }
}
