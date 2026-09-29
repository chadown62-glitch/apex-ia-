using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;
using static ApexSetup.Dashboard.Tests.Fixtures.PanelData;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Table de vérité du Panel : chaque situation de la mission produit l'état et la consigne attendus, et jamais READY sans preuve.</summary>
public class StatusEngineTests
{
    private static ComponentStatus Of(PanelSnapshot s, ComponentKey key) => s.Get(key)!.Status;

    [Fact]
    public void Everything_answering_is_APEX_READY()
    {
        var s = Decide(WslRunning(), Discovery(), Fetch(StatusJson()), GoodToken(), alive: true);

        Assert.Equal(PanelVerdict.Ready, s.Verdict);
        Assert.Equal("APEX READY", s.Headline);
        Assert.All(s.Components, c => Assert.Equal(ComponentStatus.Ready, c.Status));
        Assert.Equal(
            [ComponentKey.Wsl, ComponentKey.Backend, ComponentKey.Api, ComponentKey.Database, ComponentKey.Redis, ComponentKey.Ollama, ComponentKey.Sandbox, ComponentKey.Brain],
            s.Components.Select(c => c.Key));
        Assert.Equal("0.1.2", s.Versions.BackendApexVersion);
    }

    [Fact]
    public void Launch_sh_not_run_is_NOT_READY_with_the_exact_instruction()
    {
        var s = Decide(WslRunning(), BackendDiscovery.FromFile(FileRead.NotFound("absent")), new StatusFetch(FetchOutcome.Refused, null, null, "refusé"));

        Assert.Equal(PanelVerdict.NotReady, s.Verdict);
        Assert.Equal(NotReadyCause.BackendNotStarted, s.Cause);
        Assert.Equal(ComponentStatus.Ready, Of(s, ComponentKey.Wsl));
        Assert.Equal(ComponentStatus.Offline, Of(s, ComponentKey.Api));
        Assert.Equal(ComponentStatus.Offline, Of(s, ComponentKey.Backend));
        Assert.Equal(ComponentStatus.Unknown, Of(s, ComponentKey.Ollama)); // non mesurable d'ici : jamais deviné
        Assert.Contains("n'est pas démarré", s.Guidance);
        Assert.Contains("launch.sh", s.Guidance);
        Assert.Contains(StatusEngine.LaunchCommand, s.Guidance);
    }

    [Fact]
    public void WSL_stopped_reads_nothing_and_says_so()
    {
        var s = Decide(WslStopped(), DiscoveryRead.NotRead, StatusFetch.NotAttempted("hôte arrêté"));

        Assert.Equal(NotReadyCause.WslStopped, s.Cause);
        Assert.Equal(ComponentStatus.Offline, Of(s, ComponentKey.Wsl));
        Assert.Equal(ComponentStatus.Unknown, Of(s, ComponentKey.Api));
        Assert.Contains("ne démarre jamais WSL", s.Guidance);
    }

    [Fact]
    public void WSL_not_installed_is_its_own_cause()
    {
        var s = Decide(WslMissing(), DiscoveryRead.NotRead, StatusFetch.NotAttempted("hôte absent"));

        Assert.Equal(NotReadyCause.WslNotInstalled, s.Cause);
        Assert.Equal(ComponentStatus.Error, Of(s, ComponentKey.Wsl));
    }

    [Fact]
    public void Recently_registered_backend_that_does_not_listen_yet_is_STARTING()
    {
        var s = Decide(WslRunning(), Discovery(startedAt: Now.AddSeconds(-10)), new StatusFetch(FetchOutcome.Refused, null, null, "refusé"), GoodToken(), alive: true);

        Assert.Equal(NotReadyCause.BackendStarting, s.Cause);
        Assert.Equal(ComponentStatus.Starting, Of(s, ComponentKey.Backend));
        Assert.NotEqual(PanelVerdict.Ready, s.Verdict);
    }

    [Fact]
    public void Stale_registration_of_a_dead_process_is_OFFLINE_never_STARTING_nor_READY()
    {
        var s = Decide(WslRunning(), Discovery(startedAt: Now.AddSeconds(-10)), new StatusFetch(FetchOutcome.Refused, null, null, "refusé"), GoodToken(), alive: false);

        Assert.Equal(NotReadyCause.BackendStopped, s.Cause);
        Assert.Equal(ComponentStatus.Offline, Of(s, ComponentKey.Backend));
        Assert.Contains("Relancez", s.Guidance);
    }

    [Fact]
    public void Alive_but_silent_long_after_start_is_an_ERROR()
    {
        var s = Decide(WslRunning(), Discovery(startedAt: Now.AddMinutes(-10)), new StatusFetch(FetchOutcome.Refused, null, null, "refusé"), GoodToken(), alive: true);

        Assert.Equal(NotReadyCause.BackendUnresponsive, s.Cause);
        Assert.Equal(ComponentStatus.Error, Of(s, ComponentKey.Backend));
    }

    [Fact]
    public void Timeout_is_an_API_error_not_a_ready_state()
    {
        var s = Decide(WslRunning(), Discovery(), new StatusFetch(FetchOutcome.Timeout, null, null, "aucune réponse sous 10s"), GoodToken(), alive: true);

        Assert.Equal(NotReadyCause.ApiTimeout, s.Cause);
        Assert.Equal(ComponentStatus.Error, Of(s, ComponentKey.Api));
    }

    [Fact]
    public void A_rejected_token_is_TokenRejected()
    {
        var s = Decide(WslRunning(), Discovery(), new StatusFetch(FetchOutcome.Unauthorized, null, 401, "jeton refusé"), GoodToken(), alive: true);

        Assert.Equal(NotReadyCause.TokenRejected, s.Cause);
        Assert.Equal(ComponentStatus.Error, Of(s, ComponentKey.Api));
    }

    [Fact]
    public void A_missing_token_is_TokenMissing()
    {
        var missing = TokenRead.FromFile(FileRead.NotFound("absent"));
        var s = Decide(WslRunning(), Discovery(), new StatusFetch(FetchOutcome.Unauthorized, null, 401, "aucun jeton"), missing, alive: true);

        Assert.Equal(NotReadyCause.TokenMissing, s.Cause);
    }

    [Fact]
    public void Another_program_on_the_port_is_a_port_conflict()
    {
        var s = Decide(WslRunning(), BackendDiscovery.FromFile(FileRead.NotFound("absent")), new StatusFetch(FetchOutcome.NotApex, null, 200, "autre programme"));

        Assert.Equal(NotReadyCause.PortConflict, s.Cause);
        Assert.Contains("--port", s.Guidance);
    }

    [Fact]
    public void Partially_available_services_is_DEGRADED_and_names_them()
    {
        var states = ComponentKeys.BackendServices.ToDictionary(s => s.BackendName, _ => "ready");
        states["ollama"] = "degraded";
        states["redis"] = "offline";
        var s = Decide(WslRunning(), Discovery(), Fetch(StatusJson(states: states)), GoodToken(), alive: true);

        Assert.Equal(PanelVerdict.Degraded, s.Verdict);
        Assert.Equal(ComponentStatus.Degraded, Of(s, ComponentKey.Ollama));
        Assert.Equal(ComponentStatus.Offline, Of(s, ComponentKey.Redis));
        Assert.Contains("Ollama", s.Guidance);
        Assert.Contains("Redis", s.Guidance);
    }

    [Fact]
    public void A_service_the_backend_did_not_report_is_UNKNOWN_and_blocks_READY()
    {
        var states = ComponentKeys.BackendServices.ToDictionary(s => s.BackendName, _ => "ready");
        states.Remove("brain");
        var s = Decide(WslRunning(), Discovery(), Fetch(StatusJson(states: states)), GoodToken(), alive: true);

        Assert.Equal(ComponentStatus.Unknown, Of(s, ComponentKey.Brain));
        Assert.Equal(PanelVerdict.Degraded, s.Verdict);
    }

    [Theory]
    [InlineData("starting", ComponentStatus.Starting)]
    [InlineData("READY", ComponentStatus.Unknown)] // la casse compte : rien n'est interprété « à peu près »
    [InlineData("ok", ComponentStatus.Unknown)]
    [InlineData("", ComponentStatus.Unknown)]
    public void Unrecognised_states_are_UNKNOWN(string state, ComponentStatus expected) =>
        Assert.Equal(expected, StatusEngine.MapState(state));

    [Fact]
    public void Newer_backend_protocol_means_the_Panel_is_too_old()
    {
        var s = Decide(WslRunning(), Discovery(), Fetch(StatusJson(protocol: 2)), GoodToken(), alive: true);

        Assert.Equal(PanelVerdict.VersionMismatch, s.Verdict);
        Assert.Equal(NotReadyCause.PanelTooOld, s.Cause);
        Assert.Equal("VERSION_MISMATCH", s.Headline);
        Assert.All(ComponentKeys.BackendServices, svc => Assert.Equal(ComponentStatus.Unknown, Of(s, svc.Key)));
    }

    [Fact]
    public void Older_backend_protocol_or_missing_route_means_the_backend_is_too_old()
    {
        var older = Decide(WslRunning(), Discovery(), Fetch(StatusJson(protocol: 0)), GoodToken(), alive: true);
        var noRoute = Decide(WslRunning(), BackendDiscovery.FromFile(FileRead.NotFound("absent")), new StatusFetch(FetchOutcome.OldBackend, null, 404, "ancien"));

        Assert.Equal((PanelVerdict.VersionMismatch, NotReadyCause.BackendTooOld), (older.Verdict, older.Cause));
        Assert.Equal((PanelVerdict.VersionMismatch, NotReadyCause.BackendTooOld), (noRoute.Verdict, noRoute.Cause));
    }

    [Fact]
    public void Linux_host_has_no_WSL_row_and_can_still_be_READY()
    {
        var s = Decide(HostProbe.LocalHost(), Discovery(), Fetch(StatusJson()), GoodToken(), alive: true);

        Assert.Null(s.Get(ComponentKey.Wsl));
        Assert.False(s.WslApplicable);
        Assert.Equal(PanelVerdict.Ready, s.Verdict);
    }

    [Fact]
    public void READY_is_impossible_without_an_OK_response_whatever_else_is_true()
    {
        var outcomes = Enum.GetValues<FetchOutcome>().Where(o => o != FetchOutcome.Ok);
        foreach (var outcome in outcomes)
        {
            foreach (var alive in new bool?[] { true, false, null })
            {
                var s = Decide(WslRunning(), Discovery(startedAt: Now.AddSeconds(-5)), new StatusFetch(outcome, null, null, "x"), GoodToken(), alive);
                Assert.NotEqual(PanelVerdict.Ready, s.Verdict);
                Assert.DoesNotContain(s.Components, c => c.Key == ComponentKey.Api && c.Status == ComponentStatus.Ready);
            }
        }
    }

    [Fact]
    public void An_invalid_discovery_record_is_reported()
    {
        var s = Decide(WslRunning(), BackendDiscovery.Parse("{ pas du json"), new StatusFetch(FetchOutcome.Refused, null, null, "refusé"));

        Assert.Equal(NotReadyCause.DiscoveryInvalid, s.Cause);
        Assert.Equal(ComponentStatus.Error, Of(s, ComponentKey.Backend));
    }

    [Fact]
    public void Launch_sh_preflight_is_STARTING_however_long_it_takes_while_its_process_lives()
    {
        var s = Decide(WslRunning(), Discovery(startedAt: Now.AddMinutes(-9), phase: "starting"),
            new StatusFetch(FetchOutcome.Refused, null, null, "refusé"), TokenRead.NotRead, alive: true);

        Assert.Equal(NotReadyCause.BackendStarting, s.Cause);
        Assert.Equal(ComponentStatus.Starting, Of(s, ComponentKey.Backend));
        Assert.Contains("Docker, doctor, gate", s.Get(ComponentKey.Backend)!.Detail);
    }

    [Fact]
    public void A_dead_launch_sh_announcement_is_OFFLINE_with_its_own_message()
    {
        var s = Decide(WslRunning(), Discovery(startedAt: Now.AddSeconds(-5), phase: "starting"),
            new StatusFetch(FetchOutcome.Refused, null, null, "refusé"), TokenRead.NotRead, alive: false);

        Assert.Equal(NotReadyCause.BackendStopped, s.Cause);
        Assert.Contains("avant de démarrer l'API", s.Get(ComponentKey.Backend)!.Detail);
    }

    [Fact]
    public void A_record_without_phase_from_an_older_backend_is_read_as_serving()
    {
        var read = Discovery(phase: null);

        Assert.Equal(DiscoveryKind.Found, read.Kind);
        Assert.False(read.Record!.IsStarting);
        Assert.Equal(DiscoveryKind.Invalid, Discovery(phase: "autre").Kind);
    }

    [Fact]
    public void Guidance_names_WSL_only_where_there_is_WSL()
    {
        var missing = BackendDiscovery.FromFile(FileRead.NotFound("absent"));
        var refused = new StatusFetch(FetchOutcome.Refused, null, null, "refusé");

        Assert.Contains("dans WSL", Decide(WslRunning(), missing, refused).Guidance);
        Assert.DoesNotContain("WSL", Decide(HostProbe.LocalHost(), missing, refused).Guidance);
    }
}
