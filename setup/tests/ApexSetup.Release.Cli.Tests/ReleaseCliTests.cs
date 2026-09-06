using ApexSetup.Release;
using Xunit;

namespace ApexSetup.Release.Cli.Tests;

public class ReleaseCliTests : IDisposable
{
    private readonly string _dir = Directory.CreateTempSubdirectory("apex-release-cli-").FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private async Task<string> WriteArtifactAsync(byte[] content, string name = "apex-setup.exe")
    {
        var path = Path.Combine(_dir, name);
        await File.WriteAllBytesAsync(path, content);
        return path;
    }

    [Fact]
    public async Task Generate_then_validate_round_trips_through_the_real_binaries_never_only_the_library()
    {
        var artifactPath = await WriteArtifactAsync("contenu réel de l'artefact"u8.ToArray());
        var manifestPath = Path.Combine(_dir, "manifest.json");

        var generate = await ReleaseCliProcess.RunAsync(
            "generate", "--artifact", artifactPath, "--out", manifestPath,
            "--product-name", "APEX Setup", "--setup-version", "0.1.0", "--architecture", "win-x64");

        Assert.Equal(0, generate.ExitCode);
        Assert.True(File.Exists(manifestPath));

        var manifest = ReleaseManifestJson.TryRead(manifestPath);
        Assert.NotNull(manifest);
        Assert.Equal("apex-setup.exe", manifest!.ArtifactFileName);
        Assert.Null(manifest.RuntimeVersion);
        Assert.Null(manifest.BuildCommitSha);

        var validate = await ReleaseCliProcess.RunAsync(
            "validate", "--manifest", manifestPath, "--artifact-dir", _dir,
            "--architecture", "win-x64", "--setup-version", "0.1.0");

        Assert.Equal(0, validate.ExitCode);
        Assert.Contains("Valid", validate.Stdout);
    }

    [Fact]
    public async Task Generate_accepts_optional_runtime_version_and_commit_sha()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifestPath = Path.Combine(_dir, "manifest.json");

        var generate = await ReleaseCliProcess.RunAsync(
            "generate", "--artifact", artifactPath, "--out", manifestPath,
            "--product-name", "APEX Setup", "--setup-version", "0.1.0", "--architecture", "win-x64",
            "--runtime-version", "2.3.4", "--commit-sha", "deadbeef");

        Assert.Equal(0, generate.ExitCode);
        var manifest = ReleaseManifestJson.TryRead(manifestPath);
        Assert.Equal("2.3.4", manifest!.RuntimeVersion);
        Assert.Equal("deadbeef", manifest.BuildCommitSha);
    }

    [Fact]
    public async Task Validate_exits_1_and_reports_HashMismatch_for_a_tampered_artifact_never_silently_accepts_it()
    {
        var artifactPath = await WriteArtifactAsync("contenu original"u8.ToArray());
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseCliProcess.RunAsync(
            "generate", "--artifact", artifactPath, "--out", manifestPath,
            "--product-name", "APEX Setup", "--setup-version", "0.1.0", "--architecture", "win-x64");

        await File.WriteAllBytesAsync(artifactPath, "CONTENU-MODIFIE!"u8.ToArray());

        var validate = await ReleaseCliProcess.RunAsync(
            "validate", "--manifest", manifestPath, "--artifact-dir", _dir,
            "--architecture", "win-x64", "--setup-version", "0.1.0");

        Assert.Equal(1, validate.ExitCode);
        Assert.Contains("HashMismatch", validate.Stdout);
    }

    [Fact]
    public async Task Validate_exits_1_for_a_missing_manifest_never_crashes()
    {
        var result = await ReleaseCliProcess.RunAsync(
            "validate", "--manifest", Path.Combine(_dir, "no-such.json"), "--artifact-dir", _dir,
            "--architecture", "win-x64", "--setup-version", "0.1.0");

        Assert.Equal(1, result.ExitCode);
        Assert.Contains("ManifestUnreadable", result.Stdout);
    }

    [Fact]
    public async Task Generate_exits_2_for_a_missing_required_option_never_a_stack_trace()
    {
        var result = await ReleaseCliProcess.RunAsync("generate", "--artifact", "whatever");

        Assert.Equal(2, result.ExitCode);
        Assert.Contains("--out", result.Stderr);
        Assert.DoesNotContain("Exception", result.Stderr);
    }

    [Fact]
    public async Task Generate_exits_2_for_a_missing_artifact_file_never_fabricates_a_manifest()
    {
        var manifestPath = Path.Combine(_dir, "manifest.json");

        var result = await ReleaseCliProcess.RunAsync(
            "generate", "--artifact", Path.Combine(_dir, "does-not-exist.exe"), "--out", manifestPath,
            "--product-name", "APEX Setup", "--setup-version", "0.1.0", "--architecture", "win-x64");

        Assert.Equal(2, result.ExitCode);
        Assert.False(File.Exists(manifestPath));
    }

    [Fact]
    public async Task Unknown_command_exits_2_with_a_clear_message()
    {
        var result = await ReleaseCliProcess.RunAsync("bogus");

        Assert.Equal(2, result.ExitCode);
        Assert.Contains("bogus", result.Stderr);
    }

    [Fact]
    public async Task No_arguments_prints_usage_and_exits_0()
    {
        var result = await ReleaseCliProcess.RunAsync();

        Assert.Equal(0, result.ExitCode);
        Assert.Contains("generate", result.Stdout);
        Assert.Contains("validate", result.Stdout);
    }
}
