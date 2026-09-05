using System.Text.Json;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Json;

namespace ApexSetup.Core.RuntimeAdapter;

/// <summary>
/// Point d'entrée UNIQUE de Setup vers le Runtime — exclusivement
/// sous-processus + CLI + JSON (jamais un import Python direct, jamais une
/// interprétation du texte humain). Générique sur la commande de base pour
/// rester testable (voir <c>RuntimeAdapterTests</c>, qui pointe vers un
/// interpréteur Python réel exécutant le CLI Runtime, et vers de faux
/// scripts pour les cas négatifs) et pour permettre en production un appel
/// via <c>wsl.exe -- apex ...</c> sans changer ce code.
/// </summary>
public sealed class RuntimeAdapter(
    IRuntimeProcessRunner processRunner,
    string executable,
    IReadOnlyList<string> baseArguments,
    TimeSpan? timeout = null)
{
    private readonly TimeSpan _timeout = timeout ?? TimeSpan.FromSeconds(30);

    private async Task<RuntimeCallResult<T>> InvokeAsync<T>(
        IReadOnlyList<string> commandArguments,
        Func<JsonElement, T> parse,
        CancellationToken cancellationToken)
    {
        var arguments = baseArguments.Concat(commandArguments).ToList();
        var invocation = await processRunner.RunAsync(executable, arguments, _timeout, cancellationToken)
            .ConfigureAwait(false);

        if (!invocation.ProcessStarted)
        {
            return RuntimeCallResult<T>.Failure(
                RuntimeCallOutcome.ProcessNotFound,
                invocation.StartFailureMessage ?? "le processus Runtime n'a pas pu démarrer");
        }

        if (invocation.TimedOut)
        {
            return RuntimeCallResult<T>.Failure(
                RuntimeCallOutcome.Timeout,
                $"le Runtime n'a pas répondu dans le délai imparti ({_timeout.TotalSeconds:0}s)");
        }

        if (invocation.ExitCode != 0)
        {
            // Un exit code non nul du Doctor/Gate est un ÉTAT MESURÉ légitime
            // (ex. Doctor renvoie 1 si le pire statut est FAIL — voir
            // apex.doctor.runner.DoctorReport.exit_code côté Runtime) ; ce
            // n'est PAS en soi une erreur de contrat. On tente donc quand
            // même de parser le JSON avant de conclure à un échec brut.
            if (string.IsNullOrWhiteSpace(invocation.Stdout))
            {
                return RuntimeCallResult<T>.Failure(
                    RuntimeCallOutcome.NonZeroExitCode,
                    $"code de sortie {invocation.ExitCode}, stdout vide",
                    stderr: invocation.Stderr, exitCode: invocation.ExitCode);
            }
        }

        if (string.IsNullOrWhiteSpace(invocation.Stdout))
        {
            return RuntimeCallResult<T>.Failure(
                RuntimeCallOutcome.EmptyStdout,
                "aucune sortie standard reçue du Runtime",
                stderr: invocation.Stderr, exitCode: invocation.ExitCode);
        }

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(invocation.Stdout);
        }
        catch (JsonException ex)
        {
            return RuntimeCallResult<T>.Failure(
                RuntimeCallOutcome.InvalidJson,
                $"sortie non-JSON reçue du Runtime : {ex.Message}",
                invocation.Stdout, invocation.Stderr, invocation.ExitCode);
        }

        using (document)
        {
            try
            {
                var value = parse(document.RootElement);
                return RuntimeCallResult<T>.Success(value, invocation.Stdout, invocation.ExitCode!.Value);
            }
            catch (ContractValidationException ex)
            {
                return RuntimeCallResult<T>.Failure(
                    ex.Outcome, ex.Message, invocation.Stdout, invocation.Stderr, invocation.ExitCode);
            }
        }
    }

    public Task<RuntimeCallResult<RuntimeVersionResult>> GetVersionAsync(CancellationToken cancellationToken = default) =>
        InvokeAsync(["version", "--json"], RuntimeContractParser.ParseVersion, cancellationToken);

    public Task<RuntimeCallResult<RuntimeDoctorResult>> GetDoctorAsync(CancellationToken cancellationToken = default) =>
        InvokeAsync(["doctor", "--json"], RuntimeContractParser.ParseDoctor, cancellationToken);

    public Task<RuntimeCallResult<RuntimeGateResult>> GetGateAsync(CancellationToken cancellationToken = default) =>
        InvokeAsync(["gate", "--json"], RuntimeContractParser.ParseGate, cancellationToken);
}
