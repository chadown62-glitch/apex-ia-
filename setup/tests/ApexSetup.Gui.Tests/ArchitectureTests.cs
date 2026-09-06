using System.Reflection;
using ApexSetup.Gui;
using Xunit;

namespace ApexSetup.Gui.Tests;

/// <summary>
/// PHASE 4 : vérifie automatiquement, à chaque exécution de la suite, que
/// le GUI ne référence jamais un détail d'implémentation interne de
/// <c>ApexSetup.Core</c> — la liste exacte donnée par la mission :
/// <c>InstallationStage</c>, <c>DownloadStage</c>, <c>PathSafety</c>,
/// <c>InstallationLock</c>, <c>RecoveryClassifier</c>,
/// <c>RuntimeProcessRunner</c>, ainsi que la classe <c>RuntimeAdapter</c>
/// elle-même (le GUI parle uniquement <c>ISetupApplicationService</c>,
/// jamais l'adaptateur Runtime directement).
///
/// LIMITE ASSUMÉE : ceci inspecte les SIGNATURES publiques et privées
/// (types de base, interfaces, champs, propriétés, paramètres/retour de
/// méthode) de chaque type du binaire GUI — pas le corps des méthodes
/// (IL complet). Un GUI qui utiliserait un type interdit UNIQUEMENT comme
/// variable locale sans jamais l'exposer en signature échapperait à ce
/// test ; en pratique, tout usage réel d'un stage/verrou/adaptateur
/// Runtime finit par apparaître dans au moins une signature (paramètre de
/// constructeur, champ, etc.), donc ce contrôle reste une garde-fou
/// pertinente, documentée comme partielle plutôt que présentée comme
/// exhaustive.
///
/// <c>HardwareProfileTier</c> (Contracts, donnée pure à 4 valeurs, déjà
/// exposée publiquement par <c>SetupPlanRequest.ProfileOverride</c>) n'est
/// PAS dans la liste interdite — voir <c>SetupSession.cs</c> pour la
/// justification : un type de données pur au contrat, jamais un détail
/// d'implémentation.
/// </summary>
public class ArchitectureTests
{
    private static readonly string[] ForbiddenCoreTypeNames =
    [
        "ApexSetup.Core.Stages.InstallationStage",
        "ApexSetup.Core.Stages.DownloadStage",
        "ApexSetup.Core.Stages.ConfigurationStage",
        "ApexSetup.Core.Stages.ValidationStage",
        "ApexSetup.Core.Stages.IntegrityVerificationStage",
        "ApexSetup.Core.Stages.CommitStage",
        "ApexSetup.Core.Security.PathSafety",
        "ApexSetup.Core.Pipeline.InstallationLock",
        "ApexSetup.Core.Pipeline.InstallationPipeline",
        "ApexSetup.Core.Pipeline.InstallationRecovery",
        "ApexSetup.Core.Pipeline.RecoveryClassifier",
        "ApexSetup.Core.Pipeline.InstallationStateStore",
        "ApexSetup.Core.RuntimeAdapter.RuntimeProcessRunner",
        "ApexSetup.Core.RuntimeAdapter.RuntimeAdapter",
    ];

    [Fact]
    public void Gui_assembly_never_references_a_forbidden_Core_internal_type_in_any_signature()
    {
        var guiAssembly = typeof(App).Assembly;
        var offenders = new List<string>();

        foreach (var type in guiAssembly.GetTypes())
        {
            foreach (var referenced in CollectSignatureTypes(type))
            {
                var fullName = referenced.FullName;
                if (fullName is not null && ForbiddenCoreTypeNames.Contains(fullName))
                {
                    offenders.Add($"{type.FullName} référence {fullName}");
                }
            }
        }

        Assert.True(offenders.Count == 0, "Types Core interdits référencés par le GUI :\n" + string.Join("\n", offenders));
    }

    [Fact]
    public void Gui_project_never_directly_references_the_ApexSetup_Core_assembly_project()
    {
        // Vérifie le fichier projet lui-même (pas seulement les types
        // utilisés) — aucune ProjectReference vers ApexSetup.Core, même
        // inutilisée, ne doit jamais y être ajoutée : la frontière doit
        // rester ISetupApplicationService, jamais un accès facilité à Core.
        var csprojPath = FindGuiCsproj();
        var content = File.ReadAllText(csprojPath);

        Assert.DoesNotContain("ApexSetup.Core.csproj", content);
    }

    private static IEnumerable<Type> CollectSignatureTypes(Type type)
    {
        if (type.BaseType is not null) yield return type.BaseType;
        foreach (var i in type.GetInterfaces()) yield return i;

        const BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly;

        foreach (var field in type.GetFields(flags))
        {
            yield return field.FieldType;
        }
        foreach (var prop in type.GetProperties(flags))
        {
            yield return prop.PropertyType;
        }
        foreach (var method in type.GetMethods(flags))
        {
            yield return method.ReturnType;
            foreach (var p in method.GetParameters())
            {
                yield return p.ParameterType;
            }
        }
        foreach (var ctor in type.GetConstructors(flags))
        {
            foreach (var p in ctor.GetParameters())
            {
                yield return p.ParameterType;
            }
        }
    }

    private static string FindGuiCsproj()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            var candidate = Path.Combine(dir.FullName, "src", "ApexSetup.Gui", "ApexSetup.Gui.csproj");
            if (File.Exists(candidate))
            {
                return candidate;
            }
            dir = dir.Parent;
        }
        throw new InvalidOperationException("ApexSetup.Gui.csproj introuvable depuis " + AppContext.BaseDirectory);
    }
}
