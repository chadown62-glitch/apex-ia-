using System.Diagnostics;

namespace ApexSetup.Release.Cli.Tests;

internal sealed record ReleaseCliRunResult
{
    public required int ExitCode { get; init; }
    public required string Stdout { get; init; }
    public required string Stderr { get; init; }
}

/// <summary>
/// Lance le VRAI binaire <c>apex-setup-release.dll</c> en sous-processus —
/// même discipline que <c>ApexSetup.Cli.Tests.CliProcess</c> : ces tests
/// valident le binaire réel (parsing, sortie, codes de sortie), jamais
/// seulement les classes en mémoire.
/// </summary>
internal static class ReleaseCliProcess
{
    private static readonly string DllPath = Path.Combine(AppContext.BaseDirectory, "apex-setup-release.dll");

    public static async Task<ReleaseCliRunResult> RunAsync(params string[] args)
    {
        if (!File.Exists(DllPath))
        {
            throw new InvalidOperationException(
                $"apex-setup-release.dll introuvable à côté des tests ({DllPath}) — la ProjectReference vers ApexSetup.Release.Cli a-t-elle bien construit l'exécutable ?");
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "dotnet",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add(DllPath);
        foreach (var arg in args)
        {
            startInfo.ArgumentList.Add(arg);
        }

        using var process = new Process { StartInfo = startInfo };
        process.Start();
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync().ConfigureAwait(false);

        return new ReleaseCliRunResult
        {
            ExitCode = process.ExitCode,
            Stdout = await stdoutTask.ConfigureAwait(false),
            Stderr = await stderrTask.ConfigureAwait(false),
        };
    }
}
