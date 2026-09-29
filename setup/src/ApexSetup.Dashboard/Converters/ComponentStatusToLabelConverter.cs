using System.Globalization;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using Avalonia.Data.Converters;

namespace ApexSetup.Dashboard.Converters;

/// <summary>Libellé affiché : exactement le vocabulaire du Panel (READY / STARTING / OFFLINE / DEGRADED / ERROR / UNKNOWN).</summary>
public sealed class ComponentStatusToLabelConverter : IValueConverter
{
    public static readonly ComponentStatusToLabelConverter Instance = new();

    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is ComponentStatus status ? StatusEngine.Label(status) : "UNKNOWN";

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
