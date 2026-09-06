using ApexSetup.Launcher.ProcessLaunch;

namespace ApexSetup.Launcher.Tests.Fakes;

public sealed class FakeProcessLauncher : IProcessLauncher
{
    public ProcessLaunchResult RunResult { get; init; } = new() { Started = true, ExitCode = 0 };
    public ProcessLaunchResult CaptureResult { get; init; } = new() { Started = true, ExitCode = 0, Stdout = "" };
    public ProcessLaunchResult RelaunchResult { get; init; } = new() { Started = true, ExitCode = null };

    public (string Executable, IReadOnlyList<string> Arguments, string WorkingDirectory)? LastRun { get; private set; }
    public (string Executable, IReadOnlyList<string> Arguments, string WorkingDirectory)? LastCapture { get; private set; }
    public (string ExecutablePath, IReadOnlyList<string> Arguments)? LastRelaunch { get; private set; }

    public Task<ProcessLaunchResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken)
    {
        LastRun = (executable, arguments, workingDirectory);
        return Task.FromResult(RunResult);
    }

    public Task<ProcessLaunchResult> CaptureOutputAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken)
    {
        LastCapture = (executable, arguments, workingDirectory);
        return Task.FromResult(CaptureResult);
    }

    public ProcessLaunchResult RelaunchElevated(string executablePath, IReadOnlyList<string> arguments)
    {
        LastRelaunch = (executablePath, arguments);
        return RelaunchResult;
    }
}
