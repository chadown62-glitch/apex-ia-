using ApexSetup.Launcher.Notify;

namespace ApexSetup.Launcher.Tests.Fakes;

public sealed class FakeUserNotifier : IUserNotifier
{
    public UserAction ActionToReturn { get; init; } = UserAction.Close;
    public List<(string Title, string Message, string Detail, bool OfferRepair)> Errors { get; } = [];
    public List<(string Title, string Message)> Infos { get; } = [];

    public UserAction ShowError(string title, string message, string technicalDetail, bool offerRepair)
    {
        Errors.Add((title, message, technicalDetail, offerRepair));
        return ActionToReturn;
    }

    public void ShowInfo(string title, string message) => Infos.Add((title, message));
}
