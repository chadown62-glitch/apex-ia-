using System.ComponentModel;
using System.Diagnostics;

namespace ApexSetup.Core.RuntimeAdapter;

/// <summary>
/// Implémentation réelle de <see cref="IRuntimeProcessRunner"/> —
/// <c>System.Diagnostics.Process</c> avec <c>ArgumentList</c> (jamais une
/// ligne de commande construite par concaténation de chaîne : chaque
/// argument est transmis comme un token distinct, aucune injection via un
/// argument n'est possible — même discipline que <c>apex.bat</c>/
/// <c>scripts/apex-launch.sh</c> côté Runtime).
/// </summary>
public sealed class RuntimeProcessRunner : IRuntimeProcessRunner
{
    public async Task<ProcessInvocationResult> RunAsync(
        string executable,
        IReadOnlyList<string> arguments,
        TimeSpan timeout,
        CancellationToken cancellationToken = default)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
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
        catch (Win32Exception ex)
        {
            // Exécutable introuvable ou non exécutable — jamais une exception non gérée.
            return new ProcessInvocationResult
            {
                ProcessStarted = false,
                TimedOut = false,
                StartFailureMessage = ex.Message,
            };
        }
        catch (System.IO.FileNotFoundException ex)
        {
            return new ProcessInvocationResult
            {
                ProcessStarted = false,
                TimedOut = false,
                StartFailureMessage = ex.Message,
            };
        }

        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(timeout);

        bool timedOut = false;
        try
        {
            await process.WaitForExitAsync(timeoutCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            timedOut = true;
            TryKill(process);
        }

        string stdout;
        string stderr;
        try
        {
            stdout = timedOut ? string.Empty : await stdoutTask.ConfigureAwait(false);
            stderr = timedOut ? string.Empty : await stderrTask.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            stdout = string.Empty;
            stderr = string.Empty;
        }

        return new ProcessInvocationResult
        {
            ProcessStarted = true,
            TimedOut = timedOut,
            ExitCode = timedOut ? null : process.ExitCode,
            Stdout = stdout,
            Stderr = stderr,
        };
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch
        {
            // Le processus a pu se terminer entre la vérification et l'appel — non fatal.
        }
    }
}
