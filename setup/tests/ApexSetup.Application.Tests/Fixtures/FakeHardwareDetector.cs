using ApexSetup.Application.Hardware;

namespace ApexSetup.Application.Tests.Fixtures;

internal sealed class FakeHardwareDetector(DetectedMachine machine) : IHardwareDetector
{
    public static readonly DetectedMachine Standard = new()
    {
        Os = "TestOS", Architecture = "X64", RamTotalGb = 32, CpuThreads = 8, Gpu = null, VramTotalGb = null, Environment = "TestOS",
    };

    public DetectedMachine Detect() => machine;
}
