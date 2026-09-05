namespace ApexSetup.Cli;

/// <summary>
/// Analyseur d'arguments MINIMAL et déterministe — jamais de shell, jamais
/// de ré-assemblage en chaîne : les arguments arrivent déjà tokenisés par
/// le système d'exploitation dans <c>args</c> (voir <c>Program.Main</c>) et
/// sont lus ici un par un, jamais concaténés ni réinterprétés (voir §13
/// Security du plan Phase 3). Un flag répétable (<c>--runtime-arg</c>)
/// s'accumule dans l'ordre reçu, sans transformation.
/// </summary>
public sealed class CliArguments
{
    private readonly Dictionary<string, List<string>> _values = new(StringComparer.Ordinal);
    private readonly HashSet<string> _flags = new(StringComparer.Ordinal);

    public static CliArguments Parse(IReadOnlyList<string> args, int startIndex)
    {
        var result = new CliArguments();
        var i = startIndex;
        while (i < args.Count)
        {
            var token = args[i];
            if (!token.StartsWith("--", StringComparison.Ordinal))
            {
                throw new CliArgumentException($"argument inattendu (ni option ni valeur associée) : « {token} »");
            }

            var name = token[2..];
            if (i + 1 < args.Count && !args[i + 1].StartsWith("--", StringComparison.Ordinal))
            {
                if (!result._values.TryGetValue(name, out var list))
                {
                    list = [];
                    result._values[name] = list;
                }
                list.Add(args[i + 1]);
                i += 2;
            }
            else
            {
                result._flags.Add(name);
                i += 1;
            }
        }
        return result;
    }

    public string? Get(string name) => _values.TryGetValue(name, out var list) ? list[^1] : null;

    /// <summary>
    /// Lit un argument obligatoire — lève si absent OU si sa valeur est
    /// vide/blanche. REVUE ADVERSARIALE PHASE 3 (§14 Argument parsing,
    /// « valeur vide ») : avant ce correctif, <c>--destination ""</c> ou
    /// <c>--manifest ""</c> passaient ce contrôle (la CLÉ existe bien),
    /// pour ensuite déclencher une <c>ArgumentException</c> non interceptée
    /// au premier <c>Path.GetFullPath</c>/<c>File.ReadAllTextAsync</c> côté
    /// Core/Application — rapportée comme <c>internal_error</c> (code 99)
    /// alors qu'il s'agit d'une erreur de PARAMÈTRE (code 1). Une valeur
    /// explicitement vide n'a aucun sens pour un chemin ou une version :
    /// rejetée ICI, à la frontière CLI, avant qu'elle n'atteigne quoi que
    /// ce soit d'autre.
    /// </summary>
    public string Require(string name)
    {
        var value = Get(name)
            ?? throw new CliArgumentException($"argument obligatoire manquant : « --{name} »");
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new CliArgumentException($"argument « --{name} » ne peut pas être vide");
        }
        return value;
    }

    public IReadOnlyList<string> GetAll(string name) => _values.TryGetValue(name, out var list) ? list : [];

    public bool HasFlag(string name) => _flags.Contains(name) || _values.ContainsKey(name);
}

public sealed class CliArgumentException(string message) : Exception(message);
