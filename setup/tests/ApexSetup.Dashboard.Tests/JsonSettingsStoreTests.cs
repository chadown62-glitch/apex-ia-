using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class JsonSettingsStoreTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "apex-dashboard-tests-" + Guid.NewGuid());

    private string FilePath => Path.Combine(_dir, "settings.json");

    [Fact]
    public async Task Returns_defaults_when_no_file_exists_yet()
    {
        var store = new JsonSettingsStore(FilePath);

        var settings = await store.LoadAsync();

        Assert.Null(settings.ProjectPathWsl);
        Assert.Equal(8000, settings.ApiPort);
    }

    [Fact]
    public async Task Round_trips_saved_settings_exactly()
    {
        var store = new JsonSettingsStore(FilePath);
        var original = new DashboardSettings
        {
            WslDistro = "Ubuntu", ProjectPathWsl = "/home/user/apex-ia-", ApiHost = "127.0.0.1",
            ApiPort = 8123, WebPanelPort = 5555, OllamaUrl = "http://127.0.0.1:11434", LaunchOnWindowsStartup = true,
        };

        await store.SaveAsync(original);
        var reloaded = await store.LoadAsync();

        Assert.Equal(original, reloaded);
    }

    [Fact]
    public async Task Never_persists_a_secret_the_settings_record_carries_none_by_design()
    {
        var store = new JsonSettingsStore(FilePath);
        await store.SaveAsync(new DashboardSettings { ProjectPathWsl = "/home/user/apex-ia-" });

        var raw = await File.ReadAllTextAsync(FilePath);

        Assert.DoesNotContain("token", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("password", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", raw, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Falls_back_to_defaults_for_a_corrupted_file_never_a_crash_at_startup()
    {
        Directory.CreateDirectory(_dir);
        await File.WriteAllTextAsync(FilePath, "{ not valid json");
        var store = new JsonSettingsStore(FilePath);

        var settings = await store.LoadAsync();

        Assert.Null(settings.ProjectPathWsl);
    }

    public void Dispose()
    {
        if (Directory.Exists(_dir))
        {
            Directory.Delete(_dir, recursive: true);
        }
    }
}
