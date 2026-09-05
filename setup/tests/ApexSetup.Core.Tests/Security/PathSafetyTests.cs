using ApexSetup.Core.Security;
using Xunit;

namespace ApexSetup.Core.Tests.Security;

public class PathSafetyTests
{
    [Theory]
    [InlineData("model-a.bin")]
    [InlineData("qwen3-coder-30b-a3b-instruct")]
    [InlineData("file with spaces.bin")]
    [InlineData("file.with.dots.bin")]
    public void IsSafeFileName_accepts_ordinary_artifact_names(string candidate)
    {
        Assert.True(PathSafety.IsSafeFileName(candidate, out var reason), reason);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("..")]
    [InlineData(".")]
    [InlineData("../evil")]
    [InlineData("../../etc/passwd")]
    [InlineData("a/../../b")]
    [InlineData("/etc/passwd")]
    [InlineData("C:\\Windows\\System32\\evil.dll")]
    [InlineData("\\\\server\\share\\file")]
    [InlineData("//server/share/file")]
    [InlineData("sub/dir/file.bin")]
    [InlineData("sub\\dir\\file.bin")]
    [InlineData("CON")]
    [InlineData("con.bin")]
    [InlineData("PRN")]
    [InlineData("NUL")]
    [InlineData("COM1")]
    [InlineData("LPT1.txt")]
    [InlineData("trailing.dot.")]
    [InlineData("trailing space ")]
    [InlineData("has|pipe")]
    [InlineData("has<bracket")]
    [InlineData("has*star")]
    [InlineData("has?question")]
    [InlineData("has\"quote")]
    public void IsSafeFileName_rejects_dangerous_or_invalid_names(string candidate)
    {
        Assert.False(PathSafety.IsSafeFileName(candidate, out var reason));
        Assert.NotEmpty(reason);
    }

    [Fact]
    public void IsSafeFileName_rejects_embedded_null_character()
    {
        Assert.False(PathSafety.IsSafeFileName("evil\0.bin", out _));
    }

    [Fact]
    public void SafeCombine_returns_a_path_strictly_inside_the_base_directory_for_a_safe_name()
    {
        var baseDir = Directory.CreateTempSubdirectory("apex-setup-pathsafety-").FullName;
        try
        {
            var combined = PathSafety.SafeCombine(baseDir, "model-a.bin");
            Assert.StartsWith(Path.GetFullPath(baseDir), combined);
            Assert.Equal(Path.Combine(baseDir, "model-a.bin"), combined);
        }
        finally
        {
            Directory.Delete(baseDir, recursive: true);
        }
    }

    [Theory]
    [InlineData("../../../etc/passwd")]
    [InlineData("/etc/passwd")]
    [InlineData("..")]
    public void SafeCombine_throws_UnsafePathException_rather_than_silently_escaping_the_base_directory(string malicious)
    {
        var baseDir = Directory.CreateTempSubdirectory("apex-setup-pathsafety-").FullName;
        try
        {
            Assert.Throws<UnsafePathException>(() => PathSafety.SafeCombine(baseDir, malicious));
        }
        finally
        {
            Directory.Delete(baseDir, recursive: true);
        }
    }
}
