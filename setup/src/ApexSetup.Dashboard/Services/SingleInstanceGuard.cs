namespace ApexSetup.Dashboard.Services;

/// <summary>Ce qu'un second lancement a constaté de l'instance déjà ouverte.</summary>
public enum OtherInstance
{
    /// <summary>Aucune autre instance : ce lancement détient le Panel.</summary>
    None,

    /// <summary>L'instance ouverte a ramené sa fenêtre au premier plan (accusé reçu de son fil d'interface).</summary>
    Activated,

    /// <summary>
    /// L'instance ouverte n'a pas répondu dans le délai : sa fenêtre est bloquée ou n'a jamais pu s'afficher.
    /// Constaté sur Windows réel (10/10/2026, 0.4.2) : « je double-clique sur l'icône et rien ne se passe ».
    /// </summary>
    Unresponsive,
}

/// <summary>
/// Une seule fenêtre du Panel par session : un second double-clic ne crée pas un second Panel, il
/// ramène le premier au premier plan (Windows : événement nommé). Le nom du mutex est aussi celui
/// que l'installateur surveille (<c>AppMutex</c>) pour ne jamais remplacer ou désinstaller un Panel
/// en cours d'exécution. Un mutex abandonné (Panel tué) est repris normalement.
/// Depuis 0.4.3, l'instance ouverte ACCUSE RÉCEPTION depuis son fil d'interface : sans accusé, le second
/// lancement sait que la première fenêtre est bloquée au lieu de se terminer en silence.
/// </summary>
public sealed class SingleInstanceGuard : IDisposable
{
    public const string MutexName = "APEX.Panel.SingleInstance";
    private const string ActivateEventName = "APEX.Panel.Activate";
    private const string AcknowledgeEventName = "APEX.Panel.Activated";

    private readonly Mutex _mutex;
    private readonly EventWaitHandle? _activate;
    private readonly string? _suffix;
    private readonly CancellationTokenSource _stop = new();
    private readonly object _gate = new();
    private Action? _handler;
    private bool _pending;

    private SingleInstanceGuard(Mutex mutex, EventWaitHandle? activate, string? suffix)
    {
        _mutex = mutex;
        _activate = activate;
        _suffix = suffix;
    }

    /// <summary>Retourne <c>null</c> si une autre instance détient déjà le Panel (elle a été priée de se montrer).</summary>
    public static SingleInstanceGuard? TryAcquire(string? suffix = null) => TryAcquire(suffix, TimeSpan.Zero, out _);

    /// <summary>
    /// Comme <see cref="TryAcquire(string?)"/> ; si une autre instance détient le Panel, attend jusqu'à
    /// <paramref name="acknowledgeTimeout"/> son accusé de réception (Windows) et dit si elle a répondu.
    /// </summary>
    public static SingleInstanceGuard? TryAcquire(string? suffix, TimeSpan acknowledgeTimeout, out OtherInstance other)
    {
        other = OtherInstance.None;
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
            other = OperatingSystem.IsWindows() ? RequestActivation(suffix, acknowledgeTimeout) : OtherInstance.Activated;
            return null;
        }

        EventWaitHandle? activateEvent = OperatingSystem.IsWindows()
            ? new EventWaitHandle(false, EventResetMode.AutoReset, $"Local\\{ActivateEventName}{suffix}")
            : null;
        var guard = new SingleInstanceGuard(mutex, activateEvent, suffix);
        if (activateEvent is not null)
        {
            var thread = new Thread(() => guard.Listen(activateEvent)) { IsBackground = true, Name = "apex-activate" };
            thread.Start();
        }
        return guard;
    }

    /// <summary>
    /// Après la fermeture forcée d'une instance bloquée : le système libère son mutex à la fin du processus
    /// (abandonné) ; on le reprend dès qu'il est libre, au plus tard après <paramref name="timeout"/>.
    /// </summary>
    public static SingleInstanceGuard? AcquireWhenReleased(TimeSpan timeout, string? suffix = null)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (true)
        {
            var guard = TryAcquire(suffix, TimeSpan.Zero, out _);
            if (guard is not null || DateTime.UtcNow >= deadline)
            {
                return guard;
            }
            Thread.Sleep(250);
        }
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static OtherInstance RequestActivation(string? suffix, TimeSpan acknowledgeTimeout)
    {
        // L'accusé est créé AVANT la demande : l'autre instance le trouve toujours quand elle répond.
        using var acknowledged = new EventWaitHandle(false, EventResetMode.AutoReset, $"Local\\{AcknowledgeEventName}{suffix}");
        var deadline = DateTime.UtcNow + acknowledgeTimeout;
        var requested = false;
        while (true)
        {
            if (!requested)
            {
                try
                {
                    using var activate = EventWaitHandle.OpenExisting($"Local\\{ActivateEventName}{suffix}");
                    activate.Set();
                    requested = true;
                }
                catch (WaitHandleCannotBeOpenedException)
                {
                    // L'autre instance détient le mutex mais n'a pas encore créé son événement (elle démarre) : réessayé.
                }
            }
            if (acknowledgeTimeout <= TimeSpan.Zero)
            {
                return OtherInstance.Activated; // ancien comportement : demander, sans attendre de réponse
            }
            var left = deadline - DateTime.UtcNow;
            if (left <= TimeSpan.Zero)
            {
                return OtherInstance.Unresponsive;
            }
            if (acknowledged.WaitOne(left < TimeSpan.FromMilliseconds(250) ? left : TimeSpan.FromMilliseconds(250)))
            {
                return OtherInstance.Activated;
            }
        }
    }

    /// <summary>
    /// Ce que fait la fenêtre quand un second lancement la demande (appelé sur un fil d'arrière-plan). Une demande
    /// arrivée avant l'enregistrement (fenêtre pas encore créée) est rejouée à l'enregistrement.
    /// </summary>
    public void OnActivationRequested(Action handler)
    {
        bool replay;
        lock (_gate)
        {
            _handler = handler;
            replay = _pending;
            _pending = false;
        }
        if (replay)
        {
            handler();
        }
    }

    /// <summary>Accusé de réception, envoyé par le FIL D'INTERFACE une fois la fenêtre ramenée : il prouve qu'elle répond.</summary>
    public void Acknowledge()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }
        try
        {
            using var acknowledged = EventWaitHandle.OpenExisting($"Local\\{AcknowledgeEventName}{_suffix}");
            acknowledged.Set();
        }
        catch (WaitHandleCannotBeOpenedException)
        {
            // Le second lancement ne l'attend plus.
        }
    }

    /// <summary>Une demande d'activation (exposé pour les tests : sous Windows, c'est l'événement nommé qui l'appelle).</summary>
    internal void Signal()
    {
        Action? handler;
        lock (_gate)
        {
            handler = _handler;
            if (handler is null)
            {
                _pending = true;
            }
        }
        handler?.Invoke();
    }

    private void Listen(EventWaitHandle activate)
    {
        var handles = new[] { activate, _stop.Token.WaitHandle };
        while (WaitHandle.WaitAny(handles) == 0)
        {
            Signal();
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
