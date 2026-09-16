using ApexSetup.Core.Contracts;
using ApexSetup.Core.RuntimeAdapter;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class RuntimeStatusServiceTests
{
    [Fact]
    public async Task Maps_a_passing_check_to_Running()
    {
        var service = new FakeSetupApplicationService
        {
            DoctorResult = FakeSetupApplicationService.DoctorSuccess(
                FakeSetupApplicationService.Check("docker.present", DoctorSeverity.Pass, "docker détecté")),
        };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        var docker = states.Single(s => s.Key == ComponentKey.Docker);
        Assert.Equal(ComponentStatus.Running, docker.Status);
        Assert.Equal("docker détecté", docker.Detail);
    }

    [Fact]
    public async Task Maps_a_failing_service_check_to_Error_never_a_silent_Running()
    {
        var service = new FakeSetupApplicationService
        {
            DoctorResult = FakeSetupApplicationService.DoctorSuccess(
                FakeSetupApplicationService.Check("service.postgres", DoctorSeverity.Fail, "connexion refusée")),
        };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        Assert.Equal(ComponentStatus.Error, states.Single(s => s.Key == ComponentKey.Postgres).Status);
    }

    [Fact]
    public async Task Maps_docker_present_skip_to_NotInstalled()
    {
        var service = new FakeSetupApplicationService
        {
            DoctorResult = FakeSetupApplicationService.DoctorSuccess(
                FakeSetupApplicationService.Check("docker.present", DoctorSeverity.Skip, "docker absent du PATH")),
        };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        Assert.Equal(ComponentStatus.NotInstalled, states.Single(s => s.Key == ComponentKey.Docker).Status);
    }

    [Fact]
    public async Task Maps_gpu_absence_to_Unavailable_never_an_error_for_a_legitimate_CPU_only_machine()
    {
        var service = new FakeSetupApplicationService
        {
            DoctorResult = FakeSetupApplicationService.DoctorSuccess(
                FakeSetupApplicationService.Check("gpu.present", DoctorSeverity.Skip, "aucun GPU détecté")),
        };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        Assert.Equal(ComponentStatus.Unavailable, states.Single(s => s.Key == ComponentKey.Gpu).Status);
    }

    [Fact]
    public async Task Reports_all_rows_as_Unknown_when_the_runtime_is_unreachable_never_fabricating_a_positive_status()
    {
        var service = new FakeSetupApplicationService
        {
            DoctorResult = RuntimeCallResult<RuntimeDoctorResult>.Failure(RuntimeCallOutcome.ProcessNotFound, "wsl.exe injoignable"),
        };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        Assert.All(states, s => Assert.Equal(ComponentStatus.Unknown, s.Status));
        Assert.All(states, s => Assert.Contains("wsl.exe injoignable", s.Detail));
    }

    [Fact]
    public async Task Reports_Unknown_for_a_check_absent_from_the_report_rather_than_a_fabricated_default()
    {
        var service = new FakeSetupApplicationService { DoctorResult = FakeSetupApplicationService.DoctorSuccess() };

        var states = await new RuntimeStatusService(service).GetComponentStatesAsync();

        Assert.All(states, s => Assert.Equal(ComponentStatus.Unknown, s.Status));
    }
}
