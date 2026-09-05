using ApexSetup.Application.Hardware;
using ApexSetup.Application.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Application.Tests;

public class DetectCommandTests
{
    [Fact]
    public async Task DetectAsync_reports_the_hardware_facts_and_a_recommendation_never_an_authorization()
    {
        var machine = new DetectedMachine
        {
            Os = "TestOS", Architecture = "X64", RamTotalGb = 64, CpuThreads = 16, Gpu = null, VramTotalGb = null, Environment = "TestOS",
        };
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner(), hardwareDetector: new FakeHardwareDetector(machine));

        var response = await service.DetectAsync(Path.GetTempPath());

        Assert.True(response.Success);
        Assert.Equal(64, response.RamTotalGb);
        Assert.Equal(16, response.CpuThreads);
        Assert.Null(response.Gpu);
        Assert.Null(response.VramTotalGb);
        Assert.NotEmpty(response.RecommendedProfile);
        Assert.Contains("recommandation", response.ProfileNote, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task DetectAsync_has_no_side_effect_on_disk()
    {
        var probeDir = Directory.CreateTempSubdirectory("apex-setup-detect-").FullName;
        try
        {
            var before = Directory.GetFileSystemEntries(probeDir);
            var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

            await service.DetectAsync(probeDir);

            Assert.Equal(before, Directory.GetFileSystemEntries(probeDir));
        }
        finally
        {
            Directory.Delete(probeDir, recursive: true);
        }
    }
}
