using System.ComponentModel;
using System.Diagnostics;

namespace ApexSetup.Launcher.ProcessLaunch;

/// <summary>
/// Implémentation réelle de <see cref="IProcessLauncher"/> — utilise
/// exclusivement <c>ProcessStartInfo.ArgumentList</c> (jamais
/// <c>Arguments</c> en chaîne, jamais <c>UseShellExecute=true</c> avec une
/// commande interprétée par un shell) pour lancer :
/// (1) <c>wsl.exe -- bash scripts/apex-launch.sh &lt;args&gt;</c> — exactement
///     le même mécanisme que <c>apex.bat</c> (jamais dupliqué : ce launcher
///     réutilise l'appel WSL existant, il n'en invente pas un second) ;
/// (2) une ré-exécution élevée de lui-même (<c>Verb="runas"</c>), la SEULE
///     situation où <c>UseShellExecute=true</c> est utilisé — nécessaire
///     pour que Windows affiche l'invite UAC, mais sans aucun argument
///     shell interprété (<c>ArgumentList</c> reste utilisé).
/// </summary>
public sealed class RealProcessLauncher : IProcessLauncher
{
    public async Task<ProcessLaunchResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
        };
        foreach (var arg in arguments)
        {
            startInfo.ArgumentList.Add(arg);
        }

        using var process = new Process { StartInfo = startInfo };
        try
        {
            process.Start();
        }
        catch (Exception ex) when (ex is Win32Exception or InvalidOperationException or FileNotFoundException)
        {
            return new ProcessLaunchResult { Started = false, ExitCode = null, FailureReason = ex.Message };
        }

        try
        {
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return new ProcessLaunchResult { Started = true, ExitCode = null, FailureReason = "annulé" };
        }

        return new ProcessLaunchResult { Started = true, ExitCode = process.ExitCode, FailureReason = null };
    }

    public async Task<ProcessLaunchResult> CaptureOutputAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var arg in arguments)
        {
            startInfo.ArgumentList.Add(arg);
        }

        using var process = new Process { StartInfo = startInfo };
        try
        {
            process.Start();
        }
        catch (Exception ex) when (ex is Win32Exception or InvalidOperationException or FileNotFoundException)
        {
            return new ProcessLaunchResult { Started = false, ExitCode = null, FailureReason = ex.Message };
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return new ProcessLaunchResult { Started = true, ExitCode = null, FailureReason = "annulé" };
        }

        var stdout = await stdoutTask.ConfigureAwait(false);
        var stderr = await stderrTask.ConfigureAwait(false);
        return new ProcessLaunchResult
        {
            Started = true, ExitCode = process.ExitCode,
            FailureReason = process.ExitCode == 0 ? null : stderr,
            Stdout = stdout,
        };
    }

    public ProcessLaunchResult RelaunchElevated(string executablePath, IReadOnlyList<string> arguments)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executablePath,
            UseShellExecute = true,
            Verb = "runas",
        };
        foreach (var arg in arguments)
        {
            startInfo.ArgumentList.Add(arg);
        }

        try
        {
            using var process = Process.Start(startInfo);
            return new ProcessLaunchResult { Started = process is not null, ExitCode = null, FailureReason = null };
        }
        catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
        {
            // ERROR_CANCELLED — l'utilisateur a refusé l'invite UAC. Ce
            // N'EST PAS une erreur interne : c'est un refus explicite,
            // signalé proprement au reste du launcher (voir Program.cs et
            // les 12 scénarios E2E, « UAC refusé -> message d'erreur
            // propre »), jamais une exception non gérée qui remonterait
            // comme une erreur interne générique.
            return new ProcessLaunchResult { Started = false, ExitCode = null, FailureReason = "uac_refused" };
        }
        catch (Win32Exception ex)
        {
            return new ProcessLaunchResult { Started = false, ExitCode = null, FailureReason = ex.Message };
        }
    }
}
