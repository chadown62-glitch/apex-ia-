using System.Security.Cryptography;
using System.Text.Json;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Ce que l'installateur a déposé dans <c>app\runtime\python-runtime.json</c> : QUEL Python télécharger (adresse
/// et empreinte figées), la liste figée des bibliothèques, et le modèle par défaut. Lu strictement : un champ
/// absent ou mal formé rend la préparation impossible, jamais « à peu près bonne ».
/// </summary>
public sealed record RuntimeSpec(
    string PythonVersion, string PythonUrl, string PythonSha256, long PythonBytes,
    string Requirements, int DownloadEstimateMb,
    string ModelName, string ModelRepo, string ModelRevision, int ModelEstimateMb)
{
    public static RuntimeSpec Parse(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;
        var python = root.GetProperty("python");
        var model = root.GetProperty("model");
        var spec = new RuntimeSpec(
            Str(python, "version"), Str(python, "url"), Str(python, "sha256").ToLowerInvariant(), python.GetProperty("bytes").GetInt64(),
            Str(root, "requirements"), root.GetProperty("download_estimate_mb").GetInt32(),
            Str(model, "name"), Str(model, "repo"), Str(model, "revision"), model.GetProperty("download_estimate_mb").GetInt32());
        if (!System.Text.RegularExpressions.Regex.IsMatch(spec.PythonVersion, @"^\d+\.\d+\.\d+$")
            || !spec.PythonUrl.StartsWith("https://", StringComparison.Ordinal)
            || spec.PythonSha256.Length != 64 || !spec.PythonSha256.All(Uri.IsHexDigit)
            || !HostPaths.IsSafeRelative(spec.Requirements) || !HostPaths.IsSafeRelative(spec.ModelName))
        {
            throw new FormatException("python-runtime.json : valeurs hors du format attendu");
        }
        return spec;
    }

    private static string Str(JsonElement element, string name) =>
        element.GetProperty(name).GetString() is { Length: > 0 } value ? value : throw new FormatException(name);
}

public enum RuntimeState
{
    /// <summary>Aucun Python d'APEX sur ce poste : « Préparer APEX ».</summary>
    NotPrepared,
    /// <summary>Préparé pour une autre version d'APEX (mise à jour) ou préparation interrompue : « Préparer APEX » la complète.</summary>
    Outdated,
    Ready,
    /// <summary>L'application installée est incomplète (fichier de l'installateur absent ou illisible) : réinstaller.</summary>
    Broken,
}

/// <summary>Ce que le Panel sait du runtime APEX de ce poste — toujours constaté sur le disque, jamais supposé.</summary>
public sealed record RuntimeInspection(RuntimeState State, string Detail, bool ModelInstalled, string ModelDetail);

/// <summary>
/// APEX AUTONOME sur ce poste (mode « Windows natif », sans WSL ni Docker) : où est l'application installée, où
/// vivent les données, quel Python l'exécute. Rien n'est lu ailleurs que dans ces dossiers :
/// <list type="bullet">
/// <item><c>AppRoot</c> = <c>&lt;installation&gt;\app</c> — l'artefact APEX Runtime (paquet <c>apex</c>, interface,
/// <c>config.standalone.yaml</c>, <c>runtime\python-runtime.json</c>, <c>runtime\requirements-windows.lock</c>) ;</item>
/// <item><c>Home</c> = <c>%LOCALAPPDATA%\APEX</c> (<c>APEX_HOME</c>) — données, modèles, Python préparé, et les
/// fichiers que lit le Panel aux mêmes chemins relatifs que dans WSL (<c>.apex\runtime\backend.json</c>, jeton) ;</item>
/// <item>Python = <c>Home\python\&lt;version&gt;\python.exe</c>, préparé par <see cref="RuntimePreparer"/>.</item>
/// </list>
/// </summary>
public sealed class NativeRuntime
{
    public const string AppFolder = "app";
    public const string SpecRelativePath = "runtime/python-runtime.json";
    public const string ReadyMarkerName = "apex-runtime.json";
    public const string PthName = "apex-app.pth";

    private readonly string? _pythonOverride;

    public NativeRuntime(string appRoot, string home, string? pythonOverride = null)
    {
        AppRoot = Path.GetFullPath(appRoot);
        Home = Path.GetFullPath(home);
        _pythonOverride = string.IsNullOrWhiteSpace(pythonOverride) ? null : pythonOverride;
    }

    public string AppRoot { get; }

    public string Home { get; }

    /// <summary>Python imposé (développement, tests sous Linux : <c>APEX_PYTHON</c>) — jamais préparé ni modifié par le Panel.</summary>
    public bool ExternalPython => _pythonOverride is not null;

    public string SpecFile => Path.Combine(AppRoot, SpecRelativePath.Replace('/', Path.DirectorySeparatorChar));

    public string RuntimeLog => Path.Combine(Home, ".apex", "runtime", "prepare.log");

    public string DownloadDirectory => Path.Combine(Home, "downloads");

    public string ModelsDirectory => Path.Combine(Home, "models");

    public string PythonRoot(RuntimeSpec spec) => Path.Combine(Home, "python", spec.PythonVersion);

    public string PythonExecutable(RuntimeSpec? spec) =>
        _pythonOverride ?? (spec is null ? Path.Combine(Home, "python", "python.exe") : Path.Combine(PythonRoot(spec), "python.exe"));

    public string RequirementsFile(RuntimeSpec spec) => Path.Combine(AppRoot, "runtime", spec.Requirements);

    public string ReadyMarker(RuntimeSpec spec) => Path.Combine(PythonRoot(spec), ReadyMarkerName);

    /// <summary>Fichier <c>.pth</c> qui rend le paquet <c>apex</c> de l'installation importable par CE Python, sans PYTHONPATH.</summary>
    public string PthFile(RuntimeSpec spec) => Path.Combine(PythonRoot(spec), "Lib", "site-packages", PthName);

    public RuntimeSpec? TryReadSpec(out string problem)
    {
        try
        {
            var spec = RuntimeSpec.Parse(File.ReadAllText(SpecFile));
            problem = string.Empty;
            return spec;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or FormatException
            or KeyNotFoundException or InvalidOperationException)
        {
            problem = $"description du runtime illisible ({SpecFile}) : {ex.Message}";
            return null;
        }
    }

    /// <summary>Empreinte de la liste figée des bibliothèques : une mise à jour d'APEX qui la change rend le runtime « à compléter ».</summary>
    public static string Sha256File(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    public RuntimeInspection Inspect()
    {
        var spec = TryReadSpec(out var problem);
        if (spec is null)
        {
            return new RuntimeInspection(RuntimeState.Broken, problem + " — réinstallez APEX.", false, "non vérifié");
        }
        var (modelInstalled, modelDetail) = InspectModel(spec);
        if (!File.Exists(Path.Combine(AppRoot, "apex", "standalone", "__init__.py")))
        {
            return new RuntimeInspection(RuntimeState.Broken, $"application APEX incomplète ({AppRoot}) — réinstallez APEX.", modelInstalled, modelDetail);
        }
        if (ExternalPython)
        {
            return File.Exists(_pythonOverride)
                ? new RuntimeInspection(RuntimeState.Ready, $"Python imposé : {_pythonOverride}", modelInstalled, modelDetail)
                : new RuntimeInspection(RuntimeState.Broken, $"Python imposé introuvable : {_pythonOverride}", modelInstalled, modelDetail);
        }
        if (!File.Exists(PythonExecutable(spec)))
        {
            return new RuntimeInspection(RuntimeState.NotPrepared,
                $"non préparé : Python {spec.PythonVersion} et les bibliothèques d'APEX à télécharger (environ {spec.DownloadEstimateMb} Mo)",
                modelInstalled, modelDetail);
        }
        var marker = ReadMarker(spec);
        var lockHash = File.Exists(RequirementsFile(spec)) ? Sha256File(RequirementsFile(spec)) : null;
        if (marker is null || lockHash is null || marker.Value.Requirements != lockHash || !PathsEqual(marker.Value.AppRoot, AppRoot))
        {
            return new RuntimeInspection(RuntimeState.Outdated,
                marker is null
                    ? "préparation incomplète (interrompue ?) : « Préparer APEX » la termine"
                    : "préparé pour une autre version d'APEX : « Préparer APEX » le met à jour",
                modelInstalled, modelDetail);
        }
        return new RuntimeInspection(RuntimeState.Ready, $"Python {spec.PythonVersion} et bibliothèques d'APEX prêts (préparés le {marker.Value.PreparedAt:dd/MM/yyyy})",
            modelInstalled, modelDetail);
    }

    /// <summary>Le modèle par défaut est-il présent ? Poids, configuration et provenance écrits par <c>apex.standalone model install</c>.</summary>
    public (bool Installed, string Detail) InspectModel(RuntimeSpec spec)
    {
        var dir = Path.Combine(ModelsDirectory, spec.ModelName);
        var weights = Directory.Exists(dir) && Directory.EnumerateFiles(dir, "*.safetensors").Any();
        var complete = weights && File.Exists(Path.Combine(dir, "config.json")) && File.Exists(Path.Combine(dir, ".apex_source.json"));
        return complete
            ? (true, $"{spec.ModelName} installé")
            : (false, $"{spec.ModelName} non installé (environ {spec.ModelEstimateMb / 1000.0:0.#} Go à télécharger)");
    }

    internal readonly record struct Marker(string Requirements, string AppRoot, DateTimeOffset PreparedAt);

    internal Marker? ReadMarker(RuntimeSpec spec)
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(ReadyMarker(spec)));
            var root = doc.RootElement;
            return new Marker(root.GetProperty("requirements_sha256").GetString() ?? "", root.GetProperty("app_root").GetString() ?? "",
                DateTimeOffset.Parse(root.GetProperty("prepared_at").GetString() ?? "", System.Globalization.CultureInfo.InvariantCulture));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or FormatException
            or KeyNotFoundException or InvalidOperationException)
        {
            return null;
        }
    }

    private static bool PathsEqual(string a, string b) =>
        string.Equals(Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar), Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar),
            OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal);

    /// <summary>Dossier des données d'APEX autonome — le même que <c>apex.standalone.default_home()</c>.</summary>
    public static string DefaultHome() =>
        Environment.GetEnvironmentVariable("APEX_HOME") is { Length: > 0 } home ? home
        : OperatingSystem.IsWindows()
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "APEX")
            : Path.Combine(Environment.GetEnvironmentVariable("XDG_DATA_HOME") is { Length: > 0 } xdg ? xdg
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share"), "apex");

    /// <summary>
    /// Mode natif si l'installateur a déposé l'application à côté d'APEX.exe (<c>app\apex\standalone</c>) — ou si
    /// <c>APEX_PANEL_MODE=native</c> l'impose (<c>APEX_PANEL_APP_DIR</c>, <c>APEX_PYTHON</c> : développement et
    /// tests). <c>APEX_PANEL_MODE=wsl</c> garde l'ancien mode même si l'application est présente.
    /// </summary>
    public static NativeRuntime? Detect(string installDirectory)
    {
        var mode = Environment.GetEnvironmentVariable("APEX_PANEL_MODE")?.Trim().ToLowerInvariant();
        if (mode is "wsl" or "local")
        {
            return null;
        }
        var appRoot = Environment.GetEnvironmentVariable("APEX_PANEL_APP_DIR") is { Length: > 0 } dir ? dir : Path.Combine(installDirectory, AppFolder);
        var bundled = File.Exists(Path.Combine(appRoot, "apex", "standalone", "__init__.py"));
        if (mode == "native" || (bundled && OperatingSystem.IsWindows()))
        {
            return new NativeRuntime(appRoot, DefaultHome(), Environment.GetEnvironmentVariable("APEX_PYTHON"));
        }
        return null;
    }
}
