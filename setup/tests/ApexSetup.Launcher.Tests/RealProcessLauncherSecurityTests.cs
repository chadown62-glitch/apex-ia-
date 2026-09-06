using ApexSetup.Launcher.ProcessLaunch;
using Xunit;

// CA1416 : ce fichier teste RealProcessLauncher via de vrais scripts bash —
// intrinsèquement Linux/WSL, jamais exécuté ni destiné à l'être sous
// Windows natif (File.SetUnixFileMode y est simplement un no-op documenté).
#pragma warning disable CA1416

namespace ApexSetup.Launcher.Tests;

/// <summary>
/// Vérifie empiriquement — pas seulement par lecture de code — que
/// <see cref="RealProcessLauncher"/> ne passe JAMAIS par un shell
/// interprété : des arguments contenant des métacaractères shell
/// (`; rm -rf /`, `$(...)`, guillemets, espaces) doivent atteindre le
/// process cible EXACTEMENT tels quels, jamais interprétés/exécutés
/// (règle absolue #10/#11 de la mission N+15).
/// </summary>
public class RealProcessLauncherSecurityTests
{
    private static string WriteArgvDumpScript()
    {
        var path = Path.Combine(Path.GetTempPath(), $"apex-launcher-argv-dump-{Guid.NewGuid():N}.sh");
        File.WriteAllText(path, "#!/usr/bin/env bash\nfor a in \"$@\"; do printf '%s\\x1f' \"$a\"; done\n");
        File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        return path;
    }

    [Theory]
    [InlineData("; rm -rf /tmp/should-not-be-touched")]
    [InlineData("$(whoami)")]
    [InlineData("`whoami`")]
    [InlineData("a b c")]
    [InlineData("a\"b'c")]
    [InlineData("--flag=value; echo injected")]
    [InlineData("a\nb")]
    public async Task Argument_reaches_the_child_process_byte_for_byte_never_interpreted_by_a_shell(string maliciousArgument)
    {
        var script = WriteArgvDumpScript();
        try
        {
            var launcher = new RealProcessLauncher();
            var capture = Path.Combine(Path.GetTempPath(), $"apex-launcher-argv-capture-{Guid.NewGuid():N}.txt");
            try
            {
                // On redirige la sortie standard via un second script bash
                // (toujours ArgumentList, jamais une chaîne) plutôt que
                // RedirectStandardOutput (RealProcessLauncher ne l'active
                // pas — voir sa docstring) : on invoque bash lui-même avec
                // ArgumentList pour composer "script argv... > capture".
                var wrapper = Path.Combine(Path.GetTempPath(), $"apex-launcher-wrapper-{Guid.NewGuid():N}.sh");
                File.WriteAllText(wrapper, $"#!/usr/bin/env bash\n\"{script}\" \"$@\" > \"{capture}\"\n");
                File.SetUnixFileMode(wrapper, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);

                var result = await launcher.RunAsync(
                    "/usr/bin/env", ["bash", wrapper, maliciousArgument], Path.GetTempPath(), CancellationToken.None);

                Assert.True(result.Started);
                Assert.Equal(0, result.ExitCode);

                var captured = File.ReadAllText(capture).TrimEnd('\x1f', '\n');
                Assert.Equal(maliciousArgument, captured);

                File.Delete(wrapper);
            }
            finally
            {
                if (File.Exists(capture)) File.Delete(capture);
            }
        }
        finally
        {
            File.Delete(script);
        }
    }

    [Fact]
    public async Task Unknown_executable_never_throws_and_reports_not_started()
    {
        var launcher = new RealProcessLauncher();
        var result = await launcher.RunAsync(
            "/this/executable/does/not/exist-12345", [], Path.GetTempPath(), CancellationToken.None);

        Assert.False(result.Started);
        Assert.NotNull(result.FailureReason);
    }

    [Fact]
    public async Task Path_traversal_style_argument_is_never_resolved_or_expanded_by_the_launcher()
    {
        // RealProcessLauncher ne touche jamais lui-même au système de
        // fichiers pour "résoudre" un chemin — il transmet l'argument tel
        // quel au process cible, qui seul décide de son interprétation.
        var script = WriteArgvDumpScript();
        try
        {
            var launcher = new RealProcessLauncher();
            var capture = Path.Combine(Path.GetTempPath(), $"apex-launcher-argv-capture-{Guid.NewGuid():N}.txt");
            var wrapper = Path.Combine(Path.GetTempPath(), $"apex-launcher-wrapper-{Guid.NewGuid():N}.sh");
            File.WriteAllText(wrapper, $"#!/usr/bin/env bash\n\"{script}\" \"$@\" > \"{capture}\"\n");
            File.SetUnixFileMode(wrapper, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
            try
            {
                const string traversal = "../../../../etc/passwd";
                var result = await launcher.RunAsync("/usr/bin/env", ["bash", wrapper, traversal], Path.GetTempPath(), CancellationToken.None);
                Assert.True(result.Started);
                var captured = File.ReadAllText(capture).TrimEnd('\x1f', '\n');
                Assert.Equal(traversal, captured);
            }
            finally
            {
                if (File.Exists(capture)) File.Delete(capture);
                File.Delete(wrapper);
            }
        }
        finally
        {
            File.Delete(script);
        }
    }
}
