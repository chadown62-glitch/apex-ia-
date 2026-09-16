using System.Globalization;
using ApexSetup.Dashboard.Models;
using Avalonia.Data.Converters;
using Avalonia.Media;

namespace ApexSetup.Dashboard.Converters;

/// <summary>Couleur du point d'état — purement présentationnel, ne participe à aucune décision (voir <see cref="ComponentStatus"/> pour le vocabulaire réel).</summary>
public sealed class ComponentStatusToBrushConverter : IValueConverter
{
    public static readonly ComponentStatusToBrushConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => value switch
    {
        ComponentStatus.Running => Brushes.LimeGreen,
        ComponentStatus.Starting => Brushes.Orange,
        ComponentStatus.Stopped => Brushes.Gray,
        ComponentStatus.Error => Brushes.Crimson,
        ComponentStatus.NotInstalled => Brushes.DimGray,
        ComponentStatus.Unavailable => Brushes.SlateGray,
        _ => Brushes.Goldenrod,
    };

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
