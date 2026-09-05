using ApexSetup.Core.Contracts;
using Xunit;

namespace ApexSetup.Core.Tests.RuntimeAdapter;

public class RuntimeSchemaPolicyTests
{
    [Theory]
    [InlineData(1, 1, true)]
    [InlineData(0, 1, true)]
    [InlineData(2, 1, false)]
    [InlineData(99, 1, false)]
    public void IsKnown_never_accepts_a_version_higher_than_the_one_this_setup_knows(
        int observed, int known, bool expected)
    {
        Assert.Equal(expected, RuntimeSchemaPolicy.IsKnown(observed, known));
    }

    [Fact]
    public void Doctor_and_gate_schema_counters_are_independent_constants()
    {
        // Même règle que docs/JSON_CONTRACTS.md côté Runtime : deux
        // compteurs indépendants, jamais couplés.
        Assert.Equal(1, RuntimeSchemaPolicy.KnownDoctorSchemaVersion);
        Assert.Equal(1, RuntimeSchemaPolicy.KnownGateSchemaVersion);
    }
}
