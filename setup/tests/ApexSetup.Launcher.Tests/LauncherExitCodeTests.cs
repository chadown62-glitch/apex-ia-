using ApexSetup.Application;
using Xunit;

namespace ApexSetup.Launcher.Tests;

/// <summary>
/// Garde-fou de non-régression : les valeurs de <see cref="LauncherExitCode"/>
/// documentées comme partageant une signification avec
/// <see cref="SetupExitCode"/> doivent rester numériquement identiques —
/// une dérive silencieuse de l'un sans l'autre casserait un script
/// d'orchestration qui déciderait sur le seul entier.
/// </summary>
public class LauncherExitCodeTests
{
    [Theory]
    [InlineData(LauncherExitCode.RuntimeError, SetupExitCode.RuntimeUnavailable)]
    [InlineData(LauncherExitCode.Blocked, SetupExitCode.GateBlocking)]
    [InlineData(LauncherExitCode.RecoveryRequired, SetupExitCode.RecoveryRequired)]
    [InlineData(LauncherExitCode.InvalidInstall, SetupExitCode.InconsistentState)]
    [InlineData(LauncherExitCode.InternalError, SetupExitCode.InternalError)]
    public void Shared_concepts_keep_the_same_numeric_value_as_SetupExitCode(
        LauncherExitCode launcherCode, SetupExitCode setupCode)
    {
        Assert.Equal((int)setupCode, (int)launcherCode);
    }

    [Theory]
    [InlineData(LauncherExitCode.NeedsSetup)]
    [InlineData(LauncherExitCode.ElevationDeclined)]
    [InlineData(LauncherExitCode.NeedsUpdate)]
    [InlineData(LauncherExitCode.UnknownError)]
    public void Launcher_only_concepts_never_collide_with_any_SetupExitCode_value(LauncherExitCode launcherCode)
    {
        foreach (SetupExitCode setupCode in Enum.GetValues<SetupExitCode>())
        {
            Assert.NotEqual((int)setupCode, (int)launcherCode);
        }
    }

    [Fact]
    public void Success_is_zero_like_every_deterministic_exit_code_contract_in_this_repo()
    {
        Assert.Equal(0, (int)LauncherExitCode.Success);
    }
}
