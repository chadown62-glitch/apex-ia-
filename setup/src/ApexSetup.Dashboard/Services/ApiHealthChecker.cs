using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

public sealed class ApiHealthChecker(IProcessRunner processRunner, HttpClient httpClient) : IApiHealthChecker
{
    private static readonly TimeSpan TokenReadTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan HttpTimeout = TimeSpan.FromSeconds(5);

    public async Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(settings.ProjectPathWsl))
        {
            return ComponentState.Unknown(ComponentKey.Api, "API APEX", "chemin du projet WSL non configuré — voir Paramètres");
        }

        var (token, notYetCreated) = await ReadApiTokenAsync(settings, cancellationToken).ConfigureAwait(false);
        if (token is null)
        {
            // Distinction réelle (voir ReadApiTokenAsync) entre « pas encore créé »
            // (état transitoire ATTENDU pendant le démarrage du backend — voir
            // apex.api.app._lifespan, qui crée .apex/api-token avant d'accepter des
            // connexions, mais un court délai reste possible pendant le lancement du
            // processus WSL) et un échec de lecture réellement anormal. Jamais le même
            // message : le premier ne doit jamais alarmer inutilement l'utilisateur
            // pendant un démarrage par ailleurs normal, le second doit rester
            // diagnostiquable. Ni l'un ni l'autre ne cite jamais le contenu du jeton
            // (il n'a par définition pas pu être lu).
            var detail = notYetCreated
                ? "jeton API pas encore créé (.apex/api-token) — le backend APEX est probablement en cours de démarrage"
                : "jeton API illisible (.apex/api-token) — vérifier les permissions du fichier et le chemin du projet dans WSL";
            return new ComponentState(ComponentKey.Api, "API APEX", ComponentStatus.Unknown, detail);
        }

        var probe = await HttpProber.ProbeAsync(
            httpClient, $"{settings.ApiBaseUrl}/health", token, HttpTimeout, cancellationToken).ConfigureAwait(false);

        var status = probe.Outcome switch
        {
            HttpProbeOutcome.Ok => ComponentStatus.Running,
            HttpProbeOutcome.ConnectionRefused => ComponentStatus.Stopped,
            HttpProbeOutcome.Timeout => ComponentStatus.Error,
            HttpProbeOutcome.HttpError => ComponentStatus.Error,
            _ => ComponentStatus.Unknown,
        };
        return new ComponentState(ComponentKey.Api, "API APEX", status, probe.Detail);
    }

    /// <summary>
    /// Lit le jeton via <c>cat</c> dans WSL (jamais une seconde copie du
    /// secret côté Windows — voir la mission de sécurité de ce Dashboard).
    /// <paramref name="NotYetCreated"/> distingue le cas ATTENDU « le
    /// fichier n'existe pas encore » (ENOENT, backend en cours de
    /// démarrage — voir <c>apex.api.app._lifespan</c>, qui crée le jeton
    /// avant d'accepter des connexions, mais reste postérieur au lancement
    /// du processus WSL lui-même) de tout autre échec de lecture réel
    /// (permissions, chemin WSL invalide, <c>wsl.exe</c> lui-même en
    /// échec) — jamais confondus dans le diagnostic affiché (voir
    /// <see cref="CheckAsync"/>).
    /// </summary>
    private async Task<(string? Token, bool NotYetCreated)> ReadApiTokenAsync(
        DashboardSettings settings, CancellationToken cancellationToken)
    {
        var args = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        args.AddRange(["--", "cat", $"{settings.ProjectPathWsl}/.apex/api-token"]);
        var result = await processRunner.RunAsync("wsl.exe", args, TokenReadTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started || result.TimedOut)
        {
            return (null, false);
        }
        if (result.ExitCode != 0)
        {
            var notYetCreated = result.Stderr.Contains("No such file", StringComparison.OrdinalIgnoreCase);
            return (null, notYetCreated);
        }
        var token = result.Stdout.Trim();
        return token.Length == 0 ? (null, false) : (token, false);
    }
}
