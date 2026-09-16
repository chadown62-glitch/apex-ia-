namespace ApexSetup.Dashboard.Models;

/// <summary>Une ligne de la console de logs (mission §12) — toujours produite par une action réelle, jamais un texte de remplissage.</summary>
public sealed record LogEntry(DateTimeOffset Timestamp, string Message)
{
    public string Formatted => $"[{Timestamp:HH:mm:ss}] {Message}";
}
