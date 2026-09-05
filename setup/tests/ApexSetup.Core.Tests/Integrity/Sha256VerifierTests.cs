using System.Text;
using ApexSetup.Core.Integrity;
using Xunit;

namespace ApexSetup.Core.Tests.Integrity;

public class Sha256VerifierTests
{
    [Fact]
    public async Task VerifyFileAsync_returns_Match_for_correct_hash()
    {
        var path = Path.Combine(Path.GetTempPath(), $"apex-setup-sha-{Guid.NewGuid():N}.bin");
        var content = Encoding.UTF8.GetBytes("contenu réel d'artefact APEX Runtime");
        await File.WriteAllBytesAsync(path, content);
        var expected = Sha256Verifier.ComputeSha256(content);

        try
        {
            var result = await Sha256Verifier.VerifyFileAsync(path, expected);

            Assert.Equal(IntegrityCheckOutcome.Match, result.Outcome);
            Assert.True(result.IsValid);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task VerifyFileAsync_returns_Mismatch_for_an_incorrect_hash_never_installing_silently()
    {
        var path = Path.Combine(Path.GetTempPath(), $"apex-setup-sha-{Guid.NewGuid():N}.bin");
        await File.WriteAllBytesAsync(path, Encoding.UTF8.GetBytes("contenu réel"));
        var wrongHash = new string('0', 64);

        try
        {
            var result = await Sha256Verifier.VerifyFileAsync(path, wrongHash);

            Assert.Equal(IntegrityCheckOutcome.Mismatch, result.Outcome);
            Assert.False(result.IsValid);
            Assert.NotEqual(wrongHash, result.ActualSha256);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task VerifyFileAsync_returns_FileNotFound_rather_than_throwing_for_a_missing_file()
    {
        var missingPath = Path.Combine(Path.GetTempPath(), $"apex-setup-does-not-exist-{Guid.NewGuid():N}.bin");

        var result = await Sha256Verifier.VerifyFileAsync(missingPath, new string('a', 64));

        Assert.Equal(IntegrityCheckOutcome.FileNotFound, result.Outcome);
        Assert.False(result.IsValid);
    }

    [Fact]
    public void ComputeSha256_is_deterministic_for_identical_content()
    {
        var content = Encoding.UTF8.GetBytes("déterminisme requis");

        var a = Sha256Verifier.ComputeSha256(content);
        var b = Sha256Verifier.ComputeSha256(content);

        Assert.Equal(a, b);
        Assert.Equal(64, a.Length); // hex SHA-256
    }
}
