using ApexSetup.Dashboard.Converters;
using ApexSetup.Dashboard.Models;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Le Panel affiche exactement le vocabulaire de la mission, six libellés distincts.</summary>
public class ComponentStatusToLabelConverterTests
{
    [Theory]
    [InlineData(ComponentStatus.Ready, "READY")]
    [InlineData(ComponentStatus.Starting, "STARTING")]
    [InlineData(ComponentStatus.Offline, "OFFLINE")]
    [InlineData(ComponentStatus.Degraded, "DEGRADED")]
    [InlineData(ComponentStatus.Error, "ERROR")]
    [InlineData(ComponentStatus.Unknown, "UNKNOWN")]
    public void Every_status_has_its_mission_label(ComponentStatus status, string expected) =>
        Assert.Equal(expected, ComponentStatusToLabelConverter.Instance.Convert(status, typeof(string), null, System.Globalization.CultureInfo.InvariantCulture));

    [Fact]
    public void Exactly_six_distinct_labels()
    {
        var labels = Enum.GetValues<ComponentStatus>()
            .Select(s => ComponentStatusToLabelConverter.Instance.Convert(s, typeof(string), null, System.Globalization.CultureInfo.InvariantCulture))
            .ToList();

        Assert.Equal(6, labels.Count);
        Assert.Equal(6, labels.Distinct().Count());
    }

    [Fact]
    public void No_measurement_is_never_shown_green()
    {
        Assert.NotEqual(Avalonia.Media.Brushes.LimeGreen, VerdictToBrushConverter.Instance.Convert(null, typeof(object), null, System.Globalization.CultureInfo.InvariantCulture));
    }
}
