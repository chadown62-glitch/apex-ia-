using ApexSetup.Core.Contracts;

namespace ApexSetup.Core.Hardware;

/// <summary>
/// Instantané STATIQUE des 4 profils, construits exclusivement à partir
/// des candidats déjà déclarés dans <c>config.yaml</c> (racine du dépôt
/// DEV, section <c>models.roles.*.candidates</c> / <c>models.endpoints</c>)
/// au moment de l'écriture de ce code — AUCUN modèle inventé, chaque
/// <c>served_model</c>/VRAM ci-dessous est recopié tel quel.
///
/// Ceci n'est PAS un mécanisme de synchronisation live avec le Runtime —
/// Setup ne parse jamais <c>config.yaml</c> lui-même (ce serait un
/// couplage au dépôt DEV). Un futur manifeste de release (pipeline décrit
/// en conception, section N) régénérera ces valeurs à chaque build ; ce
/// catalogue reste la référence figée pour la Phase 1 tant que ce
/// mécanisme n'existe pas.
///
/// Traçabilité :
///   coder_main  : qwen3-coder:30b-a3b-instruct-q4_K_M (18.0 Go, ctx 65536)
///                 devstral-2:22b-q4_K_M (14.0 Go, ctx 32768)
///                 qwen3.6:27b-q4_K_M (17.0 Go, ctx 32768)
///   judge       : gemma4:12b-q4_K_M (8.0 Go, ctx 16384)
///                 gpt-oss:20b-q4_K_M (13.0 Go, ctx 32768)
///   fast        : qwen2.5-coder:7b-instruct (5.0 Go, ctx 32768, résident)
///   embed       : bge-m3:latest (0.6 Go, ctx 8192, résident)
/// Seuil Gate actuel (apex.config.schema.HardwareThresholds, defaults
/// 150/24/20/8) : min_vram_gb=20 — correspond exactement à
/// devstral-2 (14) + fast (5) + embed (0.6) ≈ 19.6 Go, arrondi. Le pool
/// local-gpu par défaut (RTX 4090, 24 Go) correspond à qwen3-coder:30b
/// (18) + fast (5) + embed (0.6) ≈ 23.6 Go.
/// </summary>
public static class HardwareProfileCatalog
{
    public const string ModelIdCoderReference = "qwen3-coder-30b-a3b-instruct";
    public const string ModelIdCoderStandard = "devstral-2-22b";
    public const string ModelIdCoderAlt = "qwen3-6-27b";
    public const string ModelIdJudgePrimary = "gemma4-12b";
    public const string ModelIdJudgeAlt = "gpt-oss-20b";
    public const string ModelIdFast = "qwen2-5-coder-7b-instruct";
    public const string ModelIdEmbed = "bge-m3-latest";

    public static readonly IReadOnlyDictionary<HardwareProfileTier, HardwareProfile> Profiles =
        new Dictionary<HardwareProfileTier, HardwareProfile>
        {
            [HardwareProfileTier.Minimal] = new HardwareProfile
            {
                Tier = HardwareProfileTier.Minimal,
                RecommendedRamGb = 16,
                RecommendedVramGb = 10,
                RecommendedCpuThreads = 8,
                RecommendedDiskGb = 150,
                // Aucun candidat coder_main déclaré ne tient sous ~12 Go une
                // fois fast+embed résidents comptés (constat documenté en
                // conception) — réutilisation honnête du modèle du rôle
                // "fast" comme assistant principal, pas un modèle inventé.
                PrimaryModelId = ModelIdFast,
                FallbackModelId = null,
                EmbeddingModelId = ModelIdEmbed,
                RecommendedContextTokens = 8192,
                RecommendedConcurrentAgents = 1,
                UsageDescription = "Découverte, tâches ponctuelles légères — capacité réduite, Gate mesurera probablement DEGRADED/WARN.",
            },
            [HardwareProfileTier.Standard] = new HardwareProfile
            {
                Tier = HardwareProfileTier.Standard,
                RecommendedRamGb = 24,
                RecommendedVramGb = 20, // seuil Gate actuel (HardwareThresholds default)
                RecommendedCpuThreads = 8,
                RecommendedDiskGb = 200,
                PrimaryModelId = ModelIdCoderStandard,
                FallbackModelId = ModelIdJudgePrimary,
                EmbeddingModelId = ModelIdEmbed,
                RecommendedContextTokens = 32768,
                RecommendedConcurrentAgents = 2,
                UsageDescription = "Usage individuel régulier — correspond au seuil min_vram_gb déjà déclaré dans config.yaml.",
            },
            [HardwareProfileTier.Performance] = new HardwareProfile
            {
                Tier = HardwareProfileTier.Performance,
                RecommendedRamGb = 32,
                RecommendedVramGb = 24, // pool local-gpu par défaut (RTX 4090)
                RecommendedCpuThreads = 12,
                RecommendedDiskGb = 300,
                PrimaryModelId = ModelIdCoderReference,
                FallbackModelId = ModelIdJudgeAlt,
                EmbeddingModelId = ModelIdEmbed,
                RecommendedContextTokens = 65536,
                RecommendedConcurrentAgents = 3,
                UsageDescription = "Poste de développement principal — déploiement de référence actuel de config.yaml.",
            },
            [HardwareProfileTier.HighEnd] = new HardwareProfile
            {
                Tier = HardwareProfileTier.HighEnd,
                RecommendedRamGb = 64,
                RecommendedVramGb = 40, // ex. A100-40G, déjà en commentaire dans config.yaml
                RecommendedCpuThreads = 16,
                RecommendedDiskGb = 500,
                PrimaryModelId = ModelIdCoderReference,
                FallbackModelId = ModelIdCoderAlt,
                EmbeddingModelId = ModelIdEmbed,
                RecommendedContextTokens = 65536,
                RecommendedConcurrentAgents = 4,
                UsageDescription = "Charge soutenue, missions concurrentes.",
            },
        };
}
