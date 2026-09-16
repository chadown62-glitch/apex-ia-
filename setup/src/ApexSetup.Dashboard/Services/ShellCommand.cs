namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Construction d'une commande <c>bash -lc "..."</c> — seul endroit du
/// Dashboard où un texte shell est assemblé (nécessaire pour <c>cd &amp;&amp;
/// commande</c>, qu'<c>ArgumentList</c> seul ne peut pas exprimer sans
/// invoquer un shell). Le chemin est TOUJOURS quoté en POSIX single-quote,
/// jamais interpolé nu — un chemin contenant une apostrophe ne casse jamais
/// la commande (voir <see cref="QuoteSingle"/>).
/// </summary>
public static class ShellCommand
{
    /// <summary>Échappement POSIX standard d'une apostrophe à l'intérieur d'une chaîne déjà entre apostrophes : <c>'</c> -&gt; <c>'\''</c>.</summary>
    public static string QuoteSingle(string value) => "'" + value.Replace("'", "'\\''") + "'";

    public static string CdAnd(string wslPath, string command) => $"cd {QuoteSingle(wslPath)} && {command}";
}
