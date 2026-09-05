using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using Xunit;

namespace ApexSetup.Core.Tests.Hardware;

public class ModelCompatibilityCheckerTests
{
    private static ModelManifestEntry MakeModel(
        double vram = 14, double ram = 16, params HardwareProfileTier[] profiles) => new()
    {
        ModelId = "devstral-2-22b",
        Version = "22b",
        Quantization = "q4_k_m",
        ServedModel = "devstral-2:22b-q4_K_M",
        SizeBytes = 15_000_000_000,
        Sha256 = new string('a', 64),
        RequiredVramGb = vram,
        RequiredRamGb = ram,
        MaxContext = 32768,
        CapabilityClass = "coder",
        SupportedProfiles = profiles,
        Source = "ollama-registry",
    };

    [Fact]
    public void Check_returns_Compatible_when_profile_supported_and_hardware_sufficient()
    {
        var model = MakeModel(profiles: [HardwareProfileTier.Standard]);
        var hw = new DetectedHardware { VramTotalGb = 20, RamTotalGb = 24, CpuThreads = 8, DiskFreeGb = 200 };

        var result = ModelCompatibilityChecker.Check(model, HardwareProfileTier.Standard, hw);

        Assert.True(result.IsCompatible);
    }

    [Fact]
    public void Check_returns_UnsupportedForProfile_when_the_manifest_does_not_list_the_profile()
    {
        var model = MakeModel(profiles: [HardwareProfileTier.HighEnd]);
        var hw = new DetectedHardware { VramTotalGb = 40, RamTotalGb = 64, CpuThreads = 16, DiskFreeGb = 500 };

        var result = ModelCompatibilityChecker.Check(model, HardwareProfileTier.Minimal, hw);

        Assert.Equal(ModelCompatibilityOutcome.UnsupportedForProfile, result.Outcome);
    }

    [Fact]
    public void Check_returns_InsufficientVram_when_the_detected_hardware_falls_short()
    {
        var model = MakeModel(vram: 14, profiles: [HardwareProfileTier.Standard]);
        var hw = new DetectedHardware { VramTotalGb = 8, RamTotalGb = 24, CpuThreads = 8, DiskFreeGb = 200 };

        var result = ModelCompatibilityChecker.Check(model, HardwareProfileTier.Standard, hw);

        Assert.Equal(ModelCompatibilityOutcome.InsufficientVram, result.Outcome);
    }

    [Fact]
    public void Check_returns_InsufficientRam_when_vram_is_fine_but_ram_falls_short()
    {
        var model = MakeModel(vram: 14, ram: 16, profiles: [HardwareProfileTier.Standard]);
        var hw = new DetectedHardware { VramTotalGb = 20, RamTotalGb = 8, CpuThreads = 8, DiskFreeGb = 200 };

        var result = ModelCompatibilityChecker.Check(model, HardwareProfileTier.Standard, hw);

        Assert.Equal(ModelCompatibilityOutcome.InsufficientRam, result.Outcome);
    }
}
