using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using Xunit;

namespace ApexSetup.Core.Tests.Hardware;

public class HardwareProfileEngineTests
{
    [Theory]
    [InlineData(40, 64, 16, HardwareProfileTier.HighEnd)]
    [InlineData(24, 32, 12, HardwareProfileTier.Performance)]
    [InlineData(20, 24, 8, HardwareProfileTier.Standard)]
    [InlineData(8, 16, 4, HardwareProfileTier.Minimal)]
    public void SelectRecommendedProfile_picks_the_highest_satisfied_tier(
        double vramGb, double ramGb, int cpuThreads, HardwareProfileTier expected)
    {
        var detected = new DetectedHardware
        {
            VramTotalGb = vramGb, RamTotalGb = ramGb, CpuThreads = cpuThreads, DiskFreeGb = 500,
        };

        var profile = HardwareProfileEngine.SelectRecommendedProfile(detected);

        Assert.Equal(expected, profile.Tier);
    }

    [Fact]
    public void SelectRecommendedProfile_never_recommends_a_tier_the_hardware_cannot_satisfy()
    {
        // Machine sous le seuil VRAM du profil STANDARD mais RAM/CPU
        // suffisants pour PERFORMANCE : ne doit jamais recommander
        // PERFORMANCE sur la seule base de la RAM/CPU.
        var detected = new DetectedHardware { VramTotalGb = 6, RamTotalGb = 64, CpuThreads = 16, DiskFreeGb = 500 };

        var profile = HardwareProfileEngine.SelectRecommendedProfile(detected);

        Assert.Equal(HardwareProfileTier.Minimal, profile.Tier);
    }

    [Fact]
    public void SelectRecommendedProfile_falls_back_to_minimal_for_hardware_below_every_tier()
    {
        var detected = new DetectedHardware { VramTotalGb = 2, RamTotalGb = 4, CpuThreads = 2, DiskFreeGb = 50 };

        var profile = HardwareProfileEngine.SelectRecommendedProfile(detected);

        Assert.Equal(HardwareProfileTier.Minimal, profile.Tier);
    }

    [Fact]
    public void All_catalog_entries_only_reference_models_already_declared_in_config_yaml()
    {
        // Garde de non-régression textuelle : les identifiants utilisés
        // ici doivent rester ceux tracés dans HardwareProfileCatalog vers
        // config.yaml — jamais un nouvel identifiant introduit ailleurs
        // sans la même traçabilité.
        var known = new[]
        {
            HardwareProfileCatalog.ModelIdCoderReference, HardwareProfileCatalog.ModelIdCoderStandard,
            HardwareProfileCatalog.ModelIdCoderAlt, HardwareProfileCatalog.ModelIdJudgePrimary,
            HardwareProfileCatalog.ModelIdJudgeAlt, HardwareProfileCatalog.ModelIdFast,
            HardwareProfileCatalog.ModelIdEmbed,
        };

        foreach (var profile in HardwareProfileCatalog.Profiles.Values)
        {
            Assert.Contains(profile.PrimaryModelId, known);
            if (profile.FallbackModelId is not null)
            {
                Assert.Contains(profile.FallbackModelId, known);
            }
            Assert.Contains(profile.EmbeddingModelId, known);
        }
    }

    [Fact]
    public void Standard_profile_vram_threshold_matches_the_gate_default_hardware_threshold()
    {
        // Ancre l'affirmation documentée : min_vram_gb=20 (défaut Gate,
        // apex.config.schema.HardwareThresholds) == seuil STANDARD ici.
        Assert.Equal(20, HardwareProfileCatalog.Profiles[HardwareProfileTier.Standard].RecommendedVramGb);
    }
}
