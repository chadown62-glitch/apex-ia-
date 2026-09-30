using System.Diagnostics;
using System.Text;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Implémentation réelle — <c>[SupportedOSPlatform("windows")]</c> non
/// appliqué délibérément (contrairement à <c>WindowsTaskDialogNotifier</c>
/// côté Launcher) : la construction/le lancement de <see cref="Process"/>
/// avec <c>UseShellExecute=false</c> et <c>ArgumentList</c> est une API
/// .NET portable, exercée par les tests de CE dépôt sous Linux (seul
/// <c>wsl.exe</c> lui-même n'existe que sous Windows — jamais supposé
/// présent ici, voir <see cref="ProcessRunResult.NotStarted"/> sur
/// <see cref="Win32Exception"/>).
/// </summary>
public sealed class RealProcessRunner : IProcessRunner
{
    public async Task<ProcessRunResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        using var process = BuildProcess(executable, arguments, redirect: true);
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();
        process.OutputDataReceived += (_, e) => { if (e.Data is not null) stdout.AppendLine(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) stderr.AppendLine(e.Data); };

        try
        {
            if (!process.Start())
            {
                return ProcessRunResult.NotStarted("Process.Start a retourné false");
            }
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            // Cas réel le plus fréquent ici : `wsl.exe` absent du PATH
            // (WSL2 non installé) — jamais une exception non gérée qui
            // remonterait jusqu'à l'UI.
            return ProcessRunResult.NotStarted(ex.Message);
        }

        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        cts.CancelAfter(timeout);
        bool timedOut = false;
        try
        {
            await process.WaitForExitAsync(cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            timedOut = true;
            TryKill(process);
        }

        if (timedOut)
        {
            return new ProcessRunResult { Started = true, TimedOut = true, Stdout = stdout.ToString(), Stderr = stderr.ToString() };
        }

        return new ProcessRunResult
        {
            Started = true, ExitCode = process.ExitCode, Stdout = stdout.ToString(), Stderr = stderr.ToString(),
        };
    }

    private static Process BuildProcess(string executable, IReadOnlyList<string> arguments, bool redirect)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            UseShellExecute = false,
            RedirectStandardOutput = redirect,
            RedirectStandardError = redirect,
            CreateNoWindow = true,
        };
        if (redirect)
        {
            // Jamais la page de code de la console (indéfinie pour une application graphique Windows) :
            // un chemin WSL non ASCII (ex. /home/josé) doit arriver intact. La sortie UTF-16 de
            // `wsl.exe -l -v` reste lisible par WslStatusParser (octets nuls retirés).
            startInfo.StandardOutputEncoding = Encoding.UTF8;
            startInfo.StandardErrorEncoding = Encoding.UTF8;
            // Observé sur Windows réel (WSL 2.7) : avec WSL_UTF8=1, wsl.exe écrit ses propres messages en
            // UTF-8 au lieu d'UTF-16LE (accents des messages traduits lisibles). Sans effet sur la sortie
            // des programmes Linux ; ignoré par les anciens wsl.exe (octets nuls alors retirés).
            startInfo.Environment["WSL_UTF8"] = "1";
        }
        foreach (var arg in arguments)
        {
            startInfo.ArgumentList.Add(arg);
        }
        return new Process { StartInfo = startInfo, EnableRaisingEvents = true };
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
        catch (InvalidOperationException)
        {
            // Déjà sorti entre le test et l'appel — pas une erreur.
        }
    }
}
