using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Mission §12 — chaque ligne vient d'une action réelle, jamais un texte de remplissage.</summary>
public interface ILogSink
{
    IReadOnlyList<LogEntry> Entries { get; }

    void Log(string message);

    event EventHandler? EntryAdded;
}

public sealed class InMemoryLogSink : ILogSink
{
    private readonly List<LogEntry> _entries = [];

    public IReadOnlyList<LogEntry> Entries => _entries;

    public event EventHandler? EntryAdded;

    public void Log(string message)
    {
        _entries.Add(new LogEntry(DateTimeOffset.Now, message));
        EntryAdded?.Invoke(this, EventArgs.Empty);
    }
}
