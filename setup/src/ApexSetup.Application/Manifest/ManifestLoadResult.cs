using ApexSetup.Core.Contracts;

namespace ApexSetup.Application.Manifest;

public sealed record ManifestLoadResult
{
    public required bool Success { get; init; }
    public IReadOnlyList<ModelManifestEntry> Entries { get; init; } = [];
    public IReadOnlyDictionary<string, string> SourceUrlsByModelId { get; init; } = new Dictionary<string, string>();
    public string? ErrorMessage { get; init; }

    public static ManifestLoadResult Ok(IReadOnlyList<ModelManifestEntry> entries, IReadOnlyDictionary<string, string> urls) =>
        new() { Success = true, Entries = entries, SourceUrlsByModelId = urls };

    public static ManifestLoadResult Failed(string message) => new() { Success = false, ErrorMessage = message };
}
