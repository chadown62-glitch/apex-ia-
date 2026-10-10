using System.Text.Json;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Sentinelle de l'affichage intégré (0.4.3). Le composant Microsoft Edge WebView2 est créé DANS le processus du
/// Panel : s'il plante ou bloque la fenêtre, le Panel ne peut pas le dire lui-même. Constaté sur Windows réel
/// (10/10/2026, 0.4.2, composant présent) : « je double-clique sur l'icône et rien ne se passe ».
/// <para>Avant la création : une marque (processus, version, heure) est écrite. Elle est retirée dès que le composant
/// a affiché une page, qu'il a échoué proprement (raison connue, déjà affichée), ou que le Panel se ferme
/// normalement. Une marque laissée par un AUTRE processus veut donc dire : le lancement précédent s'est arrêté ou
/// bloqué pendant la création. Le Panel passe alors l'assistant au navigateur, le dit, et propose de réessayer.</para>
/// </summary>
public sealed class EmbeddedDisplayGuard(string file)
{
    private sealed record Attempt(int Pid, string Version, DateTimeOffset StartedAt);

    public static EmbeddedDisplayGuard Default() => new(Path.Combine(NativeRuntime.DefaultHome(), "webview-attempt.json"));

    public string File => file;

    /// <summary>La raison, si une tentative d'un autre processus n'a jamais abouti ; sinon <c>null</c>.</summary>
    public string? Interrupted()
    {
        Attempt? attempt;
        try
        {
            if (!System.IO.File.Exists(file))
            {
                return null;
            }
            attempt = JsonSerializer.Deserialize<Attempt>(System.IO.File.ReadAllText(file));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return null;
        }
        if (attempt is null || attempt.Pid == Environment.ProcessId)
        {
            return null;
        }
        return $"au lancement précédent ({attempt.StartedAt.ToLocalTime():dd/MM/yyyy HH:mm}, APEX.exe {attempt.Version}), "
               + "la fenêtre s'est fermée ou bloquée pendant la création du composant d'affichage Microsoft Edge WebView2";
    }

    public void Begin(string version, int? pid = null) => Write(new Attempt(pid ?? Environment.ProcessId, version, DateTimeOffset.Now));

    public void End()
    {
        try
        {
            System.IO.File.Delete(file);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Au pire, la prochaine ouverture proposera le navigateur et « Réessayer dans la fenêtre ».
        }
    }

    private void Write(Attempt attempt)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            System.IO.File.WriteAllText(file, JsonSerializer.Serialize(attempt));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Sans marque, seule la détection d'un plantage au prochain lancement est perdue.
        }
    }
}
