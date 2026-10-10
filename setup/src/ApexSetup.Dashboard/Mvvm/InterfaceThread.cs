using Avalonia.Threading;

namespace ApexSetup.Dashboard.Mvvm;

/// <summary>
/// Le fil d'interface (0.4.4). Constaté sur Windows 11 réel (10/10/2026, 0.4.3) : environ 2 s après « Démarrer APEX »,
/// « InvalidOperationException : Call from invalid thread », et APEX.exe se fermait. Une action de bouton se terminait
/// sur un fil d'arrière-plan (après un <c>await … ConfigureAwait(false)</c>) et prévenait le bouton depuis ce fil ;
/// quand l'état du bouton changeait à ce moment-là, Avalonia (comme Windows) refusait. Désormais, un objet créé sur le
/// fil d'interface (vue-modèle, commande d'une vraie fenêtre) y renvoie TOUTES ses notifications, d'où qu'elles
/// partent. Hors fenêtre (tests unitaires, mode sans fenêtre), rien ne change : notification immédiate.
/// </summary>
internal static class InterfaceThread
{
    /// <summary>Vrai si l'appelant est sur le fil d'interface d'une application Avalonia en marche.</summary>
    public static bool IsCurrent() => Avalonia.Application.Current is not null && Dispatcher.UIThread.CheckAccess();

    /// <summary>
    /// Notifie : tout de suite si l'objet n'appartient pas à une fenêtre ou si l'on est déjà sur son fil ; sinon,
    /// la notification est postée sur le fil d'interface (jamais exécutée depuis un autre fil).
    /// </summary>
    public static void Notify(bool ownedByInterface, Action notify)
    {
        if (ownedByInterface && !Dispatcher.UIThread.CheckAccess())
        {
            Dispatcher.UIThread.Post(notify);
            return;
        }
        notify();
    }
}
