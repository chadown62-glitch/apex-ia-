using ApexSetup.Application.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Application.Tests;

/// <summary>
/// <see cref="ISetupApplicationService.GetDoctorDetailAsync"/>/<see cref="ISetupApplicationService.GetGateDetailAsync"/>
/// sont un pur passe-plat vers <c>RuntimeAdapter.GetDoctorAsync</c>/<c>GetGateAsync</c>
/// (déjà appelés par <see cref="ISetupApplicationService.ValidateAsync"/> pour
/// construire son résumé tronqué) — ces tests prouvent qu'aucune donnée
/// n'est perdue ni réinterprétée en chemin, jamais une seconde logique de
/// diagnostic.
/// </summary>
public class RuntimeDetailCommandTests
{
    [Fact]
    public async Task GetDoctorDetailAsync_returns_the_full_RuntimeDoctorResult_never_truncated()
    {
        var runner = ServiceFactory.HealthyRunner();
        var service = ServiceFactory.Build(runner);

        var result = await service.GetDoctorDetailAsync();

        Assert.True(result.IsSuccess);
        Assert.Equal(ApexSetup.Core.Contracts.DoctorSeverity.Pass, result.Value!.Status);
        // Le même appel que ValidateAsync ferait — jamais une seconde invocation Runtime distincte.
        Assert.Contains(runner.Invocations, args => args[0] == "doctor");
    }

    [Fact]
    public async Task GetGateDetailAsync_returns_the_full_RuntimeGateResult_including_checks()
    {
        var runner = ServiceFactory.HealthyRunner();
        var service = ServiceFactory.Build(runner);

        var result = await service.GetGateDetailAsync();

        Assert.True(result.IsSuccess);
        Assert.True(result.Value!.Phase2Authorized);
        Assert.Contains(runner.Invocations, args => args[0] == "gate");
    }

    [Fact]
    public async Task GetDoctorDetailAsync_surfaces_an_unreachable_runtime_honestly()
    {
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("doctor");
        var service = ServiceFactory.Build(runner);

        var result = await service.GetDoctorDetailAsync();

        Assert.False(result.IsSuccess);
        Assert.Null(result.Value);
    }
}
