using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;
using static ApexSetup.Dashboard.Tests.Fixtures.PanelData;

namespace ApexSetup.Dashboard.Tests;

public class LogsAndDiagnosticTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "apex-logs-" + Guid.NewGuid());

    [Fact]
    public void The_log_file_masks_registered_secrets_and_bearer_headers()
    {
        var redactor = new Redactor();
        redactor.Register(Token);
        var log = new FileLogSink(_dir, redactor);

        log.Log($"lecture du jeton {Token}");
        log.Log("en-tête Authorization: Bearer abc.def-ghi");

        var text = File.ReadAllText(log.CurrentFile);
        Assert.DoesNotContain(Token, text);
        Assert.DoesNotContain("abc.def-ghi", text);
        Assert.Contains("[secret masqué]", text);
        Assert.DoesNotContain(Token, string.Join("\n", log.Entries.Select(e => e.Message)));
    }

    [Fact]
    public void An_unwritable_log_directory_never_crashes_and_is_reported()
    {
        Directory.CreateDirectory(_dir);
        var blocker = Path.Combine(_dir, "fichier");
        File.WriteAllText(blocker, "ceci est un fichier, pas un dossier");

        var log = new FileLogSink(Path.Combine(blocker, "logs"), new Redactor());
        log.Log("le Panel continue");

        Assert.NotNull(log.FileProblem);
        Assert.Single(log.Entries);
    }

    [Fact]
    public void Old_log_files_are_pruned()
    {
        Directory.CreateDirectory(_dir);
        for (var day = 1; day <= FileLogSink.MaxFiles + 5; day++)
        {
            File.WriteAllText(Path.Combine(_dir, $"panel-2020{day:00}01.log"), "x");
        }

        _ = new FileLogSink(_dir, new Redactor());

        Assert.Equal(FileLogSink.MaxFiles, Directory.GetFiles(_dir, "panel-*.log").Length);
    }

    [Fact]
    public void The_diagnostic_lists_every_required_line_and_no_secret()
    {
        var redactor = new Redactor();
        redactor.Register(Token);
        var snapshot = Decide(WslRunning(), Discovery(), Fetch(StatusJson()), GoodToken(), alive: true);
        var context = new DiagnosticContext("Microsoft Windows 10.0.22631", ".NET 8.0", @"C:\Users\u\AppData\Local\Programs\APEX\",
            @"C:\Users\u\AppData\Roaming\APEX\dashboard-settings.json", @"C:\Users\u\AppData\Local\APEX\logs", null, null);

        var text = DiagnosticReport.Build(snapshot, context, redactor);

        foreach (var expected in new[]
                 {
                     "Microsoft Windows", "Version du Panel", "Version APEX backend", "0.1.2", "WSL", "API", "Ollama", "Brain",
                     "Sandbox", "Database", "Redis", "GPU", "APEX READY", "Journaux",
                 })
        {
            Assert.Contains(expected, text);
        }
        Assert.DoesNotContain(Token, text);
    }

    public void Dispose()
    {
        if (Directory.Exists(_dir))
        {
            Directory.Delete(_dir, recursive: true);
        }
    }
}

public class JsonSettingsStoreTests : IDisposable
{
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "apex-dashboard-tests-" + Guid.NewGuid());

    private string FilePath => Path.Combine(_dir, "settings.json");

    [Fact]
    public async Task Defaults_need_no_configuration_at_all()
    {
        var loaded = await new JsonSettingsStore(FilePath).LoadAsync();

        Assert.Null(loaded.Problem);
        Assert.Null(loaded.Settings.WslDistro);
        Assert.Null(loaded.Settings.ApiPort);
    }

    [Fact]
    public async Task Round_trips_and_never_contains_a_secret()
    {
        var store = new JsonSettingsStore(FilePath);
        var original = new DashboardSettings { WslDistro = "Ubuntu-24.04", ApiPort = 8123 };

        await store.SaveAsync(original);

        Assert.Equal(original, (await store.LoadAsync()).Settings);
        var raw = await File.ReadAllTextAsync(FilePath);
        Assert.DoesNotContain("token", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", raw, StringComparison.OrdinalIgnoreCase);
    }

    [Theory]
    [InlineData("{ not valid json")]
    [InlineData("{\"ApiPort\": \"huit mille\"}")]
    [InlineData("{\"ApiPort\": 99999}")]
    public async Task A_corrupted_file_gives_defaults_a_reported_problem_and_is_left_untouched(string content)
    {
        Directory.CreateDirectory(_dir);
        await File.WriteAllTextAsync(FilePath, content);

        var loaded = await new JsonSettingsStore(FilePath).LoadAsync();

        Assert.NotNull(loaded.Problem);
        Assert.Equal(new DashboardSettings(), loaded.Settings);
        Assert.Equal(content, await File.ReadAllTextAsync(FilePath));
    }

    [Fact]
    public async Task Settings_of_the_previous_Panel_version_are_read_without_error()
    {
        Directory.CreateDirectory(_dir);
        await File.WriteAllTextAsync(FilePath,
            "{\"WslDistro\":\"Ubuntu\",\"ProjectPathWsl\":\"/home/x/apex\",\"ApiHost\":\"127.0.0.1\",\"ApiPort\":8000,\"WebPanelPort\":5173,\"OllamaUrl\":\"http://127.0.0.1:11434\",\"LaunchOnWindowsStartup\":false}");

        var loaded = await new JsonSettingsStore(FilePath).LoadAsync();

        Assert.Null(loaded.Problem);
        Assert.Equal("Ubuntu", loaded.Settings.WslDistro);
    }

    public void Dispose()
    {
        if (Directory.Exists(_dir))
        {
            Directory.Delete(_dir, recursive: true);
        }
    }
}

public class SingleInstanceGuardTests
{
    [Fact]
    public void A_second_instance_is_refused_until_the_first_is_released()
    {
        var suffix = "-" + Guid.NewGuid().ToString("N");
        var first = SingleInstanceGuard.TryAcquire(suffix);
        Assert.NotNull(first);

        // Un mutex nommé est réentrant pour son thread propriétaire : la seconde tentative vient d'un autre thread.
        SingleInstanceGuard? second = null;
        var thread = new Thread(() => second = SingleInstanceGuard.TryAcquire(suffix));
        thread.Start();
        thread.Join();
        Assert.Null(second);

        first!.Dispose();
        SingleInstanceGuard? third = null;
        thread = new Thread(() => { third = SingleInstanceGuard.TryAcquire(suffix); third?.Dispose(); });
        thread.Start();
        thread.Join();
        Assert.NotNull(third);
    }
}

public class ActivationReplayTests
{
    [Fact]
    public void An_activation_requested_before_the_window_exists_is_replayed_when_it_registers()
    {
        // Un second double-clic pendant que la première fenêtre se crée : la demande n'est pas perdue (sinon, sous
        // Windows, le second lancement attendrait un accusé qui ne viendrait jamais et croirait la fenêtre bloquée).
        var guard = SingleInstanceGuard.TryAcquire("-" + Guid.NewGuid().ToString("N"));
        Assert.NotNull(guard);
        try
        {
            guard!.Signal();
            var calls = 0;
            guard.OnActivationRequested(() => calls++);
            Assert.Equal(1, calls); // rejouée à l'enregistrement
            guard.Signal();
            Assert.Equal(2, calls);
        }
        finally
        {
            guard!.Dispose();
        }
    }
}

/// <summary>Sortie de <c>--diagnostic</c> / <c>--status-json</c> redirigée vers un fichier ou un tube.</summary>
public class HeadlessOutputTests
{
    private const string Text = "Mesuré le : 30/09 — Paramètres « é à ç »";

    [Fact]
    public void Redirected_output_is_UTF8_without_BOM_whatever_the_console_code_page()
    {
        var stream = new MemoryStream();
        var console = new StringWriter();

        Program.Emit(Text, redirected: true, () => stream, console);

        var bytes = stream.ToArray();
        Assert.Equal(new System.Text.UTF8Encoding(false).GetBytes(Text + Environment.NewLine), bytes);
        Assert.False(bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF);
        Assert.Equal("", console.ToString()); // jamais via la console (page de codes OEM sous Windows)
    }

    [Fact]
    public void Interactive_console_output_goes_through_the_console_writer()
    {
        var console = new StringWriter();

        Program.Emit(Text, redirected: false, () => throw new InvalidOperationException("non utilisé"), console);

        Assert.Equal(Text + Environment.NewLine, console.ToString());
    }
}
