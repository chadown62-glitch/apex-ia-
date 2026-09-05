using System.Text.Json;
using Xunit;

namespace ApexSetup.Cli.Tests;

/// <summary>§13 Security, contre le VRAI binaire — complète les tests en mémoire de ApexSetup.Application.Tests.</summary>
public class CliSecurityTests
{
    [Fact]
    public async Task A_shell_metacharacter_payload_in_an_argument_value_is_never_interpreted()
    {
        var marker = Path.Combine(Path.GetTempPath(), $"apex-setup-pwned-{Guid.NewGuid():N}");
        try
        {
            // Si un jour ce CLI construisait une commande shell par
            // concaténation, cette valeur créerait le fichier marqueur.
            var payload = $"; touch {marker} #";

            var result = await CliProcess.RunAsync("status", "--destination", payload);

            Assert.False(File.Exists(marker)); // preuve directe : aucune interprétation shell n'a eu lieu
            // Le chemin littéral (avec ses métacaractères) n'existe simplement
            // pas sur le disque -> traité comme une destination ordinaire sans
            // trace Setup, jamais une erreur d'interprétation spéciale.
            Assert.Equal(0, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.Equal("no_installation", doc.RootElement.GetProperty("state").GetString());
        }
        finally
        {
            if (File.Exists(marker))
            {
                File.Delete(marker);
            }
        }
    }

    [Fact]
    public async Task A_backtick_and_dollar_paren_payload_is_treated_as_an_opaque_literal_string()
    {
        var marker = Path.Combine(Path.GetTempPath(), $"apex-setup-pwned2-{Guid.NewGuid():N}");
        var payload = $"$(touch {marker})`touch {marker}`";

        await CliProcess.RunAsync("status", "--destination", payload);

        Assert.False(File.Exists(marker));
    }

    [Fact]
    public async Task A_malicious_runtime_executable_path_never_escalates_only_fails_to_start_cleanly()
    {
        var result = await CliProcess.RunAsync(
            "version", "--runtime-executable", "/nonexistent/; rm -rf / #", "--runtime-arg", "--dangerous");

        // Le process runner tente de démarrer CE fichier littéral (qui
        // n'existe pas) -> échec de démarrage propre, jamais une commande
        // shell exécutée avec les arguments qui suivent.
        Assert.Equal(0, result.ExitCode); // `version` réussit toujours, avec juste un avertissement Runtime injoignable
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal(1, doc.RootElement.GetProperty("warnings").GetArrayLength());
    }

    [Fact]
    public async Task A_path_traversal_destination_is_handled_as_an_ordinary_path_never_specially_interpreted()
    {
        var result = await CliProcess.RunAsync("status", "--destination", "../../../../../../etc");

        // Traité comme un chemin ordinaire pointant vers un répertoire
        // système existant mais sans trace Setup dedans -> aucune
        // installation, jamais une erreur d'interprétation spéciale.
        Assert.Equal(0, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal("no_installation", doc.RootElement.GetProperty("state").GetString());
    }

    [Fact]
    public async Task No_response_ever_contains_an_environment_variable_dump_or_credential_looking_text()
    {
        Environment.SetEnvironmentVariable("APEX_SETUP_TEST_SECRET_TOKEN", "should-never-leak-abc123");
        try
        {
            var result = await CliProcess.RunAsync("version");

            Assert.DoesNotContain("should-never-leak-abc123", result.Stdout);
            Assert.DoesNotContain("should-never-leak-abc123", result.Stderr);
        }
        finally
        {
            Environment.SetEnvironmentVariable("APEX_SETUP_TEST_SECRET_TOKEN", null);
        }
    }
}
