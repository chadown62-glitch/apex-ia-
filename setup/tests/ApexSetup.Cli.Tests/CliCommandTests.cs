using System.Text.Json;
using Xunit;

namespace ApexSetup.Cli.Tests;

public class CliCommandTests
{
    /// <summary>Manifeste structurellement valide (JSON parseable, entrées ModelManifestEntry correctes) — pour isoler un test sur UN SEUL argument sans que le manifeste lui-même en soit la cause.</summary>
    private static string WriteValidManifest()
    {
        var path = Path.GetTempFileName();
        File.WriteAllText(path, """
            [{"model_id":"qwen2-5-coder-7b-instruct","version":"1","quantization":"q4_k_m","served_model":"x",
              "size_bytes":12,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "required_vram_gb":5,"required_ram_gb":8,"max_context":32768,"capability_class":"coder",
              "supported_profiles":["MINIMAL"],"source":"test","source_url":"https://models.invalid/fast"}]
            """);
        return path;
    }

    [Fact]
    public async Task Version_returns_a_structured_JSON_envelope_with_the_common_fields()
    {
        var result = await CliProcess.RunAsync("version");

        Assert.Equal(0, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal(1, doc.RootElement.GetProperty("schema_version").GetInt32());
        Assert.Equal("version", doc.RootElement.GetProperty("operation").GetString());
        Assert.True(doc.RootElement.GetProperty("success").GetBoolean());
        Assert.True(doc.RootElement.TryGetProperty("errors", out _));
        Assert.True(doc.RootElement.TryGetProperty("warnings", out _));
    }

    [Fact]
    public async Task Version_reports_the_centrally_versioned_setup_version_never_a_default_placeholder()
    {
        // PHASE 6 : setup/Directory.Build.props fixe une version PARTAGÉE
        // (CLI, GUI, manifeste de release) — jamais un "1.0.0.0" par
        // défaut d'assembly non versionnée.
        var result = await CliProcess.RunAsync("version");

        using var doc = JsonDocument.Parse(result.Stdout);
        var setupVersion = doc.RootElement.GetProperty("setup_version").GetString();
        Assert.NotEqual("1.0.0.0", setupVersion);
        Assert.StartsWith(CentralSetupVersion(), setupVersion);
    }

    /// <summary>La valeur de <c>SetupProductVersion</c> lue dans <c>setup/Directory.Build.props</c> — la source unique.</summary>
    private static string CentralSetupVersion()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "Directory.Build.props")))
        {
            dir = dir.Parent;
        }
        Assert.NotNull(dir);
        var props = System.Xml.Linq.XDocument.Load(Path.Combine(dir!.FullName, "Directory.Build.props"));
        var version = props.Descendants("SetupProductVersion").Single().Value;
        Assert.Matches(@"^\d+\.\d+\.\d+$", version);
        return version;
    }

    [Fact]
    public async Task Detect_returns_zero_and_a_recommended_profile_field_never_an_authorization_claim()
    {
        var result = await CliProcess.RunAsync("detect", "--destination", Path.GetTempPath());

        Assert.Equal(0, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.True(doc.RootElement.TryGetProperty("recommended_profile", out _));
        var note = doc.RootElement.GetProperty("profile_note").GetString();
        Assert.Contains("recommandation", note, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task An_unknown_command_returns_InvalidArguments_with_a_structured_error_never_a_stack_trace()
    {
        var result = await CliProcess.RunAsync("not-a-real-command");

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.False(doc.RootElement.GetProperty("success").GetBoolean());
        var errors = doc.RootElement.GetProperty("errors");
        Assert.Equal(1, errors.GetArrayLength());
        Assert.Equal("invalid_arguments", errors[0].GetProperty("code").GetString());
        Assert.DoesNotContain("at ApexSetup", result.Stdout); // jamais une trace de pile exposée au GUI
    }

    [Fact]
    public async Task Missing_required_arguments_return_InvalidArguments_never_an_unhandled_exception()
    {
        var result = await CliProcess.RunAsync("plan");

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
    }

    [Fact]
    public async Task An_empty_destination_value_is_rejected_as_InvalidArguments_never_an_internal_error()
    {
        // REVUE ADVERSARIALE PHASE 3 §14 (Argument parsing, "valeur vide") :
        // avant correctif, `--destination ""` traversait tout le chemin
        // jusqu'à `Path.GetFullPath("")` (Core), qui lève une
        // `ArgumentException` NON INTERCEPTÉE avant Program.Main — le CLI
        // ne plantait pas brutalement (le catch générique de Main la
        // convertissait en JSON), mais rapportait `internal_error` / exit
        // 99 pour ce qui est en réalité un problème de PARAMÈTRE, jamais
        // une erreur interne inattendue. Un même scénario doit toujours
        // donner le même code de sortie — une valeur vide est une erreur
        // de paramètre, pas un bug interne.
        var manifestPath = WriteValidManifest();
        try
        {
            var result = await CliProcess.RunAsync(
                "plan", "--apex-version", "0.1.2", "--destination", "", "--manifest", manifestPath, "--profile", "Minimal");

            Assert.Equal(1, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
        }
        finally
        {
            File.Delete(manifestPath);
        }
    }

    [Fact]
    public async Task An_empty_manifest_path_is_rejected_as_InvalidArguments_never_an_internal_error()
    {
        var result = await CliProcess.RunAsync(
            "plan", "--apex-version", "0.1.2", "--destination", Path.GetTempPath(), "--manifest", "", "--profile", "Minimal");

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
    }

    [Fact]
    public async Task An_empty_apex_version_is_rejected_as_InvalidArguments_never_an_internal_error()
    {
        var manifestPath = WriteValidManifest();
        try
        {
            var result = await CliProcess.RunAsync(
                "plan", "--apex-version", "", "--destination", Path.GetTempPath(), "--manifest", manifestPath, "--profile", "Minimal");

            Assert.Equal(1, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
        }
        finally
        {
            File.Delete(manifestPath);
        }
    }

    [Fact]
    public async Task An_empty_runtime_executable_override_is_rejected_as_InvalidArguments_never_an_internal_error()
    {
        // REVUE ADVERSARIALE PHASE 3 §14/§8 : `--runtime-executable ""`
        // atteignait `Process.Start` avec un `FileName` vide, qui lève
        // `InvalidOperationException` ("Cannot start process because a
        // file name has not been provided") — NON interceptée avant
        // Program.Main, rapportée comme `internal_error`/exit 99 alors
        // qu'il s'agit d'un argument invalide, jamais d'une panne interne.
        var result = await CliProcess.RunAsync("version", "--runtime-executable", "");

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
    }

    [Fact]
    public async Task An_excessively_long_destination_path_is_rejected_as_InvalidArguments_never_an_internal_error()
    {
        // §14/§7 : un chemin trop long lève `PathTooLongException` dès le
        // premier `Path.GetFullPath` (Core/Application) — un problème
        // d'ARGUMENT (valeur inutilisable), jamais une panne interne.
        var longSegment = new string('a', 5000);
        var result = await CliProcess.RunAsync("status", "--destination", $"/tmp/{longSegment}");

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Equal("invalid_arguments", doc.RootElement.GetProperty("errors")[0].GetProperty("code").GetString());
    }

    [Fact]
    public async Task An_invalid_profile_value_is_rejected_as_InvalidArguments_never_silently_defaulted()
    {
        var manifestPath = Path.GetTempFileName();
        try
        {
            var result = await CliProcess.RunAsync(
                "plan", "--apex-version", "0.1.2", "--destination", Path.GetTempPath(),
                "--manifest", manifestPath, "--profile", "NotARealProfile");

            Assert.Equal(1, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.False(doc.RootElement.GetProperty("success").GetBoolean());
        }
        finally
        {
            File.Delete(manifestPath);
        }
    }

    [Fact]
    public async Task No_command_at_all_returns_InvalidArguments_with_a_usage_hint()
    {
        var result = await CliProcess.RunAsync();

        Assert.Equal(1, result.ExitCode);
        using var doc = JsonDocument.Parse(result.Stdout);
        Assert.Contains("commande", doc.RootElement.GetProperty("errors")[0].GetProperty("message").GetString(), StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task The_human_flag_switches_to_readable_text_and_never_emits_JSON_on_stdout()
    {
        var result = await CliProcess.RunAsync("version", "--human");

        Assert.Equal(0, result.ExitCode);
        Assert.False(IsJson(result.Stdout));
        Assert.Contains("Setup CLI", result.Stdout);
    }

    [Theory]
    [InlineData("plan")] // argument obligatoire manquant (--apex-version)
    [InlineData("not-a-real-command")] // commande inconnue
    public async Task The_human_flag_is_respected_even_on_error_paths_never_silently_falling_back_to_JSON(string command)
    {
        // REVUE ADVERSARIALE PHASE 3 §13 (Human Output) : « tester toutes
        // les erreurs principales avec --human ». Avant correctif,
        // `EmitArgumentError`/`EmitInternalError` ignoraient TOUJOURS le
        // flag `--human` et écrivaient du JSON sur stdout quel que soit
        // le chemin d'erreur — seul le chemin de SUCCÈS respectait
        // `--human` (via `Emit<T>`). Une divergence directe entre le texte
        // humain et le JSON pour exactement le même événement.
        var result = await CliProcess.RunAsync(command, "--human");

        Assert.Equal(1, result.ExitCode);
        Assert.False(IsJson(result.Stdout), $"stdout aurait dû être du texte humain, pas du JSON : {result.Stdout}");
        Assert.Contains("ERREUR", result.Stderr); // le message reste réellement communiqué, pas juste absent de stdout
    }

    [Fact]
    public async Task The_human_flag_is_respected_even_when_argument_parsing_itself_fails()
    {
        // Cas limite : un jeton qui casse le PARSING lui-même (ni option
        // `--xxx` ni valeur rattachée à une option précédente) fait échouer
        // `CliArguments.Parse` avant même que `--human` n'y soit lu — le
        // flag doit donc rester détecté par un scan brut de `args`, jamais
        // seulement après un `CliArguments.Parse` réussi.
        var result = await CliProcess.RunAsync("status", "valeur-orpheline-sans-option", "--human");

        Assert.Equal(1, result.ExitCode);
        Assert.False(IsJson(result.Stdout));
    }

    [Fact]
    public async Task Status_on_a_never_touched_destination_reports_no_installation_and_exits_zero()
    {
        var dir = Directory.CreateTempSubdirectory("apex-setup-cli-status-").FullName;
        try
        {
            var result = await CliProcess.RunAsync("status", "--destination", dir);

            Assert.Equal(0, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.Equal("no_installation", doc.RootElement.GetProperty("state").GetString());
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public async Task Recover_on_an_untouched_destination_takes_no_action_and_exits_zero()
    {
        var dir = Directory.CreateTempSubdirectory("apex-setup-cli-recover-").FullName;
        try
        {
            var result = await CliProcess.RunAsync("recover", "--destination", dir);

            Assert.Equal(0, result.ExitCode);
            using var doc = JsonDocument.Parse(result.Stdout);
            Assert.Equal("none", doc.RootElement.GetProperty("action_taken").GetString());
        }
        finally
        {
            Directory.Delete(dir, recursive: true);
        }
    }

    private static bool IsJson(string text)
    {
        try { JsonDocument.Parse(text); return true; } catch (JsonException) { return false; }
    }
}
