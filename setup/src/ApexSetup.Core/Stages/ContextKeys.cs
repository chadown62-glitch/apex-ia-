namespace ApexSetup.Core.Stages;

/// <summary>Clés partagées de <c>InstallationContext.Items</c> — un seul endroit pour éviter une faute de frappe entre deux étapes.</summary>
internal static class ContextKeys
{
    /// <summary>Dictionnaire ArtifactId -&gt; chemin final téléchargé (rempli par <see cref="DownloadStage"/>).</summary>
    public const string DownloadedArtifactPaths = "downloaded_artifact_paths";

    /// <summary>Liste des chemins installés en destination (remplie par <see cref="InstallationStage"/>, utilisée pour le rollback).</summary>
    public const string InstalledFilePaths = "installed_file_paths";

    /// <summary>
    /// Dictionnaire chemin final -&gt; chemin de sauvegarde, UNIQUEMENT pour
    /// les fichiers qui existaient déjà avant cette exécution (remplie par
    /// <see cref="InstallationStage"/>) — voir revue adversariale §1/§2 :
    /// un rollback qui écraserait un fichier PRÉEXISTANT doit le
    /// RESTAURER, jamais simplement le supprimer.
    /// </summary>
    public const string InstalledFileBackups = "installed_file_backups";

    /// <summary>Chemin du fichier de configuration écrit (rempli par <see cref="ConfigurationStage"/>).</summary>
    public const string ConfigurationFilePath = "configuration_file_path";

    /// <summary>Chemin de sauvegarde de la configuration préexistante, si applicable (voir <see cref="InstalledFileBackups"/>).</summary>
    public const string ConfigurationBackupPath = "configuration_backup_path";
}
