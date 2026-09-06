using ApexSetup.Application;
using ApexSetup.Application.Hardware;
using ApexSetup.Core.Preflight;
using ApexSetup.Core.RuntimeAdapter;
using CoreRuntimeAdapter = ApexSetup.Core.RuntimeAdapter.RuntimeAdapter;

namespace ApexSetup.Gui.Tests.Fixtures;

internal static class TestServiceFactory
{
    public static SetupApplicationService Build(IRuntimeProcessRunner runner) =>
        new(new CoreRuntimeAdapter(runner, "apex", []), new RealEnvironmentProbe(), new RealHardwareDetector(), new HttpClient(), "gui-test-1.0.0");

    public static RoutingFakeRuntimeProcessRunner HealthyRunner() => new RoutingFakeRuntimeProcessRunner()
        .WithResponse("version", SamplePayloads.Version)
        .WithResponse("doctor", SamplePayloads.DoctorPass)
        .WithResponse("gate", SamplePayloads.GatePassAuthorized);
}
