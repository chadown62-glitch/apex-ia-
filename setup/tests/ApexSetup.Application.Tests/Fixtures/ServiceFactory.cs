using ApexSetup.Application.Hardware;
using ApexSetup.Core.Preflight;
using CoreRuntimeAdapter = ApexSetup.Core.RuntimeAdapter.RuntimeAdapter;

namespace ApexSetup.Application.Tests.Fixtures;

internal static class ServiceFactory
{
    public static SetupApplicationService Build(
        RoutingFakeRuntimeProcessRunner runner,
        IHardwareDetector? hardwareDetector = null,
        IEnvironmentProbe? environmentProbe = null,
        HttpClient? httpClient = null)
    {
        var runtimeAdapter = new CoreRuntimeAdapter(runner, "apex", Array.Empty<string>());
        return new SetupApplicationService(
            runtimeAdapter,
            environmentProbe ?? new RealEnvironmentProbe(),
            hardwareDetector ?? new FakeHardwareDetector(FakeHardwareDetector.Standard),
            httpClient ?? new HttpClient(),
            setupVersion: "test-1.0.0");
    }

    /// <summary>Amorce un runner avec des réponses version/doctor/gate toutes réussies et Gate autorisant la phase suivante — le cas "tout va bien" par défaut.</summary>
    public static RoutingFakeRuntimeProcessRunner HealthyRunner() => new RoutingFakeRuntimeProcessRunner()
        .WithResponse("version", SamplePayloads.Version)
        .WithResponse("doctor", SamplePayloads.DoctorPass)
        .WithResponse("gate", SamplePayloads.GatePassAuthorized);
}
