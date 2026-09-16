using ApexSetup.Application.Contracts;
using ApexSetup.Application.Hardware;
using ApexSetup.Application.Manifest;
using ApexSetup.Application.Requests;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Download;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Planning;
using ApexSetup.Core.Preflight;
using ApexSetup.Core.RuntimeAdapter;
using ApexSetup.Core.Stages;
using CoreRuntimeAdapter = ApexSetup.Core.RuntimeAdapter.RuntimeAdapter;

namespace ApexSetup.Application;

/// <summary>
/// Implémentation UNIQUE de <see cref="ISetupApplicationService"/> — le
/// seul endroit du dépôt Setup qui connaît à la fois <c>ApexSetup.Core</c>
/// ET la forme du contrat JSON public. Contient toute la « logique
/// applicative » (construction du plan, mapping des résultats Core vers le
/// contrat CLI, classification des dépendances) mais AUCUNE logique
/// métier de bas niveau déjà présente dans Core (jamais de manipulation de
/// fichier, jamais de calcul SHA-256, jamais de décision de rollback ici —
/// tout cela reste dans les stages/le pipeline).
/// </summary>
public sealed class SetupApplicationService(
    CoreRuntimeAdapter runtimeAdapter,
    IEnvironmentProbe environmentProbe,
    IHardwareDetector hardwareDetector,
    HttpClient httpClient,
    string setupVersion) : ISetupApplicationService
{
    // -------------------------------------------------------------
    // setup version
    // -------------------------------------------------------------

    public async Task<VersionResponse> GetVersionAsync(CancellationToken cancellationToken = default)
    {
        var warnings = new List<CliWarning>();
        var versionResult = await runtimeAdapter.GetVersionAsync(cancellationToken).ConfigureAwait(false);

        RuntimeVersionSummary? runtime = null;
        if (versionResult.IsSuccess)
        {
            var v = versionResult.Value!;
            runtime = new RuntimeVersionSummary
            {
                Version = v.Version, Phase = v.Phase, PhaseName = v.PhaseName, Python = v.Python, Platform = v.Platform,
            };
        }
        else
        {
            warnings.Add(new CliWarning
            {
                Code = "runtime_unreachable",
                Message = $"Runtime injoignable ({versionResult.Outcome}) — version Setup seule retournée.",
            });
        }

        return new VersionResponse
        {
            Operation = "version",
            Success = true,
            SetupVersion = setupVersion,
            Runtime = runtime,
            Errors = [],
            Warnings = warnings,
        };
    }

    // -------------------------------------------------------------
    // setup detect
    // -------------------------------------------------------------

    public Task<DetectResponse> DetectAsync(string destinationForDiskProbe, CancellationToken cancellationToken = default)
    {
        var machine = hardwareDetector.Detect();

        double diskFreeGb;
        var errors = new List<CliError>();
        try
        {
            diskFreeGb = environmentProbe.GetAvailableDiskBytes(destinationForDiskProbe) / 1_000_000_000.0;
        }
        catch (Exception ex)
        {
            diskFreeGb = 0;
            errors.Add(new CliError
            {
                Code = "disk_probe_failed",
                Message = "impossible de mesurer l'espace disque disponible",
                Detail = ex.Message,
            });
        }

        var detected = new DetectedHardware
        {
            RamTotalGb = machine.RamTotalGb,
            VramTotalGb = machine.VramTotalGb ?? 0,
            CpuThreads = machine.CpuThreads,
            DiskFreeGb = diskFreeGb,
        };
        var recommended = HardwareProfileEngine.SelectRecommendedProfile(detected);

        return Task.FromResult(new DetectResponse
        {
            Operation = "detect",
            Success = errors.Count == 0,
            Os = machine.Os,
            Architecture = machine.Architecture,
            RamTotalGb = machine.RamTotalGb,
            CpuThreads = machine.CpuThreads,
            Gpu = machine.Gpu,
            VramTotalGb = machine.VramTotalGb,
            DiskFreeGb = diskFreeGb,
            Environment = machine.Environment,
            RecommendedProfile = Wire(recommended.Tier),
            ProfileNote =
                "recommandation Setup uniquement, jamais une autorisation — Gate reste seul juge de la capacité réelle " +
                "(VRAM non détectée dans cette phase : traitée conservativement comme 0 Go, ce qui abaisse la recommandation).",
            Errors = errors,
            Warnings = [],
        });
    }

    // -------------------------------------------------------------
    // setup check
    // -------------------------------------------------------------

    /// <summary>
    /// LIMITE HONNÊTE (§11 Idempotence CLI, « check -> aucun effet
    /// secondaire ») : le check <c>fs.write_permission</c> hérité tel quel
    /// de <c>PreflightChecker</c>/<c>RealEnvironmentProbe.CanWriteTo</c>
    /// (Phase 1) PROUVE la permission d'écriture EMPIRIQUEMENT — il crée
    /// réellement <c>Plan.Destination</c> s'il n'existe pas encore (puis y
    /// écrit et supprime un fichier-sonde), plutôt que de lire une ACL
    /// déclarative (choix documenté et délibéré côté Core : « jamais une
    /// simple lecture d'ACL déclarative, une preuve empirique »). C'est
    /// l'UNIQUE effet de bord possible de <c>check</c>/<c>plan</c> : au
    /// pire un répertoire de destination VIDE laissé sur le disque
    /// (identique à ce que l'utilisateur aurait pu créer lui-même) —
    /// jamais un artefact, une configuration, un verrou ou un état
    /// transactionnel. Revenir sur ce choix reviendrait à modifier le
    /// comportement de Core hors du périmètre « CLI + Orchestration » de
    /// cette phase ; documenté ici plutôt que silencieusement ignoré.
    /// </summary>
    public async Task<CheckResponse> CheckAsync(SetupPlanRequest request, CancellationToken cancellationToken = default)
    {
        var (plan, gateResult, buildError) = await BuildPlanAsync(request, cancellationToken).ConfigureAwait(false);
        if (buildError is not null)
        {
            return new CheckResponse
            {
                Operation = "check", Success = false, Checks = [], HasBlockingFailure = true,
                Errors = [buildError], Warnings = [],
            };
        }

        var report = await PreflightChecker.RunAsync(
            runtimeAdapter, plan!, environmentProbe, request.MinimumApexVersion, cancellationToken).ConfigureAwait(false);

        var checks = report.Results
            .Select(r => new CheckItem { Name = r.Name, Passed = r.Passed, Blocking = r.Blocking, Detail = r.Detail })
            .ToList();

        var gateBlocking = IsGateBlocking(gateResult, out var gateDetail);
        checks.Add(new CheckItem { Name = "gate.phase2_authorized", Passed = !gateBlocking, Blocking = true, Detail = gateDetail });

        var hasBlockingFailure = report.HasBlockingFailure || gateBlocking;

        return new CheckResponse
        {
            Operation = "check",
            Success = !hasBlockingFailure,
            Checks = checks,
            HasBlockingFailure = hasBlockingFailure,
            Errors = [],
            Warnings = [],
        };
    }

    // -------------------------------------------------------------
    // setup plan
    // -------------------------------------------------------------

    public async Task<PlanResponse> PlanAsync(SetupPlanRequest request, CancellationToken cancellationToken = default)
    {
        var (plan, _, buildError) = await BuildPlanAsync(request, cancellationToken).ConfigureAwait(false);
        if (buildError is not null)
        {
            return new PlanResponse { Operation = "plan", Success = false, Errors = [buildError], Warnings = [] };
        }

        return new PlanResponse
        {
            Operation = "plan",
            Success = true,
            Plan = plan,
            PlanFingerprint = PlanFingerprint.Compute(plan!),
            Errors = [],
            Warnings = [],
        };
    }

    // -------------------------------------------------------------
    // setup install
    // -------------------------------------------------------------

    public async Task<InstallResponse> InstallAsync(SetupInstallRequest request, CancellationToken cancellationToken = default)
    {
        var (plan, gateResult, buildError) = await BuildPlanAsync(request.Plan, cancellationToken).ConfigureAwait(false);
        if (buildError is not null)
        {
            return Failed("install", buildError, "plan_build_failed");
        }

        var preflight = await PreflightChecker.RunAsync(
            runtimeAdapter, plan!, environmentProbe, request.Plan.MinimumApexVersion, cancellationToken).ConfigureAwait(false);

        var runtimeUnavailable = preflight.Results.Any(r =>
            r.Name is "runtime.available" or "runtime.version_compatible" or "runtime.doctor_exploitable" or "runtime.gate_exploitable"
            && !r.Passed);
        if (runtimeUnavailable)
        {
            return Failed("install", new CliError
            {
                Code = "runtime_unavailable",
                Message = "le Runtime est injoignable ou son contrat n'est pas exploitable — installation non démarrée",
            }, "runtime_unavailable");
        }

        if (IsGateBlocking(gateResult, out var gateDetail))
        {
            return Failed("install", new CliError
            {
                Code = "gate_blocking", Message = "le Gate n'autorise pas la phase suivante sur cette machine", Detail = gateDetail,
            }, "gate_blocking");
        }

        if (preflight.HasBlockingFailure)
        {
            var failing = preflight.Results.Where(r => r.Blocking && !r.Passed).Select(r => $"{r.Name}: {r.Detail}");
            return Failed("install", new CliError
            {
                Code = "preflight_blocking", Message = "une ou plusieurs vérifications préalables bloquantes ont échoué",
                Detail = string.Join(" | ", failing),
            }, "preflight_blocking");
        }

        var stages = BuildPipelineStages();
        var pipeline = new InstallationPipeline(stages);
        var context = new InstallationContext
        {
            Plan = plan!, WorkingDirectory = request.WorkingDirectory, DestinationDirectory = plan!.Destination,
        };
        var result = await pipeline.RunAsync(context, cancellationToken).ConfigureAwait(false);

        if (result.Success)
        {
            return new InstallResponse
            {
                Operation = "install", Success = true, State = "committed", CurrentStep = "commit",
                Committed = true, ExecutedSteps = result.ExecutedStageNames,
                RolledBackSteps = [], FailedRollbackSteps = [],
                Progress = new CliProgress { CompletedSteps = result.ExecutedStageNames, TotalSteps = plan.Steps.Count },
                Errors = [], Warnings = [],
            };
        }

        // Refus explicite AVANT toute étape (verrou de concurrence) — jamais confondu avec un échec de pipeline.
        if (result.FailedStageName is null && result.ExecutedStageNames.Count == 0 && result.RolledBackStageNames.Count == 0)
        {
            return new InstallResponse
            {
                Operation = "install", Success = false, State = "refused",
                Committed = false, ExecutedSteps = [], FailedStep = null, FailureDetail = result.FailureDetail,
                RolledBackSteps = [], FailedRollbackSteps = [],
                Errors = [new CliError { Code = "concurrency_refused", Message = "une autre installation est déjà en cours à cette destination", Detail = result.FailureDetail }],
                Warnings = [],
            };
        }

        var errorCode = result.FailedRollbackStageNames.Count > 0
            ? "rollback_failed"
            : MapStageToErrorCode(result.FailedStageName);

        return new InstallResponse
        {
            Operation = "install", Success = false, State = "failed", CurrentStep = result.FailedStageName,
            Committed = false, ExecutedSteps = result.ExecutedStageNames,
            FailedStep = result.FailedStageName, FailureDetail = result.FailureDetail,
            RolledBackSteps = result.RolledBackStageNames, FailedRollbackSteps = result.FailedRollbackStageNames,
            Errors = [new CliError { Code = errorCode, Message = result.FailureDetail ?? "échec sans détail", Detail = result.FailedStageName }],
            Warnings = [],
        };
    }

    // -------------------------------------------------------------
    // setup recover
    // -------------------------------------------------------------

    public Task<RecoverResponse> RecoverAsync(string destination, CancellationToken cancellationToken = default)
    {
        using var recoveryLock = InstallationLock.TryAcquire(destination);
        if (recoveryLock is null)
        {
            return Task.FromResult(new RecoverResponse
            {
                Operation = "recover", Success = false, State = Wire(SetupLifecycleState.InProgress),
                StateDetected = Wire(SetupLifecycleState.InProgress),
                ActionTaken = "refused", Result = Wire(SetupLifecycleState.InProgress),
                Detail = "une exécution est actuellement en cours à cette destination — recovery refusée pour ne jamais interférer avec elle",
                Errors = [new CliError { Code = "concurrency_refused", Message = "installation en cours, recovery refusée" }],
                Warnings = [],
            });
        }

        var assessment = RecoveryClassifier.Classify(destination);
        var detectedState = MapRecoveryState(assessment.State);
        var summary = ToSummary(assessment.PersistedState);

        switch (assessment.State)
        {
            case RecoveryState.InterruptedNeedsRollback:
                InstallationRecovery.RollbackInterrupted(destination);
                return Task.FromResult(new RecoverResponse
                {
                    Operation = "recover", Success = true, State = Wire(SetupLifecycleState.RollbackPerformed),
                    StateDetected = Wire(detectedState), PersistedState = summary,
                    ActionTaken = "rollback_performed", Result = Wire(SetupLifecycleState.NoInstallation),
                    Detail = assessment.Detail, Errors = [], Warnings = [],
                });

            case RecoveryState.Corrupt:
                return Task.FromResult(new RecoverResponse
                {
                    Operation = "recover", Success = false, State = Wire(SetupLifecycleState.Inconsistent),
                    StateDetected = Wire(detectedState), PersistedState = summary,
                    ActionTaken = "none", Result = Wire(SetupLifecycleState.Inconsistent),
                    Detail = assessment.Detail,
                    Errors = [new CliError { Code = "state_inconsistent", Message = "état incohérent — intervention manuelle requise, aucune recovery automatique tentée", Detail = assessment.Detail }],
                    Warnings = [],
                });

            case RecoveryState.NoPriorInstallation:
                return Task.FromResult(new RecoverResponse
                {
                    Operation = "recover", Success = true, State = Wire(SetupLifecycleState.NoInstallation),
                    StateDetected = Wire(detectedState), PersistedState = summary,
                    ActionTaken = "none", Result = Wire(SetupLifecycleState.NoInstallation),
                    Detail = assessment.Detail, Errors = [], Warnings = [],
                });

            default: // Completed
                return Task.FromResult(new RecoverResponse
                {
                    Operation = "recover", Success = true, State = Wire(SetupLifecycleState.Completed),
                    StateDetected = Wire(detectedState), PersistedState = summary,
                    ActionTaken = "none", Result = Wire(SetupLifecycleState.Completed),
                    Detail = assessment.Detail, Errors = [], Warnings = [],
                });
        }
    }

    // -------------------------------------------------------------
    // setup validate
    // -------------------------------------------------------------

    public async Task<ValidateResponse> ValidateAsync(CancellationToken cancellationToken = default)
    {
        var versionResult = await runtimeAdapter.GetVersionAsync(cancellationToken).ConfigureAwait(false);
        var doctorResult = await runtimeAdapter.GetDoctorAsync(cancellationToken).ConfigureAwait(false);
        var gateResult = await runtimeAdapter.GetGateAsync(cancellationToken).ConfigureAwait(false);

        var reachable = versionResult.IsSuccess && doctorResult.IsSuccess && gateResult.IsSuccess;
        var errors = new List<CliError>();

        if (!versionResult.IsSuccess)
        {
            errors.Add(new CliError { Code = "version_unreachable", Message = "apex version --json inexploitable", Detail = versionResult.ErrorDetail });
        }
        if (!doctorResult.IsSuccess)
        {
            errors.Add(new CliError { Code = "doctor_unreachable", Message = "apex doctor --json inexploitable", Detail = doctorResult.ErrorDetail });
        }
        if (!gateResult.IsSuccess)
        {
            errors.Add(new CliError { Code = "gate_unreachable", Message = "apex gate --json inexploitable", Detail = gateResult.ErrorDetail });
        }

        // REVUE ADVERSARIALE PHASE 3 : `Status != Fail` NE SUFFIT PAS pour
        // Gate — un statut `DEGRADED` (distinct de `FAIL`, voir
        // docs/JSON_CONTRACTS.md côté DEV) peut très bien accompagner
        // `phase_2_authorized = false`. `check`/`install` décident déjà
        // sur `Phase2Authorized` exclusivement (voir `IsGateBlocking`) —
        // `validate` DOIT utiliser exactement le même critère, sous peine
        // de rapporter un succès pour une mesure Gate que `check`/`install`
        // auraient bloquée : deux commandes ne doivent jamais interpréter
        // la même mesure Gate différemment.
        var overallSuccess = reachable
            && doctorResult.Value!.Status != DoctorSeverity.Fail
            && gateResult.Value!.Phase2Authorized;

        if (reachable && !overallSuccess)
        {
            errors.Add(new CliError
            {
                Code = "validation_failed",
                Message = $"Doctor={doctorResult.Value!.Status}, Gate={gateResult.Value!.Status} (phase2_authorized={gateResult.Value.Phase2Authorized})",
            });
        }

        return new ValidateResponse
        {
            Operation = "validate",
            Success = overallSuccess,
            RuntimeReachable = reachable,
            Version = versionResult.IsSuccess
                ? new RuntimeVersionSummary
                {
                    Version = versionResult.Value!.Version, Phase = versionResult.Value.Phase,
                    PhaseName = versionResult.Value.PhaseName, Python = versionResult.Value.Python, Platform = versionResult.Value.Platform,
                }
                : null,
            Doctor = doctorResult.IsSuccess
                ? new DoctorSummary { Status = Wire(doctorResult.Value!.Status), Counts = doctorResult.Value.Counts, ResultsCount = doctorResult.Value.Results.Count }
                : null,
            Gate = gateResult.IsSuccess
                ? new GateSummary
                {
                    Status = Wire(gateResult.Value!.Status), Phase2Authorized = gateResult.Value.Phase2Authorized,
                    Blocking = gateResult.Value.Blocking, Unresolved = gateResult.Value.Unresolved,
                }
                : null,
            Errors = errors,
            Warnings = [],
        };
    }

    // -------------------------------------------------------------
    // setup status
    // -------------------------------------------------------------

    public Task<StatusResponse> StatusAsync(string destination, CancellationToken cancellationToken = default)
    {
        // REVUE ADVERSARIALE PHASE 3 (§3/§5 État transactionnel / races
        // status-recover) : le verrou est sondé D'ABORD, INCONDITIONNELLEMENT
        // — jamais seulement quand l'état déjà classifié laisse penser à une
        // interruption. Entre `InstallationLock.TryAcquire` (tout début de
        // `InstallationPipeline.RunAsync`) et la toute première
        // `InstallationStateStore.WriteAsync` (après la première étape
        // réussie), le verrou est RÉELLEMENT tenu mais rien n'est encore
        // persisté : `RecoveryClassifier.Classify` renvoie alors
        // `NoPriorInstallation`, et un ancien code qui ne sondait le verrou
        // QUE sur `InterruptedNeedsRollback` rapportait à tort « aucune
        // installation » pendant cette fenêtre de démarrage — reproduit et
        // corrigé (voir `StatusAsync_reports_in_progress_even_before_the_first_stage_has_persisted_any_state`).
        // Un verrou tenu par quelqu'un d'autre l'emporte TOUJOURS sur ce
        // que l'état persisté suggère : c'est le fait le plus récent et le
        // plus fiable disponible.
        bool lockHeld;
        using (var probe = InstallationLock.TryAcquire(destination))
        {
            // Sonde TRANSITOIRE : acquiert puis relâche immédiatement (le
            // `using` supprime le fichier de verrou qu'elle vient de créer,
            // si elle l'a créé) — effet net nul sur le disque, voir §11
            // Idempotence CLI (« status -> aucun effet secondaire »).
            lockHeld = probe is null;
        }

        var assessment = RecoveryClassifier.Classify(destination);
        var state = lockHeld ? SetupLifecycleState.InProgress : MapRecoveryState(assessment.State);
        var success = state is SetupLifecycleState.NoInstallation or SetupLifecycleState.Completed or SetupLifecycleState.InProgress;

        return Task.FromResult(new StatusResponse
        {
            Operation = "status",
            Success = success,
            State = Wire(state),
            LockCurrentlyHeld = lockHeld,
            PersistedState = ToSummary(assessment.PersistedState),
            Detail = assessment.Detail,
            Errors = success ? [] : [new CliError { Code = Wire(state), Message = assessment.Detail }],
            Warnings = [],
        });
    }

    // -------------------------------------------------------------
    // détail Doctor/Gate (pass-through, voir docstring de l'interface)
    // -------------------------------------------------------------

    public Task<RuntimeCallResult<RuntimeDoctorResult>> GetDoctorDetailAsync(CancellationToken cancellationToken = default) =>
        runtimeAdapter.GetDoctorAsync(cancellationToken);

    public Task<RuntimeCallResult<RuntimeGateResult>> GetGateDetailAsync(CancellationToken cancellationToken = default) =>
        runtimeAdapter.GetGateAsync(cancellationToken);

    // -------------------------------------------------------------
    // Internes partagés
    // -------------------------------------------------------------

    private async Task<(InstallationPlan? Plan, RuntimeCallResult<RuntimeGateResult>? GateResult, CliError? Error)> BuildPlanAsync(
        SetupPlanRequest request, CancellationToken cancellationToken)
    {
        var manifestResult = await ApplicationManifestLoader.LoadAsync(request.ManifestPath, cancellationToken).ConfigureAwait(false);
        if (!manifestResult.Success)
        {
            return (null, null, new CliError { Code = "manifest_load_failed", Message = "impossible de charger le manifeste modèle", Detail = manifestResult.ErrorMessage });
        }

        var profile = request.ProfileOverride ?? HardwareProfileEngine.SelectRecommendedProfile(ToDetectedHardware(hardwareDetector.Detect())).Tier;

        var gateResult = await runtimeAdapter.GetGateAsync(cancellationToken).ConfigureAwait(false);
        var dependencies = ClassifyDependencies(gateResult);

        string ResolveUrl(ModelManifestEntry m)
        {
            if (manifestResult.SourceUrlsByModelId.TryGetValue(m.ModelId, out var url))
            {
                return url;
            }
            throw new MissingSourceUrlException(m.ModelId);
        }

        InstallationPlan plan;
        try
        {
            plan = InstallationPlanBuilder.Build(
                request.TargetApexVersion, profile, manifestResult.Entries, request.Destination, dependencies, ResolveUrl);
        }
        catch (MissingSourceUrlException ex)
        {
            return (null, gateResult, new CliError
            {
                Code = "missing_source_url",
                Message = $"aucune URL de téléchargement (« source_url ») pour l'artefact « {ex.ModelId} »",
                Detail = "ajouter le champ « source_url » à l'entrée correspondante du fichier manifeste",
            });
        }

        return (plan, gateResult, null);
    }

    /// <summary>
    /// Classifie chaque dépendance manquante rapportée par le Gate en
    /// s'appuyant EXCLUSIVEMENT sur ce que le Gate a déjà lui-même décidé
    /// (<c>Blocking</c>/<c>Unresolved</c>) — jamais un seuil ou une règle
    /// inventée ici (voir contrainte explicite « Gate reste l'autorité de
    /// capacité »). Une dépendance nommée dans <c>Blocking</c> devient
    /// <see cref="DependencyRequirement.Required"/> ; sinon
    /// <see cref="DependencyRequirement.Missing"/> (gap reconnu, non
    /// bloquant pour l'instant) — jamais <see cref="DependencyRequirement.Unknown"/>,
    /// qui ferait échouer <c>PreflightChecker.CheckDependenciesKnown</c>.
    /// </summary>
    private static IReadOnlyDictionary<string, DependencyRequirement> ClassifyDependencies(RuntimeCallResult<RuntimeGateResult> gateResult)
    {
        if (!gateResult.IsSuccess)
        {
            return new Dictionary<string, DependencyRequirement>();
        }

        var blocking = gateResult.Value!.Blocking.ToHashSet(StringComparer.Ordinal);
        return gateResult.Value.MissingDependencies.ToDictionary(
            d => d.Name,
            d => blocking.Contains(d.Name) ? DependencyRequirement.Required : DependencyRequirement.Missing);
    }

    private static bool IsGateBlocking(RuntimeCallResult<RuntimeGateResult>? gateResult, out string detail)
    {
        if (gateResult is null || !gateResult.IsSuccess)
        {
            detail = "Gate inexploitable — impossible de déterminer l'autorisation";
            return true;
        }
        if (!gateResult.Value!.Phase2Authorized)
        {
            detail = $"Gate n'autorise pas la phase suivante (status={gateResult.Value.Status}, blocking=[{string.Join(", ", gateResult.Value.Blocking)}])";
            return true;
        }
        detail = $"Gate autorise la phase suivante (status={gateResult.Value.Status})";
        return false;
    }

    private static DetectedHardware ToDetectedHardware(DetectedMachine machine) => new()
    {
        RamTotalGb = machine.RamTotalGb, VramTotalGb = machine.VramTotalGb ?? 0,
        CpuThreads = machine.CpuThreads, DiskFreeGb = 0,
    };

    private IReadOnlyList<IInstallationStage> BuildPipelineStages() =>
    [
        new DownloadStage(new HttpArtifactDownloader(httpClient)),
        new IntegrityVerificationStage(),
        new InstallationStage(),
        new ConfigurationStage(),
        new ValidationStage(runtimeAdapter),
        new CommitStage(),
    ];

    private static string MapStageToErrorCode(string? stageName) => stageName switch
    {
        "download" => "download_failed",
        "integrity_verification" => "integrity_failed",
        "installation" or "configuration" or "commit" => "installation_failed",
        "validation" => "validation_failed",
        _ => "installation_failed",
    };

    private static InstallResponse Failed(string operation, CliError error, string state) => new()
    {
        Operation = operation, Success = false, State = state, Committed = false,
        ExecutedSteps = [], RolledBackSteps = [], FailedRollbackSteps = [],
        Errors = [error], Warnings = [],
    };

    /// <summary>
    /// Projette <see cref="RecoveryState"/> (4 valeurs Core) vers
    /// <see cref="SetupLifecycleState"/> (contrat CLI) — appelée
    /// UNIQUEMENT quand l'appelant sait déjà, par ailleurs, que personne
    /// ne tient le verrou (voir revue adversariale Phase 3 : la décision
    /// « in_progress » ne se prend plus ICI mais AVANT, à partir d'une
    /// sonde de verrou inconditionnelle — <see cref="StatusAsync"/> et
    /// <see cref="RecoverAsync"/> l'appellent tous deux uniquement après
    /// avoir déjà établi l'exclusivité).
    /// </summary>
    private static SetupLifecycleState MapRecoveryState(RecoveryState state) => state switch
    {
        RecoveryState.NoPriorInstallation => SetupLifecycleState.NoInstallation,
        RecoveryState.Completed => SetupLifecycleState.Completed,
        RecoveryState.Corrupt => SetupLifecycleState.Inconsistent,
        RecoveryState.InterruptedNeedsRollback => SetupLifecycleState.RecoveryNeeded,
        _ => SetupLifecycleState.Inconsistent,
    };

    /// <summary>
    /// Convertit un enum en son texte de sortie du contrat CLI —
    /// snake_case, exactement la même convention que
    /// <c>JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower)</c>
    /// appliqué aux propriétés TYPÉES enum (voir <c>Program.JsonOptions</c>).
    /// Ces champs-ci sont typés <c>string</c> (pour rester lisibles côté
    /// contrat sans exposer le type C# interne), donc convertis ici
    /// explicitement plutôt que par le convertisseur JSON — même résultat
    /// final, jamais deux conventions de casse différentes dans une même
    /// réponse.
    /// </summary>
    private static string Wire(Enum value) => System.Text.Json.JsonNamingPolicy.SnakeCaseLower.ConvertName(value.ToString());

    private static PersistedStateSummary? ToSummary(InstallationState? state) => state is null
        ? null
        : new PersistedStateSummary
        {
            PlanFingerprint = state.PlanFingerprint, RequiredArtifactIds = state.RequiredArtifactIds,
            CompletedStageNames = state.CompletedStageNames, Phase = Wire(state.Phase), UpdatedAtUtc = state.UpdatedAtUtc,
        };

    private sealed class MissingSourceUrlException(string modelId) : Exception
    {
        public string ModelId { get; } = modelId;
    }
}
