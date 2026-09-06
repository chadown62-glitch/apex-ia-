using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

public sealed record CheckItemDisplay(string Name, string Detail, string StatusGlyph);

/// <summary>Écran 3/9 — Preflight complet. Ne déclenche JAMAIS d'installation (même appel que <c>setup check</c>).</summary>
public sealed class CheckViewModel : ViewModelBase
{
    public CheckViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        RecheckCommand = new AsyncRelayCommand(_ => RunCheckAsync());
        ContinueCommand = new AsyncRelayCommand(_ => { Navigation.NavigateTo(WizardStep.Profile); return Task.CompletedTask; },
            () => Result is { HasBlockingFailure: false });
    }

    private CheckResponse? _result;
    public CheckResponse? Result
    {
        get => _result;
        private set
        {
            SetProperty(ref _result, value);
            ContinueCommand.NotifyCanExecuteChanged();
            OnPropertyChanged(nameof(Checks));
        }
    }

    /// <summary>Projection d'affichage pure (glyphe dérivé de Passed/Blocking) — jamais une donnée recalculée différemment de <see cref="CheckResponse.Checks"/>.</summary>
    public IReadOnlyList<CheckItemDisplay> Checks =>
        Result?.Checks.Select(c => new CheckItemDisplay(c.Name, c.Detail, c.Passed ? "OK" : (c.Blocking ? "X" : "!"))).ToList()
        ?? [];

    public AsyncRelayCommand RecheckCommand { get; }
    public AsyncRelayCommand ContinueCommand { get; }

    // TROUVÉ EN REVUE ADVERSARIALE PHASE 10 : appelait RunCheckAsync()
    // DIRECTEMENT, en dehors de RecheckCommand — RecheckCommand.IsRunning
    // (le seul garde-fou contre une double exécution concurrente) ne
    // protégeait donc PAS ce déclenchement automatique. Reproduit :
    // arriver sur l'écran (déclenche RunCheckAsync via OnNavigatedToAsync)
    // puis cliquer immédiatement « Revérifier » avant la fin du premier
    // appel -> DEUX exécutions concurrentes de RunCheckAsync, chacune
    // écrivant Result/Session.LastCheck/IsBusy indépendamment ; celle qui
    // termine en dernier écrase silencieusement l'autre. Passer par
    // RecheckCommand.ExecuteAsync() ici fait partager le MÊME garde
    // IsRunning aux deux déclencheurs (auto + bouton) — un second
    // déclenchement pendant que le premier tourne devient un no-op, au
    // lieu d'une course.
    public override Task OnNavigatedToAsync() => RecheckCommand.ExecuteAsync();

    private async Task RunCheckAsync()
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
            var response = await Service.CheckAsync(request).ConfigureAwait(false);
            Session.LastCheck = response;
            Result = response;
            if (!response.Success && response.Errors.Count > 0)
            {
                ErrorMessage = response.Errors[0].Message;
            }
        }
        catch (Exception ex)
        {
            // Même filet de sécurité que InstallProgressViewModel (revue
            // adversariale Phase 10) : jamais laisser une exception
            // inattendue remonter sans être affichée — ni crash (via un
            // futur appelant async void), ni disparition silencieuse (via
            // un Task fire-and-forget non observé).
            ErrorMessage = $"Erreur inattendue : {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }
}
