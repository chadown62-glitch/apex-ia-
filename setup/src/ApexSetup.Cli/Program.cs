using System.Text.Json;
using System.Text.Json.Serialization;
using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Preflight;

namespace ApexSetup.Cli;

/// <summary>
/// Point d'entrée du CLI Setup public — la SEULE façade que le futur GUI
/// n'est PAS obligé d'utiliser (il peut appeler
/// <c>ApexSetup.Application.ISetupApplicationService</c> directement,
/// voir §14 du plan Phase 3), mais qui reste la référence de comportement :
/// arguments -> requête applicative -> réponse JSON structurée -> code de
/// sortie déterministe. Ne contient AUCUNE logique métier — uniquement du
/// parsing d'arguments, de la construction d'objets de requête, et de la
/// sérialisation. Jamais de <c>Process.Start</c> avec un shell, jamais de
/// concaténation de chaîne pour construire une commande (voir §13
/// Security) — le seul sous-processus jamais lancé par ce dépôt Setup est
/// celui du Runtime, exclusivement via <c>RuntimeProcessRunner</c>
/// (<c>ProcessStartInfo.ArgumentList</c>), lui-même appelé uniquement à
/// travers <c>RuntimeAdapter</c>.
/// </summary>
public static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        // Toute énumération (Core ou Application) est sérialisée en texte
        // snake_case, jamais en entier brut — un GUI ne doit jamais avoir
        // besoin de connaître l'ORDRE de déclaration d'un enum C# pour
        // interpréter une réponse (un entier nu serait aussi opaque qu'un
        // texte humain non structuré, voir §2 du plan Phase 3).
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
    };

    public static async Task<int> Main(string[] args)
    {
        // REVUE ADVERSARIALE PHASE 3 (§13 Human Output) : `--human` DOIT
        // être respecté même sur un chemin d'erreur — y compris quand
        // aucune commande n'est fournie du tout, ou quand
        // `CliArguments.Parse` lui-même échoue (auquel cas `options`
        // n'existe pas encore pour y lire le flag proprement). Un scan
        // brut de `args`, fait UNE SEULE FOIS ici, couvre TOUS les chemins
        // d'erreur uniformément — jamais seulement le chemin de succès.
        var human = Array.Exists(args, a => a == "--human");

        if (args.Length == 0)
        {
            return EmitArgumentError(
                "unknown", "aucune commande fournie — commandes disponibles : version, detect, check, plan, install, recover, validate, status", human);
        }

        var command = args[0];
        CliArguments options;
        try
        {
            options = CliArguments.Parse(args, startIndex: 1);
        }
        catch (CliArgumentException ex)
        {
            return EmitArgumentError(command, ex.Message, human);
        }

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };

        try
        {
            var service = BuildService(options);

            return command switch
            {
                "version" => await RunVersionAsync(service, human, cts.Token).ConfigureAwait(false),
                "detect" => await RunDetectAsync(service, options, human, cts.Token).ConfigureAwait(false),
                "check" => await RunCheckAsync(service, options, human, cts.Token).ConfigureAwait(false),
                "plan" => await RunPlanAsync(service, options, human, cts.Token).ConfigureAwait(false),
                "install" => await RunInstallAsync(service, options, human, cts.Token).ConfigureAwait(false),
                "recover" => await RunRecoverAsync(service, options, human, cts.Token).ConfigureAwait(false),
                "validate" => await RunValidateAsync(service, human, cts.Token).ConfigureAwait(false),
                "status" => await RunStatusAsync(service, options, human, cts.Token).ConfigureAwait(false),
                _ => EmitArgumentError(command, $"commande inconnue : « {command} »", human),
            };
        }
        catch (CliArgumentException ex)
        {
            return EmitArgumentError(command, ex.Message, human);
        }
        catch (Exception ex) when (ex is ArgumentException or PathTooLongException)
        {
            // REVUE ADVERSARIALE PHASE 3 (§7/§14) : `ArgumentException`
            // (levée par ex. par `Path.GetFullPath` sur une valeur
            // structurellement invalide) et `PathTooLongException`
            // signalent TOUJOURS un ARGUMENT malformé fourni par
            // l'utilisateur (chemin vide, trop long, caractères
            // invalides...) — jamais une panne interne du CLI, quel que
            // soit l'endroit exact (Core ou Application) où l'API .NET
            // sous-jacente les a levées. Filet de sécurité générique en
            // complément des rejets explicites de `CliArguments.Require` —
            // celui-ci ne peut anticiper que la vacuité, pas chaque forme
            // de chemin invalide (trop long, caractères interdits...).
            return EmitArgumentError(command, $"valeur d'argument invalide : {ex.Message}", human);
        }
        catch (Exception ex)
        {
            EmitInternalError(command, ex, human);
            return (int)SetupExitCode.InternalError;
        }
    }

    // -------------------------------------------------------------
    // Composition
    // -------------------------------------------------------------

    private static SetupApplicationService BuildService(CliArguments options)
    {
        // PHASE 4 : racine de composition PARTAGÉE avec le GUI — voir
        // ApexSetup.Application.SetupApplicationServiceFactory. Le CLI ne
        // fait plus qu'extraire les valeurs de ses propres arguments ;
        // l'assemblage réel (RuntimeAdapter/EnvironmentProbe/HardwareDetector/HttpClient)
        // n'existe plus qu'à un seul endroit.
        var runtimeArgs = options.GetAll("runtime-arg");
        var timeoutSeconds = int.TryParse(options.Get("runtime-timeout-seconds"), out var t) ? t : 30;

        try
        {
            return SetupApplicationServiceFactory.Create(
                options.Get("runtime-executable"), runtimeArgs, TimeSpan.FromSeconds(timeoutSeconds));
        }
        catch (ArgumentException ex)
        {
            throw new CliArgumentException(ex.Message);
        }
    }

    private static SetupPlanRequest BuildPlanRequest(CliArguments options)
    {
        HardwareProfileTier? profileOverride = null;
        var profileArg = options.Get("profile");
        if (profileArg is not null)
        {
            if (!Enum.TryParse<HardwareProfileTier>(profileArg, ignoreCase: true, out var parsed))
            {
                throw new CliArgumentException($"« --profile » invalide : « {profileArg} » (attendu : Minimal, Standard, Performance, HighEnd)");
            }
            profileOverride = parsed;
        }

        return new SetupPlanRequest
        {
            TargetApexVersion = options.Require("apex-version"),
            Destination = options.Require("destination"),
            ManifestPath = options.Require("manifest"),
            ProfileOverride = profileOverride,
            MinimumApexVersion = options.Get("minimum-apex-version") ?? PreflightChecker.DefaultMinimumApexVersion,
        };
    }

    // -------------------------------------------------------------
    // Commandes
    // -------------------------------------------------------------

    private static async Task<int> RunVersionAsync(SetupApplicationService service, bool human, CancellationToken ct)
    {
        var response = await service.GetVersionAsync(ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunDetectAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var destination = options.Get("destination") ?? Directory.GetCurrentDirectory();
        var response = await service.DetectAsync(destination, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunCheckAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var request = BuildPlanRequest(options);
        var response = await service.CheckAsync(request, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunPlanAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var request = BuildPlanRequest(options);
        var response = await service.PlanAsync(request, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunInstallAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var planRequest = BuildPlanRequest(options);
        var workingDirectory = options.Get("working-directory") ?? Path.Combine(Path.GetTempPath(), "apex-setup-work");
        var request = new SetupInstallRequest { Plan = planRequest, WorkingDirectory = workingDirectory };
        var response = await service.InstallAsync(request, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunRecoverAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var destination = options.Require("destination");
        var response = await service.RecoverAsync(destination, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunValidateAsync(SetupApplicationService service, bool human, CancellationToken ct)
    {
        var response = await service.ValidateAsync(ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    private static async Task<int> RunStatusAsync(SetupApplicationService service, CliArguments options, bool human, CancellationToken ct)
    {
        var destination = options.Require("destination");
        var response = await service.StatusAsync(destination, ct).ConfigureAwait(false);
        return Emit(response, human, HumanRenderer.Render, ExitCodeMapper.For);
    }

    // -------------------------------------------------------------
    // Émission (JSON par défaut, texte humain optionnel), jamais les deux à la fois.
    // -------------------------------------------------------------

    private static int Emit<T>(T response, bool human, Action<T> renderHuman, Func<T, SetupExitCode> mapExitCode)
    {
        if (human)
        {
            renderHuman(response);
        }
        else
        {
            Console.WriteLine(JsonSerializer.Serialize(response, JsonOptions));
        }
        return (int)mapExitCode(response);
    }

    private static int EmitArgumentError(string operation, string message, bool human)
    {
        if (human)
        {
            Console.Error.WriteLine($"ERREUR [invalid_arguments] {message}");
            return (int)SetupExitCode.InvalidArguments;
        }

        var envelope = new
        {
            schema_version = CliContractVersion.Current,
            operation,
            success = false,
            errors = new[] { new { code = "invalid_arguments", message, detail = (string?)null } },
            warnings = Array.Empty<object>(),
        };
        Console.WriteLine(JsonSerializer.Serialize(envelope, JsonOptions));
        return (int)SetupExitCode.InvalidArguments;
    }

    private static void EmitInternalError(string operation, Exception ex, bool human)
    {
        // Jamais un secret dans un message d'erreur — voir §15 Logging : ce
        // CLI ne manipule ni token ni identifiant, donc le message
        // d'exception lui-même ne peut structurellement pas en contenir un.
        var detail = $"{ex.GetType().Name}: {ex.Message}";

        if (human)
        {
            Console.Error.WriteLine($"ERREUR [internal_error] erreur interne inattendue — {detail}");
            return;
        }

        var envelope = new
        {
            schema_version = CliContractVersion.Current,
            operation,
            success = false,
            errors = new[] { new { code = "internal_error", message = "erreur interne inattendue", detail } },
            warnings = Array.Empty<object>(),
        };
        Console.WriteLine(JsonSerializer.Serialize(envelope, JsonOptions));
    }
}
