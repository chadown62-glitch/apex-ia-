using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>Écran 5/9 — construit et affiche le plan. Ne modifie JAMAIS la machine (même appel que <c>setup plan</c>).</summary>
public sealed class PlanViewModel : ViewModelBase
{
    public PlanViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        ContinueCommand = new RelayCommand(() => Navigation.NavigateTo(WizardStep.InstallProgress), () => Result is { Success: true });
    }

    private PlanResponse? _result;
    public PlanResponse? Result { get => _result; private set { SetProperty(ref _result, value); ContinueCommand.RaiseCanExecuteChanged(); } }

    public RelayCommand ContinueCommand { get; }

    public override async Task OnNavigatedToAsync()
    {
        IsBusy = true;
        ErrorMessage = null;
        try
        {
            var request = new SetupPlanRequest
            {
                TargetApexVersion = Session.ApexVersion, Destination = Session.Destination, ManifestPath = Session.ManifestPath,
                ProfileOverride = Session.ProfileOverride,
            };
            var response = await Service.PlanAsync(request).ConfigureAwait(false);
            Session.LastPlan = response;
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
