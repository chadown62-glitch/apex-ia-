using System.Text.Json;
using Xunit;

namespace ApexSetup.Cli.Tests;

/// <summary>
/// Contre le VRAI Runtime Python de ce dépôt (<c>.venv/bin/python3 -m
/// apex.cli.main</c>) — même discipline que
/// <c>ApexSetup.Core.Tests.RuntimeAdapter.RuntimeProcessRunnerTests</c>.
/// Ignoré silencieusement (jamais un échec de build) si ce Runtime n'est
/// pas présent dans l'environnement d'exécution des tests.
/// </summary>
public class CliRuntimeIntegrationTests
{
    private static string? FindRepoPythonExecutable()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var venvPython = Path.Combine(dir.FullName, ".venv", "bin", "python3");
            if (File.Exists(venvPython))
            {
                return venvPython;
            }
            dir = dir.Parent;
        }
        return null;
    }

    private static string WriteMinimalManifest(string directory)
    {
        var json = """
            [
              {"model_id":"qwen2-5-coder-7b-instruct","version":"1","quantization":"q4_k_m","served_model":"x",
               "size_bytes":12,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
               "required_vram_gb":5,"required_ram_gb":8,"max_context":32768,"capability_class":"coder",
               "supported_profiles":["MINIMAL"],"source":"test","source_url":"https://models.invalid/fast"},
              {"model_id":"bge-m3-latest","version":"1","quantization":"q8_0","served_model":"x",
               "size_bytes":6,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
               "required_vram_gb":1,"required_ram_gb":2,"max_context":8192,"capability_class":"embedding",
               "supported_profiles":["MINIMAL"],"source":"test","source_url":"https://models.invalid/embed"}
            ]
            """;
        var path = Path.Combine(directory, "manifest.json");
        File.WriteAllText(path, json);
        return path;
    }

    [Fact]
    public async Task Check_against_the_real_Runtime_reports_a_structured_verdict_and_a_matching_exit_code()
    {
        var python = FindRepoPythonExecutable();
        if (python is null) return; // Runtime absent de cet environnement — voir le commentaire de classe

        var dir = Directory.CreateTempSubdirectory("apex-setup-cli-real-check-").FullName;
        try
        {
            var manifest = WriteMinimalManifest(dir);
            var result = await CliProcess.RunAsync(
                "check", "--apex-version", "0.1.2", "--destination", Path.Combine(dir, "dest"),
                "--manifest", manifest, "--profile", "Minimal",
                "--runtime-executable", python, "--runtime-arg", "-m", "--runtime-arg", "apex.cli.main");

            using var doc = JsonDocument.Parse(result.Stdout);
            var success = doc.RootElement.GetProperty("success").GetBoolean();
            // L'exit code doit refléter EXACTEMENT le verdict JSON, jamais l'un sans l'autre.
            Assert.Equal(success, result.ExitCode == 0);
            Assert.True(doc.RootElement.GetProperty("checks").GetArrayLength() > 0);
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public async Task Install_against_the_real_Runtime_never_touches_the_destination_when_refused()
    {
        var python = FindRepoPythonExecutable();
        if (python is null) return;

        var dir = Directory.CreateTempSubdirectory("apex-setup-cli-real-install-").FullName;
        var destination = Path.Combine(dir, "dest");
        try
        {
            var manifest = WriteMinimalManifest(dir);
            var result = await CliProcess.RunAsync(
                "install", "--apex-version", "0.1.2", "--destination", destination,
                "--manifest", manifest, "--profile", "Minimal", "--working-directory", Path.Combine(dir, "work"),
                "--runtime-executable", python, "--runtime-arg", "-m", "--runtime-arg", "apex.cli.main");

            using var doc = JsonDocument.Parse(result.Stdout);
            var committed = doc.RootElement.GetProperty("committed").GetBoolean();
            if (!committed)
            {
                // Jamais un artefact écrit tant que Preflight/Gate n'ont pas autorisé.
                Assert.False(File.Exists(Path.Combine(destination, ".apex-setup-installed")));
                Assert.Contains(result.ExitCode, new[] { 2, 3, 4 }); // PreflightBlocking / RuntimeUnavailable / GateBlocking
            }
            else
            {
                Assert.Equal(0, result.ExitCode);
            }
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public async Task An_unreachable_runtime_executable_yields_the_RuntimeUnavailable_exit_code_on_check()
    {
        var dir = Directory.CreateTempSubdirectory("apex-setup-cli-real-unreachable-").FullName;
        try
        {
            var manifest = WriteMinimalManifest(dir);
            var result = await CliProcess.RunAsync(
                "check", "--apex-version", "0.1.2", "--destination", Path.Combine(dir, "dest"),
                "--manifest", manifest, "--profile", "Minimal",
                "--runtime-executable", "/definitely/does/not/exist/apex");

            Assert.Equal(3, result.ExitCode); // SetupExitCode.RuntimeUnavailable
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }
}
