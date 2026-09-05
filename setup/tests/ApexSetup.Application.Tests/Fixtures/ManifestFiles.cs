using System.Security.Cryptography;
using System.Text.Json;

namespace ApexSetup.Application.Tests.Fixtures;

/// <summary>Écrit un fichier manifeste JSON minimal (avec <c>source_url</c>, voir <c>ApplicationManifestLoader</c>) pour les tests — jamais un manifeste inventé sans rapport avec <c>Core.Json.ModelManifestParser</c>.</summary>
internal static class ManifestFiles
{
    public static string WriteMinimalProfileManifest(string directory, string? sourceUrlOverride = null, string? modelIdOverride = null)
    {
        var fastId = modelIdOverride ?? "qwen2-5-coder-7b-instruct";
        var entries = new object[]
        {
            new
            {
                model_id = fastId, version = "1", quantization = "q4_k_m", served_model = fastId,
                size_bytes = 12, sha256 = new string('a', 64), required_vram_gb = 5.0, required_ram_gb = 8.0,
                max_context = 32768, capability_class = "coder", supported_profiles = new[] { "MINIMAL" },
                source = "test-registry", source_url = sourceUrlOverride ?? "https://models.invalid/fast",
            },
            new
            {
                model_id = "bge-m3-latest", version = "1", quantization = "q8_0", served_model = "bge-m3-latest",
                size_bytes = 6, sha256 = new string('b', 64), required_vram_gb = 1.0, required_ram_gb = 2.0,
                max_context = 8192, capability_class = "embedding", supported_profiles = new[] { "MINIMAL" },
                source = "test-registry", source_url = "https://models.invalid/embed",
            },
        };

        var path = Path.Combine(directory, "manifest.json");
        File.WriteAllText(path, JsonSerializer.Serialize(entries));
        return path;
    }

    /// <summary>Manifeste dont les artefacts pointent vers de VRAIS petits contenus servis par <see cref="LocalHttpTestServer"/>, hash SHA-256 calculé pour de vrai — utilisé par les tests <c>install</c> bout en bout. <paramref name="fastDeclaredSizeOverride"/> permet de déclarer une taille MENSONGÈRE (jamais vérifiée avant l'étape Validation) pour reproduire un échec tardif du pipeline, déclenchant un vrai rollback multi-étapes.</summary>
    public static string WriteDownloadableManifest(
        string directory, string baseUrl, byte[] fastContent, byte[] embedContent, long? fastDeclaredSizeOverride = null)
    {
        var entries = new object[]
        {
            new
            {
                model_id = "qwen2-5-coder-7b-instruct", version = "1", quantization = "q4_k_m", served_model = "qwen2-5-coder-7b-instruct",
                size_bytes = fastDeclaredSizeOverride ?? fastContent.Length, sha256 = Sha256Hex(fastContent), required_vram_gb = 5.0, required_ram_gb = 8.0,
                max_context = 32768, capability_class = "coder", supported_profiles = new[] { "MINIMAL" },
                source = "test-registry", source_url = baseUrl + "fast-model",
            },
            new
            {
                model_id = "bge-m3-latest", version = "1", quantization = "q8_0", served_model = "bge-m3-latest",
                size_bytes = embedContent.Length, sha256 = Sha256Hex(embedContent), required_vram_gb = 1.0, required_ram_gb = 2.0,
                max_context = 8192, capability_class = "embedding", supported_profiles = new[] { "MINIMAL" },
                source = "test-registry", source_url = baseUrl + "embed-model",
            },
        };

        var path = Path.Combine(directory, "manifest.json");
        File.WriteAllText(path, JsonSerializer.Serialize(entries));
        return path;
    }

    private static string Sha256Hex(byte[] content) => Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant();
}
