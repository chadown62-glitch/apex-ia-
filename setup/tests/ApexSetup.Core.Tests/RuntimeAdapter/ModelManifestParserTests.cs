using System.Text.Json;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Json;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.RuntimeAdapter;

public class ModelManifestParserTests
{
    [Fact]
    public void ParseManifest_parses_a_valid_manifest()
    {
        using var doc = JsonDocument.Parse(SampleJsonPayloads.ModelManifestValid);

        var entries = ModelManifestParser.ParseManifest(doc.RootElement);

        Assert.Single(entries);
        Assert.Equal("devstral-2-22b", entries[0].ModelId);
        Assert.Equal(14.0, entries[0].RequiredVramGb);
        Assert.Contains(HardwareProfileTier.Standard, entries[0].SupportedProfiles);
    }

    [Fact]
    public void ParseManifest_throws_ContractValidationException_when_sha256_is_missing()
    {
        var broken = SampleJsonPayloads.ModelManifestValid.Replace(
            "\"sha256\": \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",", "");
        using var doc = JsonDocument.Parse(broken);

        var ex = Assert.Throws<ContractValidationException>(() => ModelManifestParser.ParseManifest(doc.RootElement));

        Assert.Equal(Core.RuntimeAdapter.RuntimeCallOutcome.MissingRequiredField, ex.Outcome);
    }

    [Fact]
    public void ParseManifest_throws_for_an_unrecognized_supported_profile_value()
    {
        var broken = SampleJsonPayloads.ModelManifestValid.Replace("\"STANDARD\"", "\"ULTRA_EXTREME\"");
        using var doc = JsonDocument.Parse(broken);

        var ex = Assert.Throws<ContractValidationException>(() => ModelManifestParser.ParseManifest(doc.RootElement));

        Assert.Equal(Core.RuntimeAdapter.RuntimeCallOutcome.InvalidEnumValue, ex.Outcome);
    }

    [Fact]
    public void ParseManifest_throws_for_a_non_numeric_required_vram_gb()
    {
        var broken = SampleJsonPayloads.ModelManifestValid.Replace("\"required_vram_gb\": 14.0,", "\"required_vram_gb\": \"beaucoup\",");
        using var doc = JsonDocument.Parse(broken);

        var ex = Assert.Throws<ContractValidationException>(() => ModelManifestParser.ParseManifest(doc.RootElement));

        Assert.Equal(Core.RuntimeAdapter.RuntimeCallOutcome.UnexpectedFieldType, ex.Outcome);
    }

    [Fact]
    public void ParseManifest_throws_JsonException_for_syntactically_invalid_json()
    {
        // JsonReaderException (interne à System.Text.Json) hérite de
        // JsonException — ThrowsAny couvre le sous-type, contrairement à
        // Throws<T> qui exige le type exact.
        Assert.ThrowsAny<JsonException>(() => JsonDocument.Parse("[{ceci n'est pas du json"));
    }
}
