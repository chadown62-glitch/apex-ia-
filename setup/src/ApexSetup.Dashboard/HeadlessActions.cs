using System.Text.Json;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard;

/// <summary>
/// <c>APEX.exe --start</c> / <c>--stop</c> / <c>--ui-url</c> : les boutons « Démarrer APEX »,
/// « Arrêter APEX », « Ouvrir APEX » sans fenêtre — exactement les mêmes services (aucun second
/// chemin), pour l'automatisation et la validation sur un Windows réel. Sortie JSON du verdict final
/// (même format que <c>--status-json</c>) ; code de sortie = verdict (<see cref="Program.ExitCode"/>).
/// </summary>
public static class HeadlessActions
{
    public static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(3);

    /// <summary>Démarre APEX s'il ne tourne pas, puis attend un verdict établi : READY, DEGRADED, BLOCKED, échec, ou le délai.</summary>
    public static async Task<int> StartAsync()
    {
        var composition = PanelComposition.Create();
        var settings = (await composition.Settings.LoadAsync().ConfigureAwait(false)).Settings;
        var snapshot = await composition.Status.MeasureAsync(settings).ConfigureAwait(false);
        if (snapshot.Verdict is PanelVerdict.Ready or PanelVerdict.Degraded)
        {
            composition.Log.Log($"[--start] APEX tourne déjà ({snapshot.Headline}) : rien n'est relancé.");
            return Emit(composition, snapshot);
        }
        var (root, source) = settings.EffectiveProjectRoot();
        var distro = string.IsNullOrWhiteSpace(settings.WslDistro) ? null : settings.WslDistro.Trim();
        composition.Log.Log($"[--start] démarrage d'APEX — dossier « {root} » ({source}) : launch.sh {BackendLauncher.StartCommand}.");
        var (attempt, exited) = composition.Control.Launcher.Start(distro, root, DateTimeOffset.Now);
        var limit = TimeSpan.FromSeconds(int.TryParse(Environment.GetEnvironmentVariable("APEX_PANEL_START_TIMEOUT_S"), out var s) && s > 0 ? s : 900);
        var started = DateTimeOffset.Now;
        while (true)
        {
            if (attempt.Running && exited.IsCompleted)
            {
                attempt = attempt with { ExitCode = exited.Result };
                composition.Log.Log($"[--start] launch.sh s'est terminé (code {attempt.ExitCode}).");
            }
            snapshot = await composition.Status.MeasureAsync(settings, launch: attempt).ConfigureAwait(false);
            var elapsed = DateTimeOffset.Now - started;
            var settled = snapshot.Verdict is PanelVerdict.Ready or PanelVerdict.Degraded or PanelVerdict.Blocked or PanelVerdict.VersionMismatch
                || snapshot.Cause == NotReadyCause.StartFailed
                || (!attempt.Running && snapshot.Cause != NotReadyCause.BackendStarting && elapsed > PollInterval * 3);
            if (settled || elapsed > limit)
            {
                break;
            }
            await Task.Delay(PollInterval).ConfigureAwait(false);
        }
        composition.Log.Log($"[--start] {snapshot.Headline} ({snapshot.Cause}).");
        await RememberAsync(composition, settings).ConfigureAwait(false);
        return Emit(composition, snapshot);
    }

    /// <summary>Arrête APEX (launch.sh stop) puis rend le verdict mesuré ensuite ; code = celui de launch.sh stop.</summary>
    public static async Task<int> StopAsync()
    {
        var composition = PanelComposition.Create();
        var settings = (await composition.Settings.LoadAsync().ConfigureAwait(false)).Settings;
        await composition.Status.MeasureAsync(settings).ConfigureAwait(false);
        var access = composition.Status.LastAccess;
        var root = access?.ProjectRoot ?? settings.EffectiveProjectRoot().Path;
        var distro = access?.Distro ?? (string.IsNullOrWhiteSpace(settings.WslDistro) ? null : settings.WslDistro.Trim());
        composition.Log.Log($"[--stop] arrêt d'APEX — dossier « {root} » : launch.sh {BackendLauncher.StopCommand}.");
        var result = await composition.Control.Launcher.StopAsync(distro, root).ConfigureAwait(false);
        Console.Error.WriteLine(composition.Redactor.Redact((result.Stderr + result.Stdout).Trim()));
        var snapshot = await composition.Status.MeasureAsync(settings).ConfigureAwait(false);
        composition.Log.Log($"[--stop] launch.sh stop : code {result.ExitCode?.ToString() ?? "—"} ; ensuite : {snapshot.Headline} ({snapshot.Cause}).");
        Emit(composition, snapshot);
        return !result.Started ? 98 : result.TimedOut ? 97 : result.ExitCode ?? 98;
    }

    /// <summary>Écrit sur la sortie standard une adresse d'ouverture À USAGE UNIQUE de l'interface (60 s).</summary>
    public static async Task<int> UiUrlAsync()
    {
        var composition = PanelComposition.Create();
        var settings = (await composition.Settings.LoadAsync().ConfigureAwait(false)).Settings;
        var snapshot = await composition.Status.MeasureAsync(settings).ConfigureAwait(false);
        if (composition.Status.LastAccess is not { } access)
        {
            Console.Error.WriteLine($"APEX ne répond pas avec son jeton ({snapshot.Headline} : {snapshot.Cause}) : rien à ouvrir.");
            return 2;
        }
        var launch = await composition.Control.Client.CreateLaunchCodeAsync(access.Address, access.Port, access.Token).ConfigureAwait(false);
        if (!launch.Ok || launch.Url is null)
        {
            Console.Error.WriteLine($"ouverture impossible : {launch.Detail}");
            return 2;
        }
        if (!launch.UiAvailable)
        {
            Console.Error.WriteLine($"interface non construite : {launch.Detail}");
        }
        Console.WriteLine(launch.Url);
        return 0;
    }

    private static int Emit(PanelComposition composition, PanelSnapshot snapshot)
    {
        Program.Emit(composition.Redactor.Redact(JsonSerializer.Serialize(snapshot, Program.JsonOutput)),
            Console.IsOutputRedirected, Console.OpenStandardOutput, Console.Out);
        return Program.ExitCode(snapshot.Verdict);
    }

    private static async Task RememberAsync(PanelComposition composition, DashboardSettings settings)
    {
        if (composition.Status.LastAccess?.ProjectRoot is { } verified && verified != settings.LastProjectRoot)
        {
            try
            {
                await composition.Settings.SaveAsync(settings with { LastProjectRoot = verified }).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                composition.Log.Log($"[--start] dossier APEX non mémorisé ({ex.GetType().Name}).");
            }
        }
    }
}
