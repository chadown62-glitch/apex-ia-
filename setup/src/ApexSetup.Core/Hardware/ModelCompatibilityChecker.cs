using ApexSetup.Core.Contracts;

namespace ApexSetup.Core.Hardware;

public enum ModelCompatibilityOutcome
{
    Compatible,
    UnsupportedForProfile,
    InsufficientVram,
    InsufficientRam,
}

public sealed record ModelCompatibilityResult
{
    public required ModelCompatibilityOutcome Outcome { get; init; }
    public bool IsCompatible => Outcome == ModelCompatibilityOutcome.Compatible;
}

/// <summary>
/// Vérifie qu'une entrée de manifeste modèle est réellement utilisable
/// pour un profil et un matériel donnés — sélection déterministe (règle
/// verrouillée en conception : « Le Setup ne doit jamais simplement
/// télécharger le plus gros modèle possible »). Ne remplace jamais le
/// Gate : c'est une vérification de PLAN, pas une autorisation finale.
/// </summary>
public static class ModelCompatibilityChecker
{
    public static ModelCompatibilityResult Check(ModelManifestEntry model, HardwareProfileTier profile, DetectedHardware detected)
    {
        if (!model.SupportedProfiles.Contains(profile))
        {
            return new ModelCompatibilityResult { Outcome = ModelCompatibilityOutcome.UnsupportedForProfile };
        }
        if (detected.VramTotalGb < model.RequiredVramGb)
        {
            return new ModelCompatibilityResult { Outcome = ModelCompatibilityOutcome.InsufficientVram };
        }
        if (detected.RamTotalGb < model.RequiredRamGb)
        {
            return new ModelCompatibilityResult { Outcome = ModelCompatibilityOutcome.InsufficientRam };
        }
        return new ModelCompatibilityResult { Outcome = ModelCompatibilityOutcome.Compatible };
    }
}
