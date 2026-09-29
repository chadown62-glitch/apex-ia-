using System.Text.RegularExpressions;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Journal du Panel — chaque ligne vient d'une action ou d'une mesure réelle.</summary>
public interface ILogSink
{
    IReadOnlyList<LogEntry> Entries { get; }

    void Log(string message);

    event EventHandler? EntryAdded;
}

/// <summary>Masque les secrets avant toute écriture : les valeurs enregistrées (le jeton lu) et tout en-tête <c>Bearer</c>.</summary>
public sealed partial class Redactor
{
    private readonly HashSet<string> _secrets = [];
    private readonly object _gate = new();

    public void Register(string secret)
    {
        if (secret.Length >= 8)
        {
            lock (_gate)
            {
                _secrets.Add(secret);
            }
        }
    }

    public string Redact(string text)
    {
        lock (_gate)
        {
            foreach (var secret in _secrets)
            {
                text = text.Replace(secret, "[secret masqué]", StringComparison.Ordinal);
            }
        }
        return BearerPattern().Replace(text, "Bearer [secret masqué]");
    }

    [GeneratedRegex(@"Bearer\s+[^\s""']+", RegexOptions.IgnoreCase)]
    private static partial Regex BearerPattern();
}

public sealed class InMemoryLogSink(Redactor? redactor = null) : ILogSink
{
    private readonly List<LogEntry> _entries = [];
    private readonly Redactor _redactor = redactor ?? new Redactor();

    public IReadOnlyList<LogEntry> Entries => _entries;

    public event EventHandler? EntryAdded;

    public void Log(string message)
    {
        _entries.Add(new LogEntry(DateTimeOffset.Now, _redactor.Redact(message)));
        EntryAdded?.Invoke(this, EventArgs.Empty);
    }
}

/// <summary>
/// Journal persistant : un fichier par jour (<c>panel-AAAAMMJJ.log</c>), au plus <see cref="MaxFiles"/>
/// fichiers conservés. Un dossier de journaux inaccessible ne fait jamais planter le Panel : l'erreur
/// est mémorisée (<see cref="FileProblem"/>, affichée dans le diagnostic) et le journal reste en mémoire.
/// </summary>
public sealed class FileLogSink : ILogSink
{
    public const int MaxFiles = 14;
    private const long MaxBytesPerFile = 5 * 1024 * 1024;

    private readonly InMemoryLogSink _memory;
    private readonly Redactor _redactor;
    private readonly string _directory;
    private readonly object _gate = new();

    public FileLogSink(string directory, Redactor redactor)
    {
        _directory = directory;
        _redactor = redactor;
        _memory = new InMemoryLogSink(redactor);
        _memory.EntryAdded += (s, e) => EntryAdded?.Invoke(this, e);
        try
        {
            Directory.CreateDirectory(directory);
            Prune();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            FileProblem = $"dossier de journaux inaccessible ({directory}) : {ex.Message}";
        }
    }

    public string Folder => _directory;

    /// <summary>Non nul si l'écriture sur disque a échoué — le Panel continue avec le journal en mémoire.</summary>
    public string? FileProblem { get; private set; }

    public string CurrentFile => Path.Combine(_directory, $"panel-{DateTime.Now:yyyyMMdd}.log");

    public IReadOnlyList<LogEntry> Entries => _memory.Entries;

    public event EventHandler? EntryAdded;

    public void Log(string message)
    {
        var line = _redactor.Redact(message);
        lock (_gate)
        {
            if (FileProblem is null)
            {
                try
                {
                    var file = CurrentFile;
                    if (!File.Exists(file) || new FileInfo(file).Length < MaxBytesPerFile)
                    {
                        File.AppendAllText(file, $"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss.fff zzz} {line}{Environment.NewLine}");
                    }
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    FileProblem = $"écriture du journal impossible ({_directory}) : {ex.Message}";
                }
            }
        }
        _memory.Log(line);
    }

    private void Prune()
    {
        var files = new DirectoryInfo(_directory).GetFiles("panel-*.log").OrderByDescending(f => f.Name).Skip(MaxFiles);
        foreach (var file in files)
        {
            file.Delete();
        }
    }
}
