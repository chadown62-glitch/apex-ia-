using ApexSetup.Application.Contracts;

namespace ApexSetup.Cli;

/// <summary>
/// Rendu humain OPTIONNEL (<c>--human</c>) — lit exactement les mêmes
/// objets de réponse que le rendu JSON, jamais une seconde exécution de
/// logique métier (voir §1 : « le CLI ne doit contenir aucune logique
/// métier importante »). Un GUI n'appelle jamais ce code — il consomme
/// uniquement le JSON structuré.
/// </summary>
internal static class HumanRenderer
{
    public static void Render(VersionResponse r)
    {
        Console.WriteLine($"Setup CLI: {r.SetupVersion}");
        if (r.Runtime is { } rt)
        {
            Console.WriteLine($"Runtime  : {rt.Version} (phase {rt.Phase} — {rt.PhaseName}) — Python {rt.Python} sur {rt.Platform}");
        }
        else
        {
            Console.WriteLine("Runtime  : injoignable");
        }
        PrintWarnings(r.Warnings);
    }

    public static void Render(DetectResponse r)
    {
        Console.WriteLine($"OS               : {r.Os} ({r.Architecture})");
        Console.WriteLine($"RAM              : {r.RamTotalGb:0.0} Go");
        Console.WriteLine($"CPU threads      : {r.CpuThreads}");
        Console.WriteLine($"GPU              : {r.Gpu ?? "non détecté"}");
        Console.WriteLine($"VRAM             : {(r.VramTotalGb is { } v ? $"{v:0.0} Go" : "non détectée")}");
        Console.WriteLine($"Disque libre     : {r.DiskFreeGb:0.0} Go");
        Console.WriteLine($"Profil recommandé: {r.RecommendedProfile}  ({r.ProfileNote})");
        PrintErrors(r.Errors);
    }

    public static void Render(CheckResponse r)
    {
        foreach (var c in r.Checks)
        {
            var mark = c.Passed ? "PASS" : (c.Blocking ? "FAIL" : "WARN");
            Console.WriteLine($"[{mark,4}] {c.Name} — {c.Detail}");
        }
        Console.WriteLine(r.HasBlockingFailure ? "Résultat : BLOQUANT" : "Résultat : OK");
    }

    public static void Render(PlanResponse r)
    {
        if (r.Plan is null)
        {
            PrintErrors(r.Errors);
            return;
        }
        Console.WriteLine($"Profil            : {r.Plan.SelectedProfile}");
        Console.WriteLine($"Destination       : {r.Plan.Destination}");
        Console.WriteLine($"Artefacts requis  : {r.Plan.RequiredArtifacts.Count} ({r.Plan.RequiredDiskBytes:N0} octets)");
        Console.WriteLine($"Empreinte du plan : {r.PlanFingerprint}");
        foreach (var step in r.Plan.Steps.OrderBy(s => s.Order))
        {
            Console.WriteLine($"  {step.Order}. {step.Id} — {step.Title}");
        }
    }

    public static void Render(InstallResponse r)
    {
        Console.WriteLine($"Étapes exécutées : {string.Join(" -> ", r.ExecutedSteps)}");
        if (r.Committed)
        {
            Console.WriteLine("Résultat : INSTALLATION COMMITÉE");
        }
        else
        {
            Console.WriteLine($"Résultat : ÉCHEC (étape « {r.FailedStep ?? "?"} » — {r.FailureDetail})");
            if (r.RolledBackSteps.Count > 0)
            {
                Console.WriteLine($"Rollback effectué pour : {string.Join(", ", r.RolledBackSteps)}");
            }
            if (r.FailedRollbackSteps.Count > 0)
            {
                Console.WriteLine($"ROLLBACK EN ÉCHEC pour : {string.Join(", ", r.FailedRollbackSteps)}");
            }
        }
        PrintErrors(r.Errors);
    }

    public static void Render(RecoverResponse r)
    {
        Console.WriteLine($"État détecté : {r.StateDetected}");
        Console.WriteLine($"Action       : {r.ActionTaken}");
        Console.WriteLine($"Résultat     : {r.Result}");
        Console.WriteLine($"Détail       : {r.Detail}");
        PrintErrors(r.Errors);
    }

    public static void Render(ValidateResponse r)
    {
        Console.WriteLine($"Runtime joignable : {(r.RuntimeReachable ? "oui" : "non")}");
        if (r.Version is { } v)
        {
            Console.WriteLine($"Version : {v.Version}");
        }
        if (r.Doctor is { } d)
        {
            Console.WriteLine($"Doctor  : {d.Status} ({d.ResultsCount} vérification(s))");
        }
        if (r.Gate is { } g)
        {
            Console.WriteLine($"Gate    : {g.Status} (phase2_authorized={g.Phase2Authorized})");
        }
        Console.WriteLine(r.Success ? "Résultat global : OK" : "Résultat global : ÉCHEC");
        PrintErrors(r.Errors);
    }

    public static void Render(StatusResponse r)
    {
        Console.WriteLine($"État              : {r.State}");
        Console.WriteLine($"Verrou actuel     : {(r.LockCurrentlyHeld ? "tenu par une autre exécution" : "libre")}");
        Console.WriteLine($"Détail            : {r.Detail}");
        if (r.PersistedState is { } s)
        {
            Console.WriteLine($"Étapes complétées : {string.Join(", ", s.CompletedStageNames)} (phase={s.Phase})");
        }
    }

    private static void PrintErrors(IReadOnlyList<CliError> errors)
    {
        foreach (var e in errors)
        {
            Console.Error.WriteLine($"ERREUR [{e.Code}] {e.Message}{(e.Detail is null ? "" : $" — {e.Detail}")}");
        }
    }

    private static void PrintWarnings(IReadOnlyList<CliWarning> warnings)
    {
        foreach (var w in warnings)
        {
            Console.Error.WriteLine($"AVERTISSEMENT [{w.Code}] {w.Message}");
        }
    }
}
