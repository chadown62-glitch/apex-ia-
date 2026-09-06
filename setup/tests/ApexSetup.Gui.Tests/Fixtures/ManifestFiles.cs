using System.Security.Cryptography;
using System.Text.Json;

namespace ApexSetup.Gui.Tests.Fixtures;

internal static class ManifestFiles
{
    public static string WriteDownloadableManifest(string directory, string baseUrl, byte[] fastContent, byte[] embedContent)
    {
        var entries = new object[]
        {
            new
            {
                model_id = "qwen2-5-coder-7b-instruct", version = "1", quantization = "q4_k_m", served_model = "x",
                size_bytes = fastContent.Length, sha256 = Sha256Hex(fastContent), required_vram_gb = 5.0, required_ram_gb = 8.0,
                max_context = 32768, capability_class = "coder", supported_profiles = new[] { "MINIMAL" },
                source = "test", source_url = baseUrl + "fast-model",
            },
            new
            {
                model_id = "bge-m3-latest", version = "1", quantization = "q8_0", served_model = "x",
                size_bytes = embedContent.Length, sha256 = Sha256Hex(embedContent), required_vram_gb = 1.0, required_ram_gb = 2.0,
                max_context = 8192, capability_class = "embedding", supported_profiles = new[] { "MINIMAL" },
                source = "test", source_url = baseUrl + "embed-model",
            },
        };
        var path = Path.Combine(directory, "manifest.json");
        File.WriteAllText(path, JsonSerializer.Serialize(entries));
        return path;
    }

    private static string Sha256Hex(byte[] content) => Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant();
    public static string WriteMinimalProfileManifest(string directory)
    {
        var entries = new object[]
        {
            new
            {
                model_id = "qwen2-5-coder-7b-instruct", version = "1", quantization = "q4_k_m", served_model = "x",
                size_bytes = 12, sha256 = new string('a', 64), required_vram_gb = 5.0, required_ram_gb = 8.0,
                max_context = 32768, capability_class = "coder", supported_profiles = new[] { "MINIMAL" },
                source = "test", source_url = "https://models.invalid/fast",
            },
            new
            {
                model_id = "bge-m3-latest", version = "1", quantization = "q8_0", served_model = "x",
                size_bytes = 6, sha256 = new string('b', 64), required_vram_gb = 1.0, required_ram_gb = 2.0,
                max_context = 8192, capability_class = "embedding", supported_profiles = new[] { "MINIMAL" },
                source = "test", source_url = "https://models.invalid/embed",
            },
        };
        var path = Path.Combine(directory, "manifest.json");
        File.WriteAllText(path, JsonSerializer.Serialize(entries));
        return path;
    }
}
