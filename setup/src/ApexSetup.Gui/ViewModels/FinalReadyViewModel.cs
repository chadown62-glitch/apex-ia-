using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Écran 9/9 — état final, dérivé de <c>setup status</c> (jamais un état
/// fabriqué localement : la seule source de vérité est l'état
/// transactionnel réel lu sur disque par
/// <see cref="ISetupApplicationService.StatusAsync"/>). N'INVOQUE JAMAIS
/// Docker/Ollama/le Runtime directement — règle absolue héritée de
/// l'architecture Setup (« ne lance jamais Docker ou Ollama directement »,
/// déjà appliquée à <c>apex.bat</c>/<c>scripts/apex-launch.sh</c> côté
/// DEV) : ce Setup s'arrête à « installé et validé », lancer APEX reste
/// la responsabilité de l'utilisateur via le lanceur du Runtime lui-même.
/// </summary>
public sealed class FinalReadyViewModel : ViewModelBase
{
    public FinalReadyViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        RefreshStatusCommand = new AsyncRelayCommand(_ => RunStatusAsync());
    }

    private StatusResponse? _result;
    public StatusResponse? Result { get => _result; private set => SetProperty(ref _result, value); }

    public bool IsReady => Result?.State == "completed";

    /// <summary>Texte informatif uniquement — jamais un bouton qui lance Docker/Ollama/APEX lui-même.</summary>
    public string NextStepMessage =>
        "Installation terminée et validée. Utilisez le lanceur APEX habituel (apex.bat / apex-launch.sh) pour démarrer — " +
        "ce programme d'installation ne lance jamais le Runtime lui-même.";

    public AsyncRelayCommand RefreshStatusCommand { get; }

    // Même correctif que CheckViewModel (revue adversariale Phase 10) :
    // route par RefreshStatusCommand pour que le déclenchement automatique
    // et le bouton « Rafraîchir » partagent le même garde IsRunning.
    public override Task OnNavigatedToAsync() => RefreshStatusCommand.ExecuteAsync();

    private async Task RunStatusAsync()
    {
        IsBusy = true;
        ErrorMessage = null;
        try
        {
            var response = await Service.StatusAsync(Session.Destination).ConfigureAwait(false);
            Session.LastStatus = response;
            Result = response;
            OnPropertyChanged(nameof(IsReady));
            if (!response.Success && response.Errors.Count > 0)
            {
                ErrorMessage = response.Errors[0].Message;
            }
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Erreur inattendue : {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }
}
