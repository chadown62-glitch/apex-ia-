using System.Diagnostics;
using Xunit;

namespace ApexSetup.Launcher.Tests;

/// <summary>
/// Scénarios E2E (§J de la mission N+15) exécutés contre le VRAI binaire
/// compilé (<c>ApexLauncher.dll</c>, build framework-dépendante — même
/// discipline que <c>ApexSetup.Cli.Tests.CliProcess</c>), jamais seulement
/// <see cref="LauncherStateEngine"/> en mémoire.
///
/// Limite assumée de cet environnement (même précédent que
/// setup/docs/GUI_ARCHITECTURE.md pour Avalonia) : ce bac à sable Linux ne
/// peut pas vérifier une VRAIE invite UAC, un VRAI double-clic depuis
/// l'Explorateur Windows, ni une élévation Windows réelle — ces scénarios
/// (UAC accepté/refusé, double-clic, mode Admin) restent non vérifiés
/// empiriquement ici et sont documentés comme tels dans le rapport final.
/// Les scénarios ci-dessous sont ceux vérifiables identiquement sur
/// Linux/WSL2 et Windows : détection d'état (aucune installation), et
/// chemin de lancement contenant des espaces.
/// </summary>
public class LauncherEndToEndTests
{
    private static readonly string DllPath = Path.Combine(AppContext.BaseDirectory, "ApexLauncher.dll");

    private sealed record RunResult(int ExitCode, string Stdout, string Stderr);

    private static async Task<RunResult> RunLauncherAsync(string workingDirectory)
    {
        Assert.True(File.Exists(DllPath), $"ApexLauncher.dll introuvable à côté des tests ({DllPath})");

        var startInfo = new ProcessStartInfo
        {
            FileName = "dotnet",
            WorkingDirectory = workingDirectory,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        // Copie du binaire dans workingDirectory est nécessaire : le
        // launcher résout sa racine d'installation via AppContext.BaseDirectory
        // (l'emplacement de SES PROPRES fichiers), pas le répertoire courant —
        // exactement le même principe que apex.bat (%~dp0). Voir la fixture
        // appelante pour la copie réelle.
        startInfo.ArgumentList.Add(Path.Combine(workingDirectory, "ApexLauncher.dll"));

        using var process = new Process { StartInfo = startInfo };
        process.Start();
        var stdoutTask = process.StandardOutput.ReadToEndAsync();
        var stderrTask = process.StandardError.ReadToEndAsync();
        var completed = await Task.Run(() => process.WaitForExit(20_000));
        if (!completed)
        {
            process.Kill(entireProcessTree: true);
            throw new TimeoutException("le launcher ne s'est pas terminé dans le délai imparti (20s)");
        }

        return new RunResult(process.ExitCode, await stdoutTask, await stderrTask);
    }

    private static void CopyPublishedLauncherInto(string targetDirectory)
    {
        Directory.CreateDirectory(targetDirectory);
        foreach (var file in Directory.GetFiles(AppContext.BaseDirectory))
        {
            var name = Path.GetFileName(file);
            // Copie uniquement ce qui appartient au launcher lui-même —
            // jamais les artefacts du harness de test (xunit, etc.), pour
            // isoler ce que "AppContext.BaseDirectory" représentera dans le
            // process enfant : un dossier de release plausible, jamais
            // pollué par des DLL de test qui n'existeraient pas dans un
            // vrai paquet.
            if (name.StartsWith("ApexLauncher", StringComparison.Ordinal)
                || name.StartsWith("ApexSetup.Application", StringComparison.Ordinal)
                || name.StartsWith("ApexSetup.Core", StringComparison.Ordinal))
            {
                File.Copy(file, Path.Combine(targetDirectory, name), overwrite: true);
            }
        }
    }

    [Fact]
    public async Task Clean_directory_with_no_prior_installation_yields_NeedsSetup_deterministically()
    {
        var root = Directory.CreateTempSubdirectory("apex-launcher-e2e-").FullName;
        try
        {
            CopyPublishedLauncherInto(root);
            var result = await RunLauncherAsync(root);

            Assert.Equal((int)LauncherExitCode.NeedsSetup, result.ExitCode);
            Assert.Contains("NeedsSetup", result.Stderr);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task Launch_directory_containing_spaces_resolves_and_behaves_identically()
    {
        // §H Packaging / §J E2E : "chemin Windows avec espaces" — vérifiable
        // identiquement sur ce système de fichiers (la contrainte est
        // générique, pas spécifique à NTFS).
        var root = Directory.CreateTempSubdirectory("apex launcher e2e with spaces ").FullName;
        try
        {
            CopyPublishedLauncherInto(root);
            var result = await RunLauncherAsync(root);

            Assert.Equal((int)LauncherExitCode.NeedsSetup, result.ExitCode);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task Two_simultaneous_launches_from_a_clean_directory_are_both_deterministic()
    {
        // §J scénario "deux lancements simultanés" : StatusAsync est une
        // lecture pure (aucun effet de bord durable, voir sa docstring) —
        // deux lancements concurrents sur un répertoire sans installation
        // doivent donc converger vers LE MÊME résultat déterministe, jamais
        // une course qui produirait un résultat différent selon l'ordre.
        var root = Directory.CreateTempSubdirectory("apex-launcher-e2e-concurrent-").FullName;
        try
        {
            CopyPublishedLauncherInto(root);
            var results = await Task.WhenAll(RunLauncherAsync(root), RunLauncherAsync(root));

            Assert.All(results, r => Assert.Equal((int)LauncherExitCode.NeedsSetup, r.ExitCode));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
