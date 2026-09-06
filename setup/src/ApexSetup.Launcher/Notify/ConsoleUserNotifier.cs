namespace ApexSetup.Launcher.Notify;

/// <summary>
/// Repli non-Windows — même rôle que
/// <see cref="Elevation.NullElevationContext"/> : jamais utilisé en
/// production (le launcher est packagé exclusivement en win-x64), mais
/// nécessaire pour que <c>Program</c> compile et s'exécute sur les
/// plateformes de CI/tests de ce dépôt.
/// </summary>
public sealed class ConsoleUserNotifier : IUserNotifier
{
    public UserAction ShowError(string title, string message, string technicalDetail, bool offerRepair)
    {
        Console.Error.WriteLine($"[{title}] {message}");
        return UserAction.Close;
    }

    public void ShowInfo(string title, string message) => Console.Error.WriteLine($"[{title}] {message}");
}
