using ApexSetup.Application.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Application.Tests;

public class ValidateCommandTests
{
    [Fact]
    public async Task ValidateAsync_never_reinstalls_only_queries_RuntimeAdapter()
    {
        var runner = ServiceFactory.HealthyRunner();
        var service = ServiceFactory.Build(runner);

        var response = await service.ValidateAsync();

        Assert.True(response.Success);
        Assert.True(response.RuntimeReachable);
        Assert.NotNull(response.Version);
        Assert.NotNull(response.Doctor);
        Assert.NotNull(response.Gate);
        // Seuls version/doctor/gate ont été invoqués — jamais une commande d'installation.
        Assert.All(runner.Invocations, args => Assert.Contains(args[0], new[] { "version", "doctor", "gate" }));
    }

    [Fact]
    public async Task ValidateAsync_reports_failure_when_doctor_or_gate_measure_a_real_fail_status()
    {
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorFail)
            .WithResponse("gate", SamplePayloads.GateFailBlocking);
        var service = ServiceFactory.Build(runner);

        var response = await service.ValidateAsync();

        Assert.False(response.Success);
        Assert.True(response.RuntimeReachable); // joignable, juste en échec mesuré — jamais confondu
        Assert.Equal("fail", response.Doctor!.Status);
        Assert.Equal("fail", response.Gate!.Status);
    }

    [Fact]
    public async Task ValidateAsync_reports_failure_when_gate_is_DEGRADED_but_not_phase2_authorized()
    {
        // REVUE ADVERSARIALE PHASE 3 §1/§10 : le Gate peut refuser la phase
        // suivante (`phase_2_authorized=false`) SANS que son `status` soit
        // `FAIL` (ex. `DEGRADED`) — voir docs/JSON_CONTRACTS.md côté DEV,
        // qui documente `DEGRADED` comme une valeur DISTINCTE de `FAIL`.
        // `check`/`install` décident déjà correctement sur
        // `Phase2Authorized` (jamais sur `Status`), mais `validate`
        // (avant correctif) ne regardait QUE `Status != Fail` — un Gate
        // DEGRADED-mais-non-autorisé était donc rapporté comme un succès,
        // en contradiction directe avec ce que `check`/`install`
        // décideraient pour la MÊME mesure Gate. Incohérence de contrat
        // entre commandes, jamais acceptable (§10 Profile vs Gate : Gate
        // reste l'autorité, JAMAIS masqué ou réinterprété différemment
        // d'une commande à l'autre).
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorPass)
            .WithResponse("gate", SamplePayloads.GateDegradedNotAuthorized);
        var service = ServiceFactory.Build(runner);

        var response = await service.ValidateAsync();

        Assert.True(response.RuntimeReachable);
        Assert.Equal("degraded", response.Gate!.Status);
        Assert.False(response.Gate!.Phase2Authorized);
        // Le Gate a refusé la phase suivante : `validate` DOIT le refléter,
        // exactement comme `check`/`install` le feraient pour cette même mesure.
        Assert.False(response.Success);
        Assert.Contains(response.Errors, e => e.Code == "validation_failed");
    }

    [Fact]
    public async Task ValidateAsync_reports_unreachable_distinctly_from_a_measured_fail()
    {
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("version").WithNotFound("doctor").WithNotFound("gate");
        var service = ServiceFactory.Build(runner);

        var response = await service.ValidateAsync();

        Assert.False(response.Success);
        Assert.False(response.RuntimeReachable);
        Assert.Null(response.Doctor);
        Assert.Null(response.Gate);
        Assert.Contains(response.Errors, e => e.Code == "version_unreachable");
    }
}
