using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Écran 6/9 — orchestre l'installation réelle via
/// <see cref="ISetupApplicationService.InstallAsync"/> (exactement l'appel
/// que fait <c>setup install</c> — jamais une deuxième logique
/// d'installation). AUCUN pourcentage : <c>InstallAsync</c> ne renvoie un
/// résultat qu'une fois le pipeline terminé (succès ou échec), il n'existe
/// aujourd'hui aucune API de progression PAR ÉTAPE côté
/// <c>ISetupApplicationService</c> — inventer un pourcentage inventé
/// serait un FAUX pourcentage, explicitement interdit. <see cref="IsBusy"/>
/// pilote donc un indicateur d'activité INDÉTERMINÉ pendant l'appel, et le
/// détail (étapes exécutées, rollback éventuel) n'apparaît qu'au résultat
/// final — honnête plutôt que fabriqué. <see cref="Committed"/> ne peut
/// jamais devenir vrai avant que <c>InstallAsync</c> ne soit revenu avec
/// <c>Committed=true</c>, lui-même garanti par le pipeline Core (jamais
/// avant Commit).
/// </summary>
public sealed class InstallProgressViewModel : ViewModelBase
{
    public InstallProgressViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        // Pas de canExecute redondant ici : AsyncRelayCommand.IsRunning
        // empêche déjà toute ré-invocation concurrente de LUI-MÊME — la
        // protection réelle contre un double déclenchement (double-clic,
        // double installation), indépendante d'une propriété ViewModel
        // qui pourrait devenir périmée.
        InstallCommand = new AsyncRelayCommand(RunInstallAsync);
        ContinueCommand = new RelayCommand(() => Navigation.NavigateTo(WizardStep.Validation), () => Result is { Committed: true });
        RecoverInsteadCommand = new RelayCommand(() => Navigation.NavigateTo(WizardStep.Recovery));
        CancelCommand = new RelayCommand(RequestCancel, () => InstallCommand.IsRunning);
        // TROUVÉ EN REVUE ADVERSARIALE PHASE 10 : sans cet abonnement,
        // CancelCommand.CanExecute() n'est évalué QU'UNE FOIS (au moment du
        // binding XAML, quand InstallCommand.IsRunning vaut encore false) —
        // rien ne notifiait jamais CancelCommand qu'IsRunning avait changé,
        // le bouton Annuler restait donc DÉSACTIVÉ en permanence, même
        // pendant une installation réellement en cours (reproduit : lancer
        // Install, cliquer Annuler -> aucun effet, RequestCancel jamais
        // appelée). AsyncRelayCommand.IsRunning lève déjà son PROPRE
        // CanExecuteChanged à chaque changement (voir Mvvm/RelayCommand.cs) —
        // il suffit de le répercuter sur CancelCommand.
        InstallCommand.CanExecuteChanged += (_, _) => CancelCommand.RaiseCanExecuteChanged();
    }

    private InstallResponse? _result;
    public InstallResponse? Result { get => _result; private set { SetProperty(ref _result, value); ContinueCommand.RaiseCanExecuteChanged(); OnPropertyChanged(nameof(Committed)); } }

    public bool Committed => Result?.Committed ?? false;

    public bool WasCancelled { get; private set; }

    public AsyncRelayCommand InstallCommand { get; }
    public RelayCommand ContinueCommand { get; }
    public RelayCommand RecoverInsteadCommand { get; }
    public RelayCommand CancelCommand { get; }

    private async Task RunInstallAsync(CancellationToken cancellationToken)
    {
        IsBusy = true;
        ErrorMessage = null;
        WasCancelled = false;
        Result = null;
        try
        {
            var request = new SetupInstallRequest
            {
                Plan = new SetupPlanRequest
                {
                    TargetApexVersion = Session.ApexVersion, Destination = Session.Destination, ManifestPath = Session.ManifestPath,
                    ProfileOverride = Session.ProfileOverride,
                },
                WorkingDirectory = Session.WorkingDirectory,
            };
            var response = await Service.InstallAsync(request, cancellationToken).ConfigureAwait(false);
            Session.LastInstall = response;
            Result = response;
            if (!response.Success && response.Errors.Count > 0)
            {
                ErrorMessage = response.Errors[0].Message;
            }
        }
        catch (OperationCanceledException)
        {
            // Annulation réelle propagée jusqu'au pipeline Core
            // (InstallationPipeline vérifie le token entre chaque étape) —
            // jamais un état "installé" fabriqué après une annulation.
            WasCancelled = true;
            ErrorMessage = "Installation annulée par l'utilisateur.";
        }
        catch (Exception ex)
        {
            // TROUVÉ EN REVUE ADVERSARIALE PHASE 10 : sans ce catch, toute
            // exception AUTRE qu'une annulation (ex. levée par BuildPlanAsync
            // ou un I/O de manifeste avant même que le pipeline ne démarre —
            // Service.InstallAsync n'a pas de try/catch de dernier recours)
            // remontait à travers AsyncRelayCommand.Execute (`async void`,
            // voir Mvvm/RelayCommand.cs) et terminait TOUT le processus GUI,
            // au pire moment possible : en pleine écriture d'une
            // installation. Jamais masquée pour autant : affichée comme
            // n'importe quelle autre erreur, jamais un simple message
            // d'exception brut non traduit pour l'utilisateur final mais
            // au moins visible et non fatal.
            ErrorMessage = $"Erreur inattendue pendant l'installation : {ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>Annulation réelle — jamais un simple masquage visuel de l'opération toujours en cours en arrière-plan.</summary>
    public void RequestCancel() => InstallCommand.Cancel();
}
