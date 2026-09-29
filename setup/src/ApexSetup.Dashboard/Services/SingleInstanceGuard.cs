namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Une seule fenêtre du Panel par session : un second double-clic ne crée pas un second Panel, il
/// ramène le premier au premier plan (Windows : événement nommé). Le nom du mutex est aussi celui
/// que l'installateur surveille (<c>AppMutex</c>) pour ne jamais remplacer ou désinstaller un Panel
/// en cours d'exécution. Un mutex abandonné (Panel tué) est repris normalement.
/// </summary>
public sealed class SingleInstanceGuard : IDisposable
{
    public const string MutexName = "APEX.Panel.SingleInstance";
    private const string ActivateEventName = "APEX.Panel.Activate";

    private readonly Mutex _mutex;
    private readonly EventWaitHandle? _activate;
    private readonly CancellationTokenSource _stop = new();

    private SingleInstanceGuard(Mutex mutex, EventWaitHandle? activate)
    {
        _mutex = mutex;
        _activate = activate;
    }

    /// <summary>Déclenché (sur un thread d'arrière-plan) quand une autre instance demande l'activation.</summary>
    public event Action? ActivationRequested;

    /// <summary>Retourne <c>null</c> si une autre instance détient déjà le Panel (elle a été priée de se montrer).</summary>
    public static SingleInstanceGuard? TryAcquire(string? suffix = null)
    {
        var name = $"Local\\{MutexName}{suffix}";
        var mutex = new Mutex(false, name);
        bool owned;
        try
        {
            owned = mutex.WaitOne(TimeSpan.Zero);
        }
        catch (AbandonedMutexException)
        {
            owned = true; // l'instance précédente a été tuée : on reprend la main
        }
        if (!owned)
        {
            mutex.Dispose();
            if (OperatingSystem.IsWindows())
            {
                try
                {
                    using var activate = EventWaitHandle.OpenExisting($"Local\\{ActivateEventName}{suffix}");
                    activate.Set();
                }
                catch (WaitHandleCannotBeOpenedException)
                {
                    // L'autre instance démarre encore : rien à activer.
                }
            }
            return null;
        }

        EventWaitHandle? activateEvent = OperatingSystem.IsWindows()
            ? new EventWaitHandle(false, EventResetMode.AutoReset, $"Local\\{ActivateEventName}{suffix}")
            : null;
        var guard = new SingleInstanceGuard(mutex, activateEvent);
        if (activateEvent is not null)
        {
            var thread = new Thread(() => guard.Listen(activateEvent)) { IsBackground = true, Name = "apex-activate" };
            thread.Start();
        }
        return guard;
    }

    private void Listen(EventWaitHandle activate)
    {
        var handles = new[] { activate, _stop.Token.WaitHandle };
        while (WaitHandle.WaitAny(handles) == 0)
        {
            ActivationRequested?.Invoke();
        }
    }

    public void Dispose()
    {
        _stop.Cancel();
        _activate?.Dispose();
        try
        {
            _mutex.ReleaseMutex();
        }
        catch (ApplicationException)
        {
            // Libéré depuis un autre thread : le système le libère à la fin du processus.
        }
        _mutex.Dispose();
        _stop.Dispose();
    }
}
