using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Écran 7/9 — accessible hors séquence (installation interrompue lors
/// d'une session précédente). N'agit JAMAIS silencieusement : classifie
/// d'abord (visible dans <see cref="Result.StateDetected"/>), l'action
/// entreprise et son résultat restent toujours affichés explicitement —
/// même appel que <c>setup recover</c>, jamais de logique de récupération
/// dupliquée ici.
/// </summary>
public sealed class RecoveryViewModel : ViewModelBase
{
    public RecoveryViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        RecoverCommand = new AsyncRelayCommand(_ => RunRecoverAsync());
        ContinueCommand = new RelayCommand(() => Navigation.NavigateTo(WizardStep.Plan));
    }

    private RecoverResponse? _result;
    public RecoverResponse? Result { get => _result; private set => SetProperty(ref _result, value); }

    public AsyncRelayCommand RecoverCommand { get; }
    public RelayCommand ContinueCommand { get; }

    private async Task RunRecoverAsync()
    {
        IsBusy = true;
        ErrorMessage = null;
        try
        {
            var response = await Service.RecoverAsync(Session.Destination).ConfigureAwait(false);
            Session.LastRecover = response;
            Result = response;
            if (!response.Success && response.Errors.Count > 0)
            {
                ErrorMessage = response.Errors[0].Message;
            }
        }
        catch (Exception ex)
        {
            // Voir InstallProgressViewModel/CheckViewModel (revue adversariale Phase 10).
            ErrorMessage = $"Erreur inattendue : {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }
}
