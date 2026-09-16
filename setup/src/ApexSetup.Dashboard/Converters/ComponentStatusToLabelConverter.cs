using System.Globalization;
using ApexSetup.Dashboard.Models;
using Avalonia.Data.Converters;

namespace ApexSetup.Dashboard.Converters;

/// <summary>
/// Libellé utilisateur — RELEASE LOCK 2/2 : le tableau System Status
/// affichait le nom brut de l'enum <see cref="ComponentStatus"/>
/// (<c>NotInstalled</c>, <c>Unavailable</c>...), lisible mais pas un
/// libellé produit fini. Purement présentationnel, comme
/// <see cref="ComponentStatusToBrushConverter"/> : ne change ni la valeur
/// mesurée ni la logique de mapping Doctor -> <see cref="ComponentStatus"/>
/// (voir <see cref="Services.RuntimeStatusService"/>), uniquement son texte.
/// </summary>
public sealed class ComponentStatusToLabelConverter : IValueConverter
{
    public static readonly ComponentStatusToLabelConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => value switch
    {
        ComponentStatus.Running => "Ready",
        ComponentStatus.Starting => "Starting…",
        ComponentStatus.Stopped => "Stopped",
        ComponentStatus.Error => "Error",
        ComponentStatus.NotInstalled => "Not installed",
        ComponentStatus.Unavailable => "Unavailable",
        ComponentStatus.Unknown => "Unknown",
        _ => "Unknown",
    };

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
