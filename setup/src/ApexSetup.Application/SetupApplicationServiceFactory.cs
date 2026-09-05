using ApexSetup.Application.Hardware;
using ApexSetup.Core.Preflight;
using CoreRuntimeAdapter = ApexSetup.Core.RuntimeAdapter.RuntimeAdapter;
using CoreRuntimeProcessRunner = ApexSetup.Core.RuntimeAdapter.RuntimeProcessRunner;

namespace ApexSetup.Application;

/// <summary>
/// PHASE 4 : racine de composition PARTAGÉE entre toutes les façades
/// (CLI, GUI, tout futur consommateur). Avant cette classe, la
/// construction d'un <see cref="SetupApplicationService"/> (quel
/// <see cref="Core.RuntimeAdapter.RuntimeAdapter"/>, quelle sonde
/// d'environnement, quel détecteur matériel réel) n'existait que dans
/// <c>ApexSetup.Cli.Program.BuildService</c> — un futur GUI aurait dû soit
/// la dupliquer (deux endroits à maintenir en synchronisation), soit
/// dépendre directement de types internes (<c>RuntimeProcessRunner</c>,
/// <c>RealEnvironmentProcess</c>...) qu'un GUI ne doit justement jamais
/// connaître (voir §« Frontière GUI/Core »). Ce n'est PAS de la logique
/// métier — uniquement l'assemblage des implémentations réelles, la même
/// chose qu'un `Program.cs` fait pour n'importe quelle application .NET.
/// </summary>
public static class SetupApplicationServiceFactory
{
    public const string DefaultRuntimeExecutable = "apex";
    public static readonly TimeSpan DefaultRuntimeTimeout = TimeSpan.FromSeconds(30);

    public static SetupApplicationService Create(
        string? runtimeExecutable = null,
        IReadOnlyList<string>? runtimeArgs = null,
        TimeSpan? runtimeTimeout = null,
        string? setupVersion = null)
    {
        var executable = runtimeExecutable ?? DefaultRuntimeExecutable;
        if (string.IsNullOrWhiteSpace(executable))
        {
            // Même règle que CliArguments.Require côté CLI (revue
            // adversariale Phase 3) : une valeur vide n'a de sens pour
            // aucun appelant, jamais laissée atteindre Process.Start.
            throw new ArgumentException("« runtimeExecutable » ne peut pas être vide", nameof(runtimeExecutable));
        }

        var processRunner = new CoreRuntimeProcessRunner();
        var runtimeAdapter = new CoreRuntimeAdapter(
            processRunner, executable, runtimeArgs ?? [], runtimeTimeout ?? DefaultRuntimeTimeout);
        var environmentProbe = new RealEnvironmentProbe();
        var hardwareDetector = new RealHardwareDetector();
        var httpClient = new HttpClient();
        var version = setupVersion ?? typeof(SetupApplicationServiceFactory).Assembly.GetName().Version?.ToString() ?? "0.0.0";

        return new SetupApplicationService(runtimeAdapter, environmentProbe, hardwareDetector, httpClient, version);
    }
}
