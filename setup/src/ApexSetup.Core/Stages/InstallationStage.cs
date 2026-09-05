using ApexSetup.Core.Integrity;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Security;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 3 : copie chaque artefact vérifié vers
/// <c>{DestinationDirectory}/artifacts/{ArtifactId}</c>.
///
/// CORRIGÉ EN REVUE ADVERSARIALE :
/// §1/§2 (rollback/remplacement de fichier) — si un fichier du même nom
///   existait déjà (réinstallation, mise à niveau), il est SAUVEGARDÉ
///   (<c>.installation-backup</c>) avant d'être écrasé ; un rollback
///   ultérieur RESTAURE ce contenu au lieu de supprimer purement et
///   simplement un fichier qui n'était pas nouveau.
/// §5 (TOCTOU) — le hash attendu est RE-VÉRIFIÉ sur le fichier temporaire
///   fraîchement écrit, juste avant le renommage atomique, plutôt que de
///   faire confiance à la vérification déjà faite par
///   <c>IntegrityVerificationStage</c> sur le cache de
///   <c>WorkingDirectory</c> (qui reste ouvert entre les étapes — fenêtre
///   de substitution possible en théorie sur un système multi-processus).
/// §6 (chemins) — <c>artifact.ArtifactId</c> passe par
///   <see cref="PathSafety.SafeCombine"/>, jamais un <c>Path.Combine</c> nu.
/// §10 (idempotence) — si le fichier final existe déjà et correspond DÉJÀ
///   au hash attendu, l'étape ne le touche pas du tout : ni sauvegarde, ni
///   copie, ni renommage. Réexécuter l'étape sur un état déjà correct ne
///   doit produire ni fichier <c>.installation-backup</c> superflu, ni
///   écriture inutile.
/// </summary>
public sealed class InstallationStage : IInstallationStage
{
    public string Name => "installation";

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var downloadedPaths = context.GetRequiredItem<Dictionary<string, string>>(ContextKeys.DownloadedArtifactPaths);
        var artifactsDirectory = Path.Combine(context.DestinationDirectory, "artifacts");

        try
        {
            Directory.CreateDirectory(artifactsDirectory);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return StageOutcome.Failed($"impossible de créer {artifactsDirectory} : {ex.Message}");
        }

        var installedPaths = new List<string>();
        var backups = new Dictionary<string, string>();

        foreach (var artifact in context.Plan.RequiredArtifacts)
        {
            string finalPath;
            try
            {
                finalPath = PathSafety.SafeCombine(artifactsDirectory, artifact.ArtifactId);
            }
            catch (UnsafePathException ex)
            {
                UndoPartial(installedPaths, backups);
                return StageOutcome.Failed($"artefact « {artifact.ArtifactId} » : {ex.Message}");
            }

            if (File.Exists(finalPath))
            {
                var existing = await Sha256Verifier.VerifyFileAsync(finalPath, artifact.Sha256, cancellationToken).ConfigureAwait(false);
                if (existing.IsValid)
                {
                    // Déjà installé, contenu déjà correct : rien à faire.
                    // Ni sauvegarde ni écriture — sinon une réexécution sur
                    // un état déjà bon laisserait un
                    // « .installation-backup » qui ne sert jamais.
                    // Volontairement PAS ajouté à `installedPaths` : ce
                    // fichier n'a pas été touché par cette exécution, donc
                    // un rollback déclenché par l'échec d'un artefact
                    // suivant ne doit ni le supprimer ni le restaurer — il
                    // doit rester exactement tel qu'il était.
                    continue;
                }
            }

            var sourcePath = downloadedPaths[artifact.ArtifactId];
            var tempPath = finalPath + ".installing";
            var preExisted = File.Exists(finalPath);
            string? backupPath = null;

            try
            {
                if (preExisted)
                {
                    backupPath = finalPath + ".installation-backup";
                    File.Copy(finalPath, backupPath, overwrite: true);
                }

                await using (var source = File.OpenRead(sourcePath))
                await using (var dest = new FileStream(tempPath, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    await source.CopyToAsync(dest, cancellationToken).ConfigureAwait(false);
                }

                // Fermeture de la fenêtre TOCTOU : on revérifie le hash SUR
                // LE FICHIER QU'ON VIENT D'ÉCRIRE, jamais sur la base d'une
                // vérification antérieure d'un fichier partagé entre étapes.
                var integrity = await Sha256Verifier.VerifyFileAsync(tempPath, artifact.Sha256, cancellationToken).ConfigureAwait(false);
                if (!integrity.IsValid)
                {
                    TryDelete(tempPath);
                    if (backupPath is not null)
                    {
                        TryDelete(backupPath);
                    }
                    UndoPartial(installedPaths, backups);
                    return StageOutcome.Failed(
                        $"artefact « {artifact.ArtifactId} » : intégrité invalide juste avant installation " +
                        $"({integrity.Outcome}, attendu {artifact.Sha256}, obtenu {integrity.ActualSha256 ?? "(illisible)"})");
                }

                File.Move(tempPath, finalPath, overwrite: true);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                TryDelete(tempPath);
                if (backupPath is not null)
                {
                    // Restaure immédiatement si la copie a échoué APRÈS la sauvegarde.
                    RestoreBackup(finalPath, backupPath);
                }
                UndoPartial(installedPaths, backups);
                return StageOutcome.Failed($"copie de « {artifact.ArtifactId} » vers {finalPath} : {ex.Message}");
            }

            installedPaths.Add(finalPath);
            if (backupPath is not null)
            {
                backups[finalPath] = backupPath;
            }
        }

        context.SetItem(ContextKeys.InstalledFilePaths, installedPaths);
        context.SetItem(ContextKeys.InstalledFileBackups, backups);
        return StageOutcome.Ok();
    }

    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var installed = context.TryGetItem<List<string>>(ContextKeys.InstalledFilePaths, out var i) ? i! : [];
        var backups = context.TryGetItem<Dictionary<string, string>>(ContextKeys.InstalledFileBackups, out var b) ? b! : new();
        UndoPartial(installed, backups);
        return Task.CompletedTask;
    }

    /// <summary>
    /// Défait EXACTEMENT ce qui a été fait : restaure les fichiers qui
    /// préexistaient (sauvegarde présente), supprime ceux qui étaient
    /// réellement nouveaux (aucune sauvegarde) — jamais un simple
    /// « tout supprimer » qui perdrait le contenu d'origine d'un fichier
    /// remplacé (voir revue adversariale §1/§2).
    /// </summary>
    private static void UndoPartial(List<string> installedPaths, Dictionary<string, string> backups)
    {
        foreach (var path in installedPaths)
        {
            if (backups.TryGetValue(path, out var backupPath))
            {
                RestoreBackup(path, backupPath);
            }
            else
            {
                TryDelete(path);
            }
        }
    }

    private static void RestoreBackup(string finalPath, string backupPath)
    {
        try
        {
            if (File.Exists(backupPath))
            {
                File.Move(backupPath, finalPath, overwrite: true);
            }
        }
        catch (IOException)
        {
            // Best-effort — voir InstallationPipeline.RollbackAsync.
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (IOException)
        {
            // Best-effort — voir InstallationPipeline.RollbackAsync.
        }
    }
}
