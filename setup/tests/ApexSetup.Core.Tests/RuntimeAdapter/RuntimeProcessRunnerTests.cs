using System.Runtime.InteropServices;
using ApexSetup.Core.RuntimeAdapter;
using Xunit;

namespace ApexSetup.Core.Tests.RuntimeAdapter;

/// <summary>
/// Tests d'intégration RÉELS de <see cref="RuntimeProcessRunner"/> — de
/// vrais sous-processus, jamais une simulation. Certains invoquent le CLI
/// Python du Runtime réel de ce dépôt (via <c>python3</c>) quand il est
/// disponible dans l'environnement d'exécution des tests, prouvant
/// l'interopérabilité de bout en bout ; les autres utilisent des
/// utilitaires POSIX universels (<c>sh</c>, <c>sleep</c>, <c>false</c>)
/// pour les scénarios impossibles à obtenir depuis un Runtime sain
/// (timeout, exécutable absent).
/// </summary>
public class RuntimeProcessRunnerTests
{
    private static readonly RuntimeProcessRunner Runner = new();

    [Fact]
    public async Task RunAsync_captures_real_stdout_from_a_real_process()
    {
        var result = await Runner.RunAsync("/bin/echo", ["hello-from-a-real-process"], TimeSpan.FromSeconds(5));

        Assert.True(result.ProcessStarted);
        Assert.False(result.TimedOut);
        Assert.Equal(0, result.ExitCode);
        Assert.Contains("hello-from-a-real-process", result.Stdout);
    }

    [Fact]
    public async Task RunAsync_reports_ProcessStarted_false_for_a_nonexistent_executable()
    {
        var result = await Runner.RunAsync(
            "/this/executable/does/not/exist/apex-setup-test", [], TimeSpan.FromSeconds(5));

        Assert.False(result.ProcessStarted);
        Assert.NotNull(result.StartFailureMessage);
    }

    [Fact]
    public async Task RunAsync_reports_TimedOut_true_for_a_real_hanging_process()
    {
        var result = await Runner.RunAsync("/bin/sleep", ["10"], TimeSpan.FromMilliseconds(500));

        Assert.True(result.ProcessStarted);
        Assert.True(result.TimedOut);
        Assert.Null(result.ExitCode);
    }

    [Fact]
    public async Task RunAsync_captures_a_real_nonzero_exit_code()
    {
        var result = await Runner.RunAsync("/bin/false", [], TimeSpan.FromSeconds(5));

        Assert.True(result.ProcessStarted);
        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public async Task RunAsync_captures_real_stderr_separately_from_stdout()
    {
        var script = "echo out-line; echo err-line 1>&2";
        var result = await Runner.RunAsync("/bin/sh", ["-c", script], TimeSpan.FromSeconds(5));

        Assert.Contains("out-line", result.Stdout);
        Assert.Contains("err-line", result.Stderr);
        Assert.DoesNotContain("err-line", result.Stdout);
    }

    [Fact]
    public async Task RunAsync_never_lets_a_shell_metacharacter_in_an_argument_escape_its_argument_boundary()
    {
        // Preuve directe d'absence d'injection de commande : ArgumentList
        // transmet CHAQUE argument comme un token distinct au processus
        // enfant — jamais construit via une ligne de commande shell. Si
        // une injection était possible, ce payload créerait le fichier
        // marker ; il ne doit JAMAIS être créé.
        var markerPath = Path.Combine(Path.GetTempPath(), $"apex-setup-injection-proof-{Guid.NewGuid():N}");
        var maliciousArgument = $"; touch {markerPath} #";

        try
        {
            var result = await Runner.RunAsync("/bin/echo", [maliciousArgument], TimeSpan.FromSeconds(5));

            Assert.True(result.ProcessStarted);
            Assert.Contains(maliciousArgument, result.Stdout); // echo l'imprime tel quel, littéralement
            Assert.False(File.Exists(markerPath), "un argument a été interprété par un shell — injection possible");
        }
        finally
        {
            if (File.Exists(markerPath))
            {
                File.Delete(markerPath);
            }
        }
    }

    // Ces deux tests exigent le Runtime Python de CE dépôt (.venv/bin/python3)
    // — toujours présent dans l'environnement de développement/CI de
    // apex-ia- (setup/ vit dans le même dépôt). Volontairement des [Fact]
    // ordinaires, jamais un skip conditionnel : un mécanisme de skip
    // dynamique (testé avec le paquet tiers Xunit.SkippableFact) s'est
    // révélé provoquer un code de sortie `dotnet test` incohérent selon le
    // logger utilisé (rapporté « Passed » avec code de sortie non nul) —
    // retiré au profit d'un échec explicite et lisible si l'environnement
    // manque, plutôt qu'un mécanisme fragile.
    [Fact]
    public async Task RunAsync_interoperates_with_the_real_python_runtime_cli_version_command()
    {
        var pythonExecutable = FindRepoPythonExecutable();
        Assert.True(pythonExecutable is not null,
            "Runtime Python (.venv/bin/python3) introuvable — attendu présent dans ce dépôt.");

        var result = await Runner.RunAsync(
            pythonExecutable!, ["-m", "apex.cli.main", "version", "--json"], TimeSpan.FromSeconds(30));

        Assert.True(result.ProcessStarted);
        Assert.Equal(0, result.ExitCode);
        Assert.Contains("\"version\"", result.Stdout);
        Assert.Contains("\"phase\"", result.Stdout);
    }

    [Fact]
    public async Task RunAsync_interoperates_with_the_real_python_runtime_cli_doctor_command()
    {
        var pythonExecutable = FindRepoPythonExecutable();
        Assert.True(pythonExecutable is not null,
            "Runtime Python (.venv/bin/python3) introuvable — attendu présent dans ce dépôt.");

        var result = await Runner.RunAsync(
            pythonExecutable!, ["-m", "apex.cli.main", "doctor", "--json"], TimeSpan.FromSeconds(30));

        Assert.True(result.ProcessStarted);
        Assert.False(result.TimedOut);
        // Doctor renvoie 0 ou 1 selon l'état RÉEL mesuré — jamais un autre code.
        Assert.True(result.ExitCode is 0 or 1);
        Assert.Contains("\"status\"", result.Stdout);
        Assert.Contains("\"results\"", result.Stdout);
    }

    private static string? FindRepoPythonExecutable()
    {
        var repoRoot = FindRepoRoot();
        if (repoRoot is null)
        {
            return null;
        }
        var venvPython = Path.Combine(repoRoot, ".venv", "bin", "python3");
        return File.Exists(venvPython) ? venvPython : null;
    }

    private static string? FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            if (File.Exists(Path.Combine(dir.FullName, "pyproject.toml"))
                && Directory.Exists(Path.Combine(dir.FullName, "apex")))
            {
                return dir.FullName;
            }
            dir = dir.Parent;
        }
        return null;
    }
}
