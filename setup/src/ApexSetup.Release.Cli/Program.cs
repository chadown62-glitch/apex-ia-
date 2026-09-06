using ApexSetup.Release;

namespace ApexSetup.Release.Cli;

/// <summary>
/// Outil d'invocation réel pour <see cref="ApexSetup.Release"/> — Phase 8.
/// <c>ApexSetup.Release</c> est une bibliothèque pure sans point d'entrée ;
/// ce projet lui en donne un pour que <c>setup/packaging/release.sh</c>
/// (et un opérateur humain) puisse générer/vérifier un manifeste sans
/// écrire de C# ad hoc. Parsing d'arguments manuel — aucune dépendance
/// externe non nécessaire (même discipline qu'<c>ApexSetup.Cli</c>).
/// </summary>
public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        if (args.Length == 0)
        {
            return PrintUsage();
        }

        try
        {
            return args[0] switch
            {
                "generate" => await RunGenerateAsync(args[1..]).ConfigureAwait(false),
                "validate" => await RunValidateAsync(args[1..]).ConfigureAwait(false),
                "-h" or "--help" => PrintUsage(),
                _ => PrintUnknownCommand(args[0]),
            };
        }
        catch (CliArgumentException ex)
        {
            Console.Error.WriteLine($"[apex-setup-release] argument invalide : {ex.Message}");
            return 2;
        }
        catch (FileNotFoundException ex)
        {
            Console.Error.WriteLine($"[apex-setup-release] fichier introuvable : {ex.Message}");
            return 2;
        }
    }

    private static async Task<int> RunGenerateAsync(string[] args)
    {
        var opts = ParseOptions(args);
        var artifactPath = RequireOption(opts, "artifact");
        var outPath = RequireOption(opts, "out");
        var productName = RequireOption(opts, "product-name");
        var setupVersion = RequireOption(opts, "setup-version");
        var architecture = RequireOption(opts, "architecture");
        opts.TryGetValue("runtime-version", out var runtimeVersion);
        opts.TryGetValue("commit-sha", out var commitSha);

        var manifest = await ReleaseManifestBuilder.BuildAsync(
            artifactPath, productName, setupVersion, architecture,
            runtimeVersion: runtimeVersion, buildCommitSha: commitSha).ConfigureAwait(false);

        await ReleaseManifestJson.WriteAsync(outPath, manifest).ConfigureAwait(false);

        Console.WriteLine($"[apex-setup-release] manifeste écrit : {outPath}");
        Console.WriteLine(ReleaseManifestJson.Serialize(manifest));
        return 0;
    }

    private static async Task<int> RunValidateAsync(string[] args)
    {
        var opts = ParseOptions(args);
        var manifestPath = RequireOption(opts, "manifest");
        var artifactDirectory = RequireOption(opts, "artifact-dir");
        var architecture = RequireOption(opts, "architecture");
        var setupVersion = RequireOption(opts, "setup-version");

        var result = await ReleaseManifestValidator.ValidateAsync(
            manifestPath, artifactDirectory, architecture, setupVersion).ConfigureAwait(false);

        Console.WriteLine($"[apex-setup-release] {result.Outcome} : {result.Detail}");
        return result.IsValid ? 0 : 1;
    }

    private static Dictionary<string, string> ParseOptions(string[] args)
    {
        var options = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            if (!arg.StartsWith("--", StringComparison.Ordinal))
            {
                throw new CliArgumentException($"option inattendue « {arg} » (attendu : --nom valeur)");
            }
            var name = arg[2..];
            if (i + 1 >= args.Length)
            {
                throw new CliArgumentException($"--{name} nécessite une valeur");
            }
            options[name] = args[++i];
        }
        return options;
    }

    private static string RequireOption(Dictionary<string, string> options, string name)
    {
        if (!options.TryGetValue(name, out var value) || string.IsNullOrWhiteSpace(value))
        {
            throw new CliArgumentException($"--{name} est requis");
        }
        return value;
    }

    private static int PrintUsage()
    {
        Console.WriteLine("""
            apex-setup-release — génération et vérification du manifeste de release (Phase 7/8)

            Usage :
              apex-setup-release generate --artifact <chemin> --out <manifest.json>
                  --product-name <nom> --setup-version <version> --architecture <win-x64|...>
                  [--runtime-version <version>] [--commit-sha <sha>]

              apex-setup-release validate --manifest <manifest.json> --artifact-dir <répertoire>
                  --architecture <win-x64|...> --setup-version <version>

            Codes de sortie : 0 = succès/manifeste valide, 1 = manifeste invalide/artefact modifié, 2 = usage invalide.
            """);
        return 0;
    }

    private static int PrintUnknownCommand(string command)
    {
        Console.Error.WriteLine($"[apex-setup-release] commande inconnue : « {command} » (attendu : generate | validate)");
        return 2;
    }
}

internal sealed class CliArgumentException(string message) : Exception(message);
