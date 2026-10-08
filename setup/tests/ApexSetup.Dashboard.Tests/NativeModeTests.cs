using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Mode « Windows natif » (APEX autonome installé à côté du Panel, sans WSL ni Docker) : runtime constaté sur le
/// disque, préparation vérifiée par empreintes, commandes exactes du lanceur, états et consignes sans WSL ni
/// launch.sh, chemins Windows acceptés seulement là où ils ont un sens.
/// </summary>
public sealed class NativeModeTests : IDisposable
{
    private static readonly string RepoSpec = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,
        "..", "..", "..", "..", "..", "packaging", "runtime", "python-runtime.json"));

    private readonly string _root = Path.Combine(Path.GetTempPath(), "apex-native-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    /// <summary>Une installation : app\ (paquet apex, spec, liste figée) et APEX_HOME vide.</summary>
    private NativeRuntime Install(string? spec = null, string? python = null)
    {
        var app = Path.Combine(_root, "app");
        Directory.CreateDirectory(Path.Combine(app, "apex", "standalone"));
        File.WriteAllText(Path.Combine(app, "apex", "standalone", "__init__.py"), "");
        Directory.CreateDirectory(Path.Combine(app, "runtime"));
        File.WriteAllText(Path.Combine(app, "runtime", "python-runtime.json"), spec ?? File.ReadAllText(RepoSpec));
        File.WriteAllText(Path.Combine(app, "runtime", "requirements-windows.lock"), "pyyaml==6.0.3 --hash=sha256:00\n");
        return new NativeRuntime(app, Path.Combine(_root, "home"), python);
    }

    private static void Touch(string path, string content = "")
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content);
    }

    // -- description du runtime (fichier livré par l'installateur) ---------------------------------------------

    [Fact]
    public void The_shipped_runtime_spec_pins_python_by_sha256_and_names_the_default_model()
    {
        var spec = RuntimeSpec.Parse(File.ReadAllText(RepoSpec));
        Assert.Equal("3.12.10", spec.PythonVersion);
        Assert.StartsWith("https://api.nuget.org/", spec.PythonUrl);
        Assert.Equal(64, spec.PythonSha256.Length);
        Assert.Equal("requirements-windows.lock", spec.Requirements);
        Assert.Equal("qwen2.5-coder-1.5b-instruct", spec.ModelName);
        var lockFile = Path.Combine(Path.GetDirectoryName(RepoSpec)!, spec.Requirements);
        var lines = File.ReadAllLines(lockFile);
        Assert.Contains(lines, l => l.StartsWith("torch==", StringComparison.Ordinal));
        Assert.All(lines.Where(l => System.Text.RegularExpressions.Regex.IsMatch(l, "^[a-z0-9]")),
            l => Assert.Contains("==", l)); // chaque paquet est figé (et suivi de ses empreintes)
        Assert.Contains(lines, l => l.TrimStart().StartsWith("--hash=sha256:", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData("\"sha256\": \"0eb8\"")] // empreinte tronquée
    [InlineData("\"url\": \"http://insecure.example/python.nupkg\"")] // pas HTTPS
    public void A_malformed_runtime_spec_is_refused(string replacement)
    {
        var json = File.ReadAllText(RepoSpec);
        var key = replacement.Split(':')[0];
        var line = json.Split('\n').First(l => l.TrimStart().StartsWith(key, StringComparison.Ordinal));
        var broken = json.Replace(line.Trim().TrimEnd(','), replacement);
        Assert.Throws<FormatException>(() => RuntimeSpec.Parse(broken));
    }

    // -- état du runtime : constaté sur le disque ----------------------------------------------------------------

    [Fact]
    public void Runtime_state_goes_from_not_prepared_to_outdated_to_ready_and_back_to_outdated_on_update()
    {
        var runtime = Install();
        var spec = runtime.TryReadSpec(out _)!;
        Assert.Equal(RuntimeState.NotPrepared, runtime.Inspect().State);
        Assert.Contains("Mo", runtime.Inspect().Detail);

        Touch(runtime.PythonExecutable(spec));
        Assert.Equal(RuntimeState.Outdated, runtime.Inspect().State); // Python présent, préparation non terminée

        Touch(runtime.ReadyMarker(spec), $$"""
            {"requirements_sha256": "{{NativeRuntime.Sha256File(runtime.RequirementsFile(spec))}}",
             "app_root": "{{runtime.AppRoot.Replace("\\", "\\\\")}}", "prepared_at": "2026-10-08T12:00:00+00:00"}
            """);
        Assert.Equal(RuntimeState.Ready, runtime.Inspect().State);

        File.AppendAllText(runtime.RequirementsFile(spec), "torch==2.14.0 --hash=sha256:11\n"); // mise à jour d'APEX
        Assert.Equal(RuntimeState.Outdated, runtime.Inspect().State);
    }

    [Fact]
    public void An_incomplete_installation_is_broken_never_ready()
    {
        var runtime = Install();
        File.Delete(runtime.SpecFile);
        var inspection = runtime.Inspect();
        Assert.Equal(RuntimeState.Broken, inspection.State);
        Assert.Contains("réinstallez", inspection.Detail);
    }

    [Fact]
    public void The_model_counts_as_installed_only_with_weights_config_and_provenance()
    {
        var runtime = Install();
        var spec = runtime.TryReadSpec(out _)!;
        var dir = Path.Combine(runtime.ModelsDirectory, spec.ModelName);
        Touch(Path.Combine(dir, "model.safetensors"), "x");
        Assert.False(runtime.InspectModel(spec).Installed); // téléchargement interrompu : pas de config/provenance
        Touch(Path.Combine(dir, "config.json"), "{}");
        Touch(Path.Combine(dir, ".apex_source.json"), "{}");
        Assert.True(runtime.InspectModel(spec).Installed);
    }

    // -- préparation : téléchargement vérifié, extraction confinée, pip à empreintes, vérification réelle ----------

    [Fact]
    public async Task A_download_with_an_unexpected_sha256_is_deleted_and_never_used()
    {
        var payload = Encoding.UTF8.GetBytes("pas le paquet attendu");
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            ctx.Response.ContentLength64 = payload.Length;
            await ctx.Response.OutputStream.WriteAsync(payload);
            ctx.Response.Close();
        });
        var runtime = Install();
        var preparer = new RuntimePreparer(runtime, new HttpClient(), new FakeProcessRunner());
        var target = Path.Combine(_root, "python.nupkg");
        var good = Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();

        await Assert.ThrowsAsync<InvalidDataException>(() =>
            preparer.DownloadVerifiedAsync(server.UrlFor("p").ToString(), target, new string('0', 64), payload.Length, _ => { }, default));
        Assert.False(File.Exists(target));
        Assert.False(File.Exists(target + ".part"));

        await preparer.DownloadVerifiedAsync(server.UrlFor("p").ToString(), target, good, payload.Length, _ => { }, default);
        Assert.Equal(payload, await File.ReadAllBytesAsync(target));
    }

    [Fact]
    public void Extraction_keeps_only_tools_and_refuses_paths_leaving_the_destination()
    {
        var package = Path.Combine(_root, "p.nupkg");
        Directory.CreateDirectory(_root);
        using (var zip = ZipFile.Open(package, ZipArchiveMode.Create))
        {
            zip.CreateEntry("tools/python.exe").Open().Dispose();
            zip.CreateEntry("tools/Lib/os.py").Open().Dispose();
            zip.CreateEntry("python.nuspec").Open().Dispose();
        }
        var dest = Path.Combine(_root, "py");
        RuntimePreparer.ExtractTools(package, dest);
        Assert.True(File.Exists(Path.Combine(dest, "python.exe")));
        Assert.True(File.Exists(Path.Combine(dest, "Lib", "os.py")));
        Assert.False(File.Exists(Path.Combine(dest, "python.nuspec")));

        var evil = Path.Combine(_root, "evil.nupkg");
        using (var zip = ZipFile.Open(evil, ZipArchiveMode.Create))
        {
            zip.CreateEntry("tools/../../outside.txt").Open().Dispose();
        }
        Assert.Throws<InvalidDataException>(() => RuntimePreparer.ExtractTools(evil, Path.Combine(_root, "py2")));
        Assert.False(File.Exists(Path.Combine(_root, "outside.txt")));
    }

    [Fact]
    public async Task Preparation_installs_with_hashes_links_the_app_verifies_imports_then_marks_ready()
    {
        var runtime = Install();
        var spec = runtime.TryReadSpec(out _)!;
        Touch(runtime.PythonExecutable(spec)); // Python déjà présent : seule la suite est rejouée
        var runner = new FakeProcessRunner()
            .WithRunResponse("pip install", FakeProcessRunner.Ok("Successfully installed torch-2.14.0"))
            .WithRunResponse("import apex.standalone", FakeProcessRunner.Ok("apex 0.1.2 torch 2.14.0"));
        var steps = new List<string>();
        var result = await new RuntimePreparer(runtime, new HttpClient(), runner).PrepareAsync(new Collect(steps));

        Assert.True(result.Ok, result.Message);
        var pip = runner.RunInvocations[0];
        Assert.Equal(runtime.PythonExecutable(spec), pip.Executable);
        Assert.Equal(["-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-warn-script-location",
            "--require-hashes", "--only-binary=:all:", "--no-deps", "-r", runtime.RequirementsFile(spec)], pip.Arguments);
        Assert.Equal(runtime.AppRoot, File.ReadAllText(runtime.PthFile(spec)).Trim());
        Assert.Equal(RuntimeState.Ready, runtime.Inspect().State);
        Assert.Contains(steps, s => s.Contains("prêt", StringComparison.Ordinal));
        Assert.Contains("Successfully installed torch", File.ReadAllText(runtime.RuntimeLog)); // sortie de pip journalisée
    }

    [Fact]
    public async Task A_failed_pip_or_a_failed_import_never_marks_the_runtime_ready()
    {
        var runtime = Install();
        var spec = runtime.TryReadSpec(out _)!;
        Touch(runtime.PythonExecutable(spec));
        var pipFails = new FakeProcessRunner().WithRunResponse("pip install", FakeProcessRunner.Exit(1, "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES"));
        var result = await new RuntimePreparer(runtime, new HttpClient(), pipFails).PrepareAsync(null);
        Assert.False(result.Ok);
        Assert.Contains("HASHES", result.Message);
        Assert.NotEqual(RuntimeState.Ready, runtime.Inspect().State);

        var importFails = new FakeProcessRunner()
            .WithRunResponse("pip install", FakeProcessRunner.Ok(""))
            .WithRunResponse("import apex.standalone", FakeProcessRunner.Exit(1, "OSError: [WinError 126] fbgemm.dll"));
        result = await new RuntimePreparer(runtime, new HttpClient(), importFails).PrepareAsync(null);
        Assert.False(result.Ok);
        Assert.Contains("vc_redist", result.Message); // cause probable et remède nommés
        Assert.NotEqual(RuntimeState.Ready, runtime.Inspect().State);
    }

    private sealed class Collect(List<string> steps) : IProgress<string>
    {
        public void Report(string value) => steps.Add(value);
    }

    // -- lanceur : exécutable et arguments FIXES -----------------------------------------------------------------

    [Fact]
    public void The_native_launcher_runs_only_the_official_standalone_launcher_with_the_prepared_python()
    {
        var runtime = Install();
        var python = runtime.PythonExecutable(runtime.TryReadSpec(out _));
        var launcher = new BackendLauncher(new RealProcessRunner(), new FakeProcessRunner(), wsl: false, native: runtime);
        Assert.Equal(python, launcher.Command(null, "/ignored", BackendLauncher.StartCommand)!.Value.Executable);
        Assert.Equal(["-m", "apex.standalone", "serve", "--log"], launcher.Command(null, "; rm -rf /", BackendLauncher.StartCommand)!.Value.Arguments);
        Assert.Equal(["-m", "apex.standalone", "stop"], launcher.Command("Ubuntu", "x", BackendLauncher.StopCommand)!.Value.Arguments);
    }

    // -- chemins : Windows accepté pour le mode natif, jamais transmis à WSL ---------------------------------------

    [Theory]
    [InlineData(@"C:\Users\josé\AppData\Local\APEX", true)]
    [InlineData("D:/APEX/data", true)]
    [InlineData(@"C:\APEX\..\Windows", false)]
    [InlineData(@"\\serveur\partage\APEX", false)]
    [InlineData(@"C:\APEX\fichier:flux", false)]
    [InlineData(@"APEX\data", false)]
    [InlineData("/home/moi/apex", false)]
    public void Windows_absolute_paths_are_validated_strictly(string path, bool expected) =>
        Assert.Equal(expected, HostPaths.IsSafeWindowsAbsolute(path));

    [Fact]
    public async Task A_windows_path_is_never_passed_to_wsl_or_to_a_linux_host()
    {
        var runner = new FakeProcessRunner();
        var read = await new WslBackendHost(runner).ReadFileAsync("Ubuntu", @"C:\Users\u\AppData\Local\APEX\.apex\api-token");
        Assert.Equal(FileReadKind.Failed, read.Kind);
        Assert.Empty(runner.RunInvocations);
        Assert.Equal(FileReadKind.Failed, (await new LocalBackendHost().ReadFileAsync(null, @"C:\x")).Kind);
    }

    [Fact]
    public void A_discovery_record_written_by_windows_apex_is_accepted()
    {
        var json = PanelData.DiscoveryJson().Replace(PanelData.ProjectRoot, @"C:\\Users\\u\\AppData\\Local\\APEX");
        var read = BackendDiscovery.Parse(json);
        Assert.Equal(DiscoveryKind.Found, read.Kind);
        Assert.Equal(@"C:\Users\u\AppData\Local\APEX/.apex/api-token", BackendDiscovery.TokenPath(read.Record!));
    }

    [Fact]
    public async Task The_native_host_reads_under_apex_home_and_checks_the_process_image()
    {
        var runtime = Install();
        Touch(Path.Combine(runtime.Home, ".apex", "runtime", "backend.json"), "{}");
        var host = new NativeBackendHost(runtime);
        Assert.Equal(FileReadKind.Ok, (await host.ReadHomeFileAsync(null, BackendDiscovery.RelativePath)).Kind);
        Assert.Equal(FileReadKind.Failed, (await host.ReadHomeFileAsync(null, "../secret")).Kind);
        var probe = await host.ProbeAsync(null);
        Assert.False(probe.Applicable); // aucune ligne WSL
        Assert.Equal(NotReadyCause.RuntimeNotPrepared, probe.Cause);

        var self = Environment.ProcessId;
        var exe = Environment.ProcessPath!;
        Assert.True(NativeBackendHost.IsProcessRunning(self, exe));
        Assert.False(NativeBackendHost.IsProcessRunning(self, Path.Combine(_root, "autre.exe"))); // pid d'un autre programme
        Assert.False(NativeBackendHost.IsProcessRunning(int.MaxValue - 7, exe));
    }

    // -- états et consignes : jamais WSL, launch.sh ni Docker en mode natif -----------------------------------------

    private static HostProbe NativeProbe(RuntimeState state, bool model = true) =>
        HostProbe.Native(new RuntimeInspection(state, state == RuntimeState.Ready ? "Python 3.12.10 prêt" : "non préparé (environ 190 Mo)",
            model, model ? "installé" : "non installé"));

    private static string NativeStatusJson(IDictionary<string, string> states) =>
        PanelData.StatusJson(states: states).Replace("\"components\":", "\"profile\":\"standalone\",\"informational\":[\"brain\",\"ollama\"],\"not_applicable\":[\"redis\"],\"components\":");

    [Fact]
    public void Without_a_prepared_runtime_the_only_action_is_prepare()
    {
        var snapshot = PanelData.Decide(NativeProbe(RuntimeState.NotPrepared), new DiscoveryRead(DiscoveryKind.Missing, null, "absent"),
            new StatusFetch(FetchOutcome.Refused, null, null, "refusé"));
        Assert.Equal(PanelVerdict.NotReady, snapshot.Verdict);
        Assert.Equal(NotReadyCause.RuntimeNotPrepared, snapshot.Cause);
        Assert.Equal("native", snapshot.Mode);
        Assert.Contains("Préparer APEX", snapshot.Guidance);
        Assert.Equal(ComponentKey.Runtime, snapshot.Components[0].Key);
        Assert.DoesNotContain(snapshot.Components, c => c.Key is ComponentKey.Wsl or ComponentKey.Redis);
    }

    [Theory]
    [InlineData(FetchOutcome.Refused)]
    [InlineData(FetchOutcome.NotApex)]
    [InlineData(FetchOutcome.HttpError)]
    public void Native_guidance_never_mentions_wsl_launch_sh_or_docker(FetchOutcome outcome)
    {
        var snapshot = PanelData.Decide(NativeProbe(RuntimeState.Ready), new DiscoveryRead(DiscoveryKind.Missing, null, "absent"),
            new StatusFetch(outcome, null, outcome == FetchOutcome.HttpError ? 500 : null, "détail"));
        foreach (var word in new[] { "WSL", "launch.sh", "Docker", "terminal" })
        {
            Assert.DoesNotContain(word, snapshot.Guidance, StringComparison.OrdinalIgnoreCase);
        }
        Assert.Contains("APEX", snapshot.Guidance);
    }

    [Fact]
    public void A_standalone_backend_is_ready_without_redis_and_with_research_components_informational()
    {
        var states = ComponentKeys.BackendServices.Where(s => s.BackendName != "redis").ToDictionary(s => s.BackendName, _ => "ready");
        states["brain"] = "offline";
        states["ollama"] = "offline";
        var snapshot = PanelData.Decide(NativeProbe(RuntimeState.Ready), PanelData.Discovery(),
            PanelData.Fetch(NativeStatusJson(states)), PanelData.GoodToken(), alive: true);
        Assert.Equal(PanelVerdict.Ready, snapshot.Verdict);
        Assert.DoesNotContain(snapshot.Components, c => c.Key == ComponentKey.Redis);
        Assert.Contains(snapshot.Components, c => c.Key == ComponentKey.Brain && c.Status == ComponentStatus.Offline);
    }

    [Fact]
    public void Without_the_model_the_panel_says_to_install_it()
    {
        var states = ComponentKeys.BackendServices.Where(s => s.BackendName != "redis").ToDictionary(s => s.BackendName, _ => "ready");
        states["models"] = "offline";
        var snapshot = PanelData.Decide(NativeProbe(RuntimeState.Ready, model: false), PanelData.Discovery(),
            PanelData.Fetch(NativeStatusJson(states)), PanelData.GoodToken(), alive: true);
        Assert.Equal(PanelVerdict.Degraded, snapshot.Verdict);
        Assert.Contains("Installer le modèle", snapshot.Guidance);
    }

    [Fact]
    public void A_server_backend_still_reports_redis_and_ignores_unknown_profile_fields()
    {
        var snapshot = PanelData.Decide(PanelData.WslRunning(), PanelData.Discovery(), PanelData.Fetch(PanelData.StatusJson()),
            PanelData.GoodToken(), alive: true);
        Assert.Equal(PanelVerdict.Ready, snapshot.Verdict);
        Assert.Contains(snapshot.Components, c => c.Key == ComponentKey.Redis);
        Assert.Equal("wsl", snapshot.Mode);
    }

    [Fact]
    public void A_native_start_that_dies_with_an_import_error_points_to_prepare()
    {
        var failure = StatusEngine.LaunchFailure(new LaunchAttempt(PanelData.Now, "x", ExitCode: 1), native: true);
        Assert.NotNull(failure);
        Assert.Contains("Préparer APEX", failure.Value.Guidance);
        Assert.DoesNotContain(".env", failure.Value.Guidance);
    }

    // -- fenêtre : préparer, puis démarrer ; fermer pendant qu'APEX tourne DEMANDE ---------------------------------

    [Fact]
    public async Task The_window_offers_prepare_first_and_asks_before_closing_while_apex_runs()
    {
        var runtime = Install();
        var host = new NativeBackendHost(runtime);
        var redactor = new Redactor();
        var service = new PanelStatusService(host, new StatusClient(new HttpClient()));
        var store = new JsonSettingsStore(Path.Combine(_root, "settings.json"));
        await store.SaveAsync(new DashboardSettings { ApiPort = 1 });
        var runner = new FakeProcessRunner();
        var control = new PanelControl(new BackendLauncher(new RealProcessRunner(), runner, wsl: false, native: runtime),
            new StatusClient(new HttpClient()), new SystemBrowserLauncher());
        var vm = new MainWindowViewModel(store, service, new InMemoryLogSink(redactor), redactor,
            problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem), null, control,
            new RuntimePreparer(runtime, new HttpClient(), runner));
        await vm.InitializeAsync();

        Assert.True(vm.IsNative);
        Assert.False(vm.IsWslSettingsVisible);
        Assert.True(vm.CanPrepare);
        Assert.False(vm.CanStart); // rien ne peut démarrer sans runtime
        Assert.False(vm.CanInstallModel);
        Assert.False(vm.InterceptClose()); // APEX ne tourne pas : la fenêtre se ferme normalement
    }
}
