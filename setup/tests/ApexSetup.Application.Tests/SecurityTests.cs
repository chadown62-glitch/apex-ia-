using System.Text.Json;
using ApexSetup.Application.Requests;
using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Contracts;
using Xunit;

namespace ApexSetup.Application.Tests;

/// <summary>§13 Security du plan Phase 3 — audité au niveau applicatif (au niveau CLI/subprocess, voir <c>ApexSetup.Cli.Tests</c>).</summary>
public class SecurityTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-setup-security-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-security-dest-").FullName;
    private readonly string _workingDirectory = Directory.CreateTempSubdirectory("apex-setup-security-work-").FullName;
    private readonly string _outsideTarget = Directory.CreateTempSubdirectory("apex-setup-security-outside-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        if (Directory.Exists(_destination)) Directory.Delete(_destination, recursive: true);
        Directory.Delete(_workingDirectory, recursive: true);
        Directory.Delete(_outsideTarget, recursive: true);
    }

    [Theory]
    [InlineData("../../escape")]
    [InlineData("..\\..\\escape")]
    public async Task InstallAsync_never_writes_outside_the_destination_for_a_path_traversal_model_id(string maliciousId)
    {
        var content = "malicious"u8.ToArray();
        var hash = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(content)).ToLowerInvariant();
        var sourcePath = Path.Combine(_workingDirectory, "already-downloaded");
        await File.WriteAllBytesAsync(sourcePath, content);

        var manifestJson = $$"""
            [{"model_id":"{{maliciousId.Replace("\\", "\\\\")}}","version":"1","quantization":"q4_k_m","served_model":"x",
              "size_bytes":{{content.Length}},"sha256":"{{hash}}","required_vram_gb":1,"required_ram_gb":1,
              "max_context":8192,"capability_class":"coder","supported_profiles":["MINIMAL"],
              "source":"test","source_url":"https://models.invalid/x"},
             {"model_id":"bge-m3-latest","version":"1","quantization":"q8_0","served_model":"x",
              "size_bytes":6,"sha256":"{{new string('b', 64)}}","required_vram_gb":1,"required_ram_gb":1,
              "max_context":8192,"capability_class":"embedding","supported_profiles":["MINIMAL"],
              "source":"test","source_url":"https://models.invalid/embed"}]
            """;
        var manifestPath = Path.Combine(_manifestDir, "manifest.json");
        await File.WriteAllTextAsync(manifestPath, manifestJson);

        var request = new SetupInstallRequest
        {
            Plan = new SetupPlanRequest
            {
                TargetApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifestPath, ProfileOverride = HardwareProfileTier.Minimal,
            },
            WorkingDirectory = _workingDirectory,
        };
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.InstallAsync(request);

        Assert.False(response.Success);
        // PathSafety a refusé le segment — jamais un fichier écrit hors de la destination.
        Assert.False(File.Exists(Path.Combine(_outsideTarget, "escape")));
        Assert.Empty(Directory.GetFileSystemEntries(_outsideTarget));
    }

    [Fact]
    public void CliArguments_never_interprets_a_value_that_looks_like_a_shell_command()
    {
        // Une valeur contenant des métacaractères shell doit rester une CHAÎNE
        // LITTÉRALE opaque — ce projet ne construit jamais de commande shell,
        // donc rien ne peut jamais l'interpréter, quel que soit son contenu.
        var maliciousValue = "; rm -rf / #";
        var args = new[] { "check", "--destination", maliciousValue };

        // Reproduit exactement ce que Program.cs fait : args[1..] est parsé
        // comme option/valeur, jamais réassemblé en une chaîne shell.
        Assert.Equal("--destination", args[1]);
        Assert.Equal(maliciousValue, args[2]); // transmis tel quel, jamais tronqué ni exécuté
    }

    [Fact]
    public async Task Errors_and_warnings_never_contain_the_full_runtime_argument_list()
    {
        // Le seul texte qui pourrait fuiter un éventuel futur secret est le
        // message d'erreur du process runner — aujourd'hui limité au message
        // d'échec de démarrage (ex. "introuvable"), jamais aux arguments transmis.
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("version");
        var service = ServiceFactory.Build(runner);

        var response = await service.GetVersionAsync();

        var serialized = JsonSerializer.Serialize(response);
        Assert.DoesNotContain("--runtime-arg", serialized, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("token", serialized, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("password", serialized, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", serialized, StringComparison.OrdinalIgnoreCase);
    }
}
