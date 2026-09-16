using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Dashboard.Tests.Fixtures;

/// <summary>
/// Double minimal — seules les méthodes RÉELLEMENT utilisées par
/// ApexSetup.Dashboard (<see cref="ISetupApplicationService.GetDoctorDetailAsync"/>/
/// <see cref="ISetupApplicationService.GetGateDetailAsync"/>) ont un
/// comportement configurable ; les autres lèvent — jamais silencieusement
/// utilisées par erreur.
/// </summary>
public sealed class FakeSetupApplicationService : ISetupApplicationService
{
    public RuntimeCallResult<RuntimeDoctorResult>? DoctorResult { get; set; }
    public RuntimeCallResult<RuntimeGateResult>? GateResult { get; set; }

    public Task<RuntimeCallResult<RuntimeDoctorResult>> GetDoctorDetailAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(DoctorResult ?? throw new InvalidOperationException("DoctorResult non configuré"));

    public Task<RuntimeCallResult<RuntimeGateResult>> GetGateDetailAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(GateResult ?? throw new InvalidOperationException("GateResult non configuré"));

    public Task<VersionResponse> GetVersionAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<DetectResponse> DetectAsync(string destinationForDiskProbe, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<CheckResponse> CheckAsync(SetupPlanRequest request, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PlanResponse> PlanAsync(SetupPlanRequest request, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<InstallResponse> InstallAsync(SetupInstallRequest request, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<RecoverResponse> RecoverAsync(string destination, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<ValidateResponse> ValidateAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<StatusResponse> StatusAsync(string destination, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public static DoctorCheckResult Check(string name, DoctorSeverity severity, string message = "") => new()
    {
        Name = name, Category = "test", Severity = severity, Message = message, Remediation = "",
        Facts = new Dictionary<string, object?>(), DurationMs = 1.0,
    };

    public static RuntimeCallResult<RuntimeDoctorResult> DoctorSuccess(params DoctorCheckResult[] results) =>
        RuntimeCallResult<RuntimeDoctorResult>.Success(
            new RuntimeDoctorResult
            {
                Status = results.Any(r => r.Severity == DoctorSeverity.Fail) ? DoctorSeverity.Fail : DoctorSeverity.Pass,
                Counts = new Dictionary<string, int>(), DurationMs = 1.0, Results = results,
            },
            stdout: "{}", exitCode: 0);
}
