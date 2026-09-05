using System.Net;
using System.Text;
using ApexSetup.Core.Download;
using ApexSetup.Core.Integrity;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Download;

/// <summary>
/// Tests RÉELS du moteur de téléchargement : un vrai serveur HTTP local
/// (<see cref="LocalHttpTestServer"/>), un vrai <see cref="HttpClient"/>,
/// un vrai système de fichiers temporaire. Aucun mock de la couche
/// réseau elle-même.
/// </summary>
public class HttpArtifactDownloaderTests : IAsyncDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-dl-").FullName;

    public async ValueTask DisposeAsync()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { /* best-effort */ }
        await Task.CompletedTask;
    }

    private static byte[] MakeContent(int size, byte seed = 42)
    {
        var buffer = new byte[size];
        for (var i = 0; i < size; i++) buffer[i] = (byte)((seed + i) % 251);
        return buffer;
    }

    [Fact]
    public async Task DownloadAsync_succeeds_and_never_writes_directly_to_the_final_path()
    {
        var content = MakeContent(4096);
        var expectedHash = Sha256Verifier.ComputeSha256(content);
        var finalPath = Path.Combine(_tempDir, "artifact.bin");
        var partPath = finalPath + ".part";

        // Transfert volontairement étalé dans le temps (petits blocs +
        // pause) pour laisser une fenêtre d'observation CÔTÉ CLIENT,
        // pendant que le transfert est réellement en cours (pas une
        // vérification côté serveur, qui ne dit rien de l'écriture disque
        // du client).
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            ctx.Response.ContentLength64 = content.Length;
            foreach (var chunk in content.Chunk(512))
            {
                await ctx.Response.OutputStream.WriteAsync(chunk);
                await ctx.Response.OutputStream.FlushAsync();
                await Task.Delay(15);
            }
            ctx.Response.Close();
        });

        using var httpClient = new HttpClient();
        var downloader = new HttpArtifactDownloader(httpClient);
        var request = new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("artifact"), DestinationPath = finalPath, ExpectedSha256 = expectedHash,
        };

        var downloadTask = downloader.DownloadAsync(request);

        var sawPartFileBeforeFinalExisted = false;
        while (!downloadTask.IsCompleted)
        {
            if (File.Exists(partPath) && !File.Exists(finalPath))
            {
                sawPartFileBeforeFinalExisted = true;
                break;
            }
            await Task.Delay(5);
        }

        var result = await downloadTask;

        Assert.True(result.IsSuccess, result.ErrorDetail);
        Assert.True(sawPartFileBeforeFinalExisted, "le fichier .part devait exister pendant le transfert, avant le fichier final");
        Assert.Equal(finalPath, result.FinalPath);
        Assert.Equal(expectedHash, result.ActualSha256);
        Assert.True(File.Exists(finalPath));
        Assert.False(File.Exists(partPath)); // "absence de fichier .part" après succès
        Assert.Equal(content, await File.ReadAllBytesAsync(finalPath));
    }

    [Fact]
    public async Task DownloadAsync_returns_HashMismatch_and_deletes_the_part_file_never_promoting_corrupt_content()
    {
        var content = MakeContent(1024);
        var wrongHash = new string('0', 64);
        var finalPath = Path.Combine(_tempDir, "artifact.bin");

        await using var server = new LocalHttpTestServer(async ctx =>
        {
            ctx.Response.ContentLength64 = content.Length;
            await ctx.Response.OutputStream.WriteAsync(content);
            ctx.Response.Close();
        });

        using var httpClient = new HttpClient();
        var downloader = new HttpArtifactDownloader(httpClient);
        var result = await downloader.DownloadAsync(new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("artifact"), DestinationPath = finalPath, ExpectedSha256 = wrongHash,
        });

        Assert.Equal(ArtifactDownloadOutcome.HashMismatch, result.Outcome);
        Assert.False(File.Exists(finalPath));
        Assert.False(File.Exists(finalPath + ".part"));
        Assert.Null(result.ResumablePartialPath);
    }

    [Fact]
    public async Task DownloadAsync_resumes_from_a_real_partial_file_using_a_range_request()
    {
        var content = MakeContent(8192);
        var expectedHash = Sha256Verifier.ComputeSha256(content);
        var finalPath = Path.Combine(_tempDir, "artifact.bin");
        var partPath = finalPath + ".part";

        // Simule un .part déjà partiellement téléchargé par une exécution précédente.
        await File.WriteAllBytesAsync(partPath, content[..4096]);

        var rangeHeaderSeen = string.Empty;
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            rangeHeaderSeen = ctx.Request.Headers["Range"] ?? string.Empty;
            if (ctx.Request.Headers["Range"] is { } range && range.StartsWith("bytes=4096"))
            {
                var remainder = content[4096..];
                ctx.Response.StatusCode = (int)HttpStatusCode.PartialContent;
                ctx.Response.Headers["Content-Range"] = $"bytes 4096-{content.Length - 1}/{content.Length}";
                ctx.Response.ContentLength64 = remainder.Length;
                await ctx.Response.OutputStream.WriteAsync(remainder);
            }
            else
            {
                ctx.Response.ContentLength64 = content.Length;
                await ctx.Response.OutputStream.WriteAsync(content);
            }
            ctx.Response.Close();
        });

        using var httpClient = new HttpClient();
        var downloader = new HttpArtifactDownloader(httpClient);
        var result = await downloader.DownloadAsync(new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("artifact"), DestinationPath = finalPath, ExpectedSha256 = expectedHash,
        });

        Assert.True(result.IsSuccess, result.ErrorDetail);
        Assert.StartsWith("bytes=4096", rangeHeaderSeen);
        Assert.Equal(content, await File.ReadAllBytesAsync(finalPath));
    }

    [Fact]
    public async Task DownloadAsync_returns_NetworkError_and_keeps_the_part_file_when_the_connection_is_interrupted_mid_transfer()
    {
        var content = MakeContent(1_000_000); // assez gros pour laisser le temps de couper
        var finalPath = Path.Combine(_tempDir, "artifact.bin");

        await using var server = new LocalHttpTestServer(async ctx =>
        {
            // Annonce toute la taille mais ne livre qu'une partie, puis
            // coupe la connexion — une vraie interruption réseau, pas
            // simulée par une exception injectée côté client.
            ctx.Response.ContentLength64 = content.Length;
            await ctx.Response.OutputStream.WriteAsync(content.AsMemory(0, 10_000));
            await ctx.Response.OutputStream.FlushAsync();
            ctx.Response.Abort();
        });

        using var httpClient = new HttpClient();
        var downloader = new HttpArtifactDownloader(httpClient);
        var result = await downloader.DownloadAsync(new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("artifact"),
            DestinationPath = finalPath,
            ExpectedSha256 = Sha256Verifier.ComputeSha256(content),
        });

        Assert.Equal(ArtifactDownloadOutcome.NetworkError, result.Outcome);
        Assert.False(File.Exists(finalPath));
        Assert.NotNull(result.ResumablePartialPath);
        Assert.True(File.Exists(result.ResumablePartialPath));
    }

    [Fact]
    public async Task DownloadAsync_returns_Timeout_for_a_server_that_never_responds_in_time()
    {
        var finalPath = Path.Combine(_tempDir, "artifact.bin");

        await using var server = new LocalHttpTestServer(async ctx =>
        {
            await Task.Delay(TimeSpan.FromSeconds(5));
            ctx.Response.ContentLength64 = 1;
            await ctx.Response.OutputStream.WriteAsync(new byte[] { 1 });
            ctx.Response.Close();
        });

        using var httpClient = new HttpClient { Timeout = TimeSpan.FromMilliseconds(300) };
        var downloader = new HttpArtifactDownloader(httpClient);
        var result = await downloader.DownloadAsync(new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("artifact"), DestinationPath = finalPath, ExpectedSha256 = new string('a', 64),
        });

        Assert.Equal(ArtifactDownloadOutcome.Timeout, result.Outcome);
        Assert.False(File.Exists(finalPath));
    }

    [Fact]
    public async Task DownloadAsync_returns_NetworkError_for_a_4xx_5xx_response()
    {
        var finalPath = Path.Combine(_tempDir, "artifact.bin");

        await using var server = new LocalHttpTestServer(ctx =>
        {
            ctx.Response.StatusCode = 404;
            ctx.Response.Close();
            return Task.CompletedTask;
        });

        using var httpClient = new HttpClient();
        var downloader = new HttpArtifactDownloader(httpClient);
        var result = await downloader.DownloadAsync(new ArtifactDownloadRequest
        {
            SourceUrl = server.UrlFor("missing"), DestinationPath = finalPath, ExpectedSha256 = new string('a', 64),
        });

        Assert.Equal(ArtifactDownloadOutcome.NetworkError, result.Outcome);
        Assert.Contains("404", result.ErrorDetail);
    }
}
