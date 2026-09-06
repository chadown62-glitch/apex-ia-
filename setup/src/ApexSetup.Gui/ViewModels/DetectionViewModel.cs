using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>Écran 2/9 — appelle réellement <see cref="ISetupApplicationService.DetectAsync"/> à l'entrée sur l'écran.</summary>
public sealed class DetectionViewModel : ViewModelBase
{
    public DetectionViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        ContinueCommand = new AsyncRelayCommand(_ => { Navigation.NavigateTo(WizardStep.Check); return Task.CompletedTask; }, () => Result is not null);
    }

    private DetectResponse? _result;
    public DetectResponse? Result { get => _result; private set { SetProperty(ref _result, value); ContinueCommand.NotifyCanExecuteChanged(); } }

    public AsyncRelayCommand ContinueCommand { get; }

    public override async Task OnNavigatedToAsync()
    {
        IsBusy = true;
        ErrorMessage = null;
        try
        {
            var response = await Service.DetectAsync(Session.Destination).ConfigureAwait(false);
            Session.LastDetect = response;
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
