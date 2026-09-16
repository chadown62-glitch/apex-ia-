using ApexSetup.Dashboard.Converters;
using ApexSetup.Dashboard.Models;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// RELEASE LOCK 2/2 : le tableau System Status affichait le nom brut de
/// l'enum (<c>NotInstalled</c>, <c>Unavailable</c>) plutôt qu'un libellé
/// produit fini. Preuve ici que chacune des 7 valeurs du vocabulaire réel
/// (mission §4) a un libellé lisible ET distinct — jamais deux états
/// différents rendus identiques à l'écran.
/// </summary>
public class ComponentStatusToLabelConverterTests
{
    [Theory]
    [InlineData(ComponentStatus.Running, "Ready")]
    [InlineData(ComponentStatus.Starting, "Starting…")]
    [InlineData(ComponentStatus.Stopped, "Stopped")]
    [InlineData(ComponentStatus.Error, "Error")]
    [InlineData(ComponentStatus.NotInstalled, "Not installed")]
    [InlineData(ComponentStatus.Unavailable, "Unavailable")]
    [InlineData(ComponentStatus.Unknown, "Unknown")]
    public void Converts_every_real_status_value_to_a_distinct_readable_label(ComponentStatus status, string expectedLabel)
    {
        var label = ComponentStatusToLabelConverter.Instance.Convert(status, typeof(string), null, System.Globalization.CultureInfo.InvariantCulture);

        Assert.Equal(expectedLabel, label);
    }

    [Fact]
    public void All_seven_labels_are_mutually_distinct()
    {
        var labels = Enum.GetValues<ComponentStatus>()
            .Select(s => ComponentStatusToLabelConverter.Instance.Convert(s, typeof(string), null, System.Globalization.CultureInfo.InvariantCulture))
            .ToList();

        Assert.Equal(labels.Count, labels.Distinct().Count());
    }
}
