using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace ApexSetup.Dashboard.Services;

public sealed record PrepareResult(bool Ok, string Message);

/// <summary>
/// « Préparer APEX » (premier lancement, ou après une mise à jour) — sur un geste de l'utilisateur, jamais seul :
/// <list type="number">
/// <item>Python officiel (paquet NuGet « python » de python.org) téléchargé puis VÉRIFIÉ contre l'empreinte SHA-256
/// figée dans l'installateur — un fichier différent est effacé, jamais exécuté ;</item>
/// <item>bibliothèques d'APEX installées par pip en mode <c>--require-hashes</c> depuis la liste figée
/// (<c>requirements-windows.lock</c>) : chaque fichier téléchargé doit avoir l'empreinte attendue ;</item>
/// <item>lien vers l'application installée (<c>.pth</c>), puis VÉRIFICATION réelle : ce Python importe APEX et ses
/// bibliothèques ;</item>
/// <item>marqueur « prêt » écrit en dernier : une préparation interrompue est reprise, jamais crue terminée.</item>
/// </list>
/// Tout est journalisé dans <c>%LOCALAPPDATA%\APEX\.apex\runtime\prepare.log</c>. Chaque étape déjà faite est sautée.
/// </summary>
public sealed class RuntimePreparer(NativeRuntime runtime, HttpClient http, IProcessRunner runner)
{
    public static readonly TimeSpan PipTimeout = TimeSpan.FromMinutes(90);
    public static readonly TimeSpan VerifyTimeout = TimeSpan.FromMinutes(5);
    public static readonly TimeSpan ModelTimeout = TimeSpan.FromHours(6);

    /// <summary>Ce que la vérification importe : le lanceur d'APEX et chaque bibliothèque dont il a besoin.</summary>
    public const string VerifyScript =
        "import apex.standalone, apex.api.app, fastapi, uvicorn, sqlalchemy, alembic, yaml, torch, transformers, safetensors, huggingface_hub; "
        + "print('apex', apex.__version__, 'torch', torch.__version__)";

    public async Task<PrepareResult> PrepareAsync(IProgress<string>? progress, CancellationToken cancellationToken = default)
    {
        var spec = runtime.TryReadSpec(out var problem);
        if (spec is null)
        {
            return new PrepareResult(false, problem + " — réinstallez APEX.");
        }
        if (runtime.ExternalPython)
        {
            return new PrepareResult(true, "Python imposé (APEX_PYTHON) : rien à préparer.");
        }
        Directory.CreateDirectory(Path.GetDirectoryName(runtime.RuntimeLog)!);
        await using var log = new StreamWriter(runtime.RuntimeLog, append: true, new UTF8Encoding(false)) { AutoFlush = true };
        void Step(string message)
        {
            log.WriteLine($"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss}] {message}");
            progress?.Report(message);
        }

        var python = runtime.PythonExecutable(spec);
        Step($"Préparation d'APEX : Python {spec.PythonVersion}, bibliothèques ({runtime.RequirementsFile(spec)}), application {runtime.AppRoot}.");
        try
        {
            if (!File.Exists(python))
            {
                Directory.CreateDirectory(runtime.DownloadDirectory);
                var package = Path.Combine(runtime.DownloadDirectory, $"python.{spec.PythonVersion}.nupkg");
                Step($"Téléchargement de Python {spec.PythonVersion} ({spec.PythonBytes / 1_000_000.0:0.#} Mo)…");
                await DownloadVerifiedAsync(spec.PythonUrl, package, spec.PythonSha256, spec.PythonBytes, Step, cancellationToken).ConfigureAwait(false);
                Step("Empreinte SHA-256 du paquet Python vérifiée. Installation…");
                var root = runtime.PythonRoot(spec);
                var staging = root + ".partial";
                if (Directory.Exists(staging))
                {
                    Directory.Delete(staging, recursive: true);
                }
                ExtractTools(package, staging);
                if (Directory.Exists(root))
                {
                    Directory.Delete(root, recursive: true); // dossier sans python.exe : reste d'une version incomplète
                }
                Directory.Move(staging, root);
                File.Delete(package);
                if (!File.Exists(python))
                {
                    return Fail(Step, $"le paquet Python ne contient pas python.exe ({python}).");
                }
            }

            Step($"Installation des bibliothèques d'APEX (environ {spec.DownloadEstimateMb} Mo depuis PyPI, empreintes vérifiées) — plusieurs minutes…");
            var pip = await runner.RunAsync(python,
                ["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-warn-script-location",
                 "--require-hashes", "--only-binary=:all:", "--no-deps", "-r", runtime.RequirementsFile(spec)],
                PipTimeout, cancellationToken).ConfigureAwait(false);
            log.WriteLine(pip.Stdout);
            log.WriteLine(pip.Stderr);
            if (!pip.Started || pip.TimedOut || pip.ExitCode != 0)
            {
                var why = !pip.Started ? $"pip n'a pas pu être lancé ({pip.FailureReason})"
                    : pip.TimedOut ? "pip n'a pas terminé dans le délai"
                    : $"pip a échoué (code {pip.ExitCode}) : {LastLines(pip.Stderr + "\n" + pip.Stdout, 4)}";
                return Fail(Step, why + ". Vérifiez la connexion Internet puis relancez « Préparer APEX » (ce qui est déjà installé est conservé).");
            }

            Directory.CreateDirectory(Path.GetDirectoryName(runtime.PthFile(spec))!);
            await File.WriteAllTextAsync(runtime.PthFile(spec), runtime.AppRoot + Environment.NewLine, cancellationToken).ConfigureAwait(false);

            Step("Vérification : ce Python importe APEX et ses bibliothèques…");
            var verify = await runner.RunAsync(python, ["-c", VerifyScript], VerifyTimeout, cancellationToken).ConfigureAwait(false);
            log.WriteLine(verify.Stdout);
            log.WriteLine(verify.Stderr);
            if (!verify.Started || verify.TimedOut || verify.ExitCode != 0)
            {
                var text = verify.Stderr + verify.Stdout;
                var hint = text.Contains("WinError 126", StringComparison.Ordinal) || text.Contains("DLL", StringComparison.Ordinal)
                    ? " Une bibliothèque système manque probablement : installez « Microsoft Visual C++ Redistributable » (x64) depuis https://aka.ms/vs/17/release/vc_redist.x64.exe, puis relancez « Préparer APEX »."
                    : " Relancez « Préparer APEX » ; si l'échec persiste, joignez prepare.log au diagnostic.";
                return Fail(Step, $"la vérification a échoué : {LastLines(text, 3)}.{hint}");
            }

            var marker = JsonSerializer.Serialize(new Dictionary<string, string>
            {
                ["python_version"] = spec.PythonVersion,
                ["requirements_sha256"] = NativeRuntime.Sha256File(runtime.RequirementsFile(spec)),
                ["app_root"] = runtime.AppRoot,
                ["prepared_at"] = DateTimeOffset.Now.ToString("o"),
                ["verified"] = LastLines(verify.Stdout, 1),
            });
            await File.WriteAllTextAsync(runtime.ReadyMarker(spec), marker, cancellationToken).ConfigureAwait(false);
            Step($"APEX est prêt sur ce PC ({LastLines(verify.Stdout, 1)}).");
            return new PrepareResult(true, "APEX est prêt : cliquez sur « Démarrer APEX ».");
        }
        catch (OperationCanceledException)
        {
            return Fail(Step, "préparation annulée (ce qui est déjà installé est conservé).");
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or UnauthorizedAccessException or InvalidDataException)
        {
            return Fail(Step, $"{ex.Message} Vérifiez la connexion Internet et l'espace disque, puis relancez « Préparer APEX ».");
        }
    }

    /// <summary>Le modèle par défaut (Hugging Face, révision figée) — par le lanceur d'APEX lui-même, journal dans <c>.apex\runtime\model-install.log</c>.</summary>
    public Task<ProcessRunResult> InstallModelAsync(CancellationToken cancellationToken = default)
    {
        var spec = runtime.TryReadSpec(out var problem);
        return spec is null
            ? Task.FromResult(ProcessRunResult.NotStarted(problem))
            : runner.RunAsync(runtime.PythonExecutable(spec), ["-m", "apex.standalone", "model", "install", "--log"], ModelTimeout, cancellationToken);
    }

    private static PrepareResult Fail(Action<string> step, string message)
    {
        step("ÉCHEC : " + message);
        return new PrepareResult(false, "Préparation impossible : " + message);
    }

    /// <summary>Téléchargement en flux, empreinte calculée au passage ; un fichier à l'empreinte inattendue est effacé.</summary>
    internal async Task DownloadVerifiedAsync(
        string url, string destination, string expectedSha256, long expectedBytes, Action<string> step, CancellationToken cancellationToken)
    {
        var partial = destination + ".part";
        using (var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false))
        {
            if (!response.IsSuccessStatusCode)
            {
                throw new HttpRequestException($"téléchargement refusé ({url} : HTTP {(int)response.StatusCode}).");
            }
            await using var source = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            await using var target = File.Create(partial);
            using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var buffer = new byte[1 << 16];
            long total = 0;
            var reported = 0;
            int read;
            while ((read = await source.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
            {
                hash.AppendData(buffer, 0, read);
                await target.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
                total += read;
                var percent = expectedBytes > 0 ? (int)(total * 100 / expectedBytes) : 0;
                if (percent >= reported + 25 && percent < 100)
                {
                    reported = percent - percent % 25;
                    step($"  … {reported} %");
                }
            }
            var actual = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
            if (actual != expectedSha256)
            {
                target.Close();
                File.Delete(partial);
                throw new InvalidDataException($"empreinte inattendue pour {url} (obtenue {actual}, attendue {expectedSha256}) : fichier effacé, jamais exécuté.");
            }
        }
        File.Move(partial, destination, overwrite: true);
    }

    /// <summary>Extrait le dossier <c>tools/</c> du paquet NuGet (Python complet) ; tout chemin qui sortirait de la destination est refusé.</summary>
    internal static void ExtractTools(string package, string destination)
    {
        var root = Path.GetFullPath(destination) + Path.DirectorySeparatorChar;
        using var archive = ZipFile.OpenRead(package);
        var extracted = 0;
        foreach (var entry in archive.Entries)
        {
            if (!entry.FullName.StartsWith("tools/", StringComparison.Ordinal) || entry.FullName.EndsWith('/'))
            {
                continue;
            }
            var target = Path.GetFullPath(Path.Combine(root, entry.FullName["tools/".Length..]));
            if (!target.StartsWith(root, StringComparison.Ordinal))
            {
                throw new InvalidDataException($"chemin refusé dans le paquet Python : {entry.FullName}");
            }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            entry.ExtractToFile(target, overwrite: true);
            extracted++;
        }
        if (extracted == 0)
        {
            throw new InvalidDataException("paquet Python vide (aucun fichier sous tools/).");
        }
    }

    internal static string LastLines(string text, int count) =>
        string.Join(" | ", text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).TakeLast(count));
}
