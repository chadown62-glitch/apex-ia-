using System.Diagnostics;

namespace ApexSetup.Cli.Tests;

internal sealed record CliRunResult
{
    public required int ExitCode { get; init; }
    public required string Stdout { get; init; }
    public required string Stderr { get; init; }
}

/// <summary>
/// Lance le VRAI binaire <c>apex-setup.dll</c> en sous-processus — jamais
/// via un shell (<c>ProcessStartInfo.ArgumentList</c>, un token par
/// argument, exactement la même discipline que
/// <c>Core.RuntimeAdapter.RuntimeProcessRunner</c>). Ces tests valident le
/// comportement du BINAIRE réel (parsing d'arguments, sérialisation JSON,
/// codes de sortie), jamais seulement la classe <c>Program</c> en mémoire.
/// </summary>
internal static class CliProcess
{
    private static readonly string DllPath = Path.Combine(AppContext.BaseDirectory, "apex-setup.dll");

    public static async Task<CliRunResult> RunAsync(params string[] args)
    {
        Assert(File.Exists(DllPath), $"apex-setup.dll introuvable à côté des tests ({DllPath}) — la ProjectReference vers ApexSetup.Cli a-t-elle bien construit l'exécutable ?");

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

        return new CliRunResult
        {
            ExitCode = process.ExitCode,
            Stdout = await stdoutTask.ConfigureAwait(false),
            Stderr = await stderrTask.ConfigureAwait(false),
        };
    }

    private static void Assert(bool condition, string message)
    {
        if (!condition)
        {
            throw new InvalidOperationException(message);
        }
    }
}
