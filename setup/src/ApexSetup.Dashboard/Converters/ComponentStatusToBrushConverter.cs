using System.Globalization;
using ApexSetup.Dashboard.Models;
using Avalonia.Data.Converters;
using Avalonia.Media;

namespace ApexSetup.Dashboard.Converters;

/// <summary>Couleur du point d'état — purement présentationnel, ne participe à aucune décision.</summary>
public sealed class ComponentStatusToBrushConverter : IValueConverter
{
    public static readonly ComponentStatusToBrushConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => value switch
    {
        ComponentStatus.Ready => Brushes.LimeGreen,
        ComponentStatus.Starting => Brushes.DeepSkyBlue,
        ComponentStatus.Degraded => Brushes.Orange,
        ComponentStatus.Offline => Brushes.Gray,
        ComponentStatus.Error => Brushes.Crimson,
        _ => Brushes.Goldenrod,
    };

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>Couleur du verdict global (nul = aucune mesure encore : neutre, jamais vert).</summary>
public sealed class VerdictToBrushConverter : IValueConverter
{
    public static readonly VerdictToBrushConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => value switch
    {
        PanelVerdict.Ready => Brushes.LimeGreen,
        PanelVerdict.Degraded => Brushes.Orange,
        PanelVerdict.VersionMismatch => Brushes.MediumOrchid,
        PanelVerdict.NotReady => Brushes.Crimson,
        PanelVerdict.Blocked => Brushes.Crimson,
        _ => Brushes.Gray,
    };

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
