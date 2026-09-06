using ApexSetup.Application;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Base commune des 9 écrans — chacun reçoit exclusivement
/// <see cref="ISetupApplicationService"/> (jamais un type Core) et l'état
/// partagé <see cref="SetupSession"/>. <see cref="ErrorMessage"/> est
/// TOUJOURS peuplé depuis un champ structuré de la réponse applicative
/// (<c>CliError.Message</c>/<c>Detail</c>) — jamais depuis le message brut
/// d'une exception .NET affiché tel quel à l'utilisateur.
/// </summary>
public abstract class ViewModelBase(ISetupApplicationService service, SetupSession session, INavigationService navigation) : ObservableObject
{
    protected ISetupApplicationService Service { get; } = service;
    protected SetupSession Session { get; } = session;
    protected INavigationService Navigation { get; } = navigation;

    private bool _isBusy;
    public bool IsBusy
    {
        get => _isBusy;
        protected set => SetProperty(ref _isBusy, value);
    }

    private string? _errorMessage;
    public string? ErrorMessage
    {
        get => _errorMessage;
        protected set => SetProperty(ref _errorMessage, value);
    }

    /// <summary>Appelée à chaque entrée sur l'écran — jamais dans le constructeur (garde l'UI réactive dès l'affichage).</summary>
    public virtual Task OnNavigatedToAsync() => Task.CompletedTask;
}
