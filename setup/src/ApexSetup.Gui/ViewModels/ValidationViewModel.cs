using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>Écran 8/9 — validation post-installation SANS réinstaller (même appel que <c>setup validate</c> — RuntimeAdapter uniquement).</summary>
public sealed class ValidationViewModel : ViewModelBase
{
    public ValidationViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        ContinueCommand = new RelayCommand(() => Navigation.NavigateTo(WizardStep.FinalReady), () => Result is { Success: true });
        RevalidateCommand = new AsyncRelayCommand(_ => RunValidateAsync());
    }

    private ValidateResponse? _result;
    public ValidateResponse? Result { get => _result; private set { SetProperty(ref _result, value); ContinueCommand.RaiseCanExecuteChanged(); } }

    public RelayCommand ContinueCommand { get; }
    public AsyncRelayCommand RevalidateCommand { get; }

    // Même correctif que CheckViewModel (revue adversariale Phase 10) :
    // route par RevalidateCommand pour que le déclenchement automatique et
    // le bouton « Revalider » partagent le même garde IsRunning, jamais
    // deux RunValidateAsync concurrents.
    public override Task OnNavigatedToAsync() => RevalidateCommand.ExecuteAsync();

    private async Task RunValidateAsync()
    {
        IsBusy = true;
        ErrorMessage = null;
        try
        {
            var response = await Service.ValidateAsync().ConfigureAwait(false);
            Session.LastValidate = response;
            Result = response;
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
