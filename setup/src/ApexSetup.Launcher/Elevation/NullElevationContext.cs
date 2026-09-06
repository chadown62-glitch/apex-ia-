namespace ApexSetup.Launcher.Elevation;

/// <summary>
/// Repli utilisé uniquement quand <c>OperatingSystem.IsWindows()</c> est
/// faux — permet à <c>Program</c> de compiler et de s'exécuter (pour ses
/// propres tests) sur Linux/macOS sans jamais appeler l'API Windows
/// d'identité de processus. Jamais utilisé en production : le launcher
/// n'est packagé qu'en win-x64 (voir <c>ApexSetup.Launcher.csproj</c>).
/// </summary>
public sealed class NullElevationContext : IElevationContext
{
    public bool IsElevated => true;

    public bool RequiresElevationFor(string targetPath) => false;
}
