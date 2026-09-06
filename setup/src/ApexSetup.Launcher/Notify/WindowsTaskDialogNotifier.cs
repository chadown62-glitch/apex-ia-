using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace ApexSetup.Launcher.Notify;

/// <summary>
/// Implémentation réelle via <c>TaskDialogIndirect</c> (comctl32.dll,
/// natif Windows Vista+) — choisi plutôt qu'une nouvelle bibliothèque UI
/// (§F : « intégrer l'UI existante plutôt qu'en construire une seconde »)
/// car un <c>MessageBox</c> classique ne permet pas de boutons à texte
/// personnalisé (« Réparer »/« Détails »/« Fermer » — la spécification §F
/// l'exige explicitement) : <c>TaskDialog</c> est le seul mécanisme natif
/// Windows qui le permette sans dépendance externe.
///
/// Limite assumée de cet environnement (même précédent que
/// <c>setup/docs/GUI_ARCHITECTURE.md</c> pour Avalonia) : ce fichier ne
/// peut pas être exécuté ni vérifié visuellement dans ce bac à sable
/// Linux — seule la compilation (marshaling correct des structures P/Invoke)
/// est vérifiable ici.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsTaskDialogNotifier : IUserNotifier
{
    private const int RepairButtonId = 100;
    private const int DetailsButtonId = 101;
    private const int CloseButtonId = 102;

    private const uint TdfAllowDialogCancellation = 0x0010;
    private const uint TdfExpandFooterArea = 0x0002;
    private const uint TdfUseCommandLinks = 0x0800;

    // MAKEINTRESOURCE(-3) = TD_ERROR_ICON (voir commctl.h) — évite de
    // charger un GDI HICON réel pour une simple icône stock Windows.
    private static readonly IntPtr ErrorIcon = new(unchecked((short)-3));

    public UserAction ShowError(string title, string message, string technicalDetail, bool offerRepair)
    {
        var buttons = new List<(int Id, string Text)> { (CloseButtonId, "Fermer") };
        if (offerRepair)
        {
            buttons.Insert(0, (RepairButtonId, "Réparer"));
        }
        buttons.Insert(buttons.Count - 1, (DetailsButtonId, "Détails"));

        var buttonPtrs = new List<IntPtr>();
        var nativeButtons = IntPtr.Zero;
        var titlePtr = IntPtr.Zero;
        var mainPtr = IntPtr.Zero;
        var contentPtr = IntPtr.Zero;
        var expandedPtr = IntPtr.Zero;
        try
        {
            var buttonStructSize = Marshal.SizeOf<TaskDialogButton>();
            nativeButtons = Marshal.AllocHGlobal(buttonStructSize * buttons.Count);
            for (var i = 0; i < buttons.Count; i++)
            {
                var textPtr = Marshal.StringToHGlobalUni(buttons[i].Text);
                buttonPtrs.Add(textPtr);
                var button = new TaskDialogButton { ButtonId = buttons[i].Id, ButtonText = textPtr };
                Marshal.StructureToPtr(button, IntPtr.Add(nativeButtons, i * buttonStructSize), false);
            }

            titlePtr = Marshal.StringToHGlobalUni(title);
            mainPtr = Marshal.StringToHGlobalUni(title);
            contentPtr = Marshal.StringToHGlobalUni(message);
            expandedPtr = Marshal.StringToHGlobalUni(technicalDetail);

            var config = new TaskDialogConfig
            {
                Size = (uint)Marshal.SizeOf<TaskDialogConfig>(),
                ParentWindow = IntPtr.Zero,
                Instance = IntPtr.Zero,
                Flags = TdfAllowDialogCancellation | TdfExpandFooterArea | TdfUseCommandLinks,
                CommonButtons = 0,
                WindowTitle = titlePtr,
                MainIcon = ErrorIcon,
                MainInstruction = mainPtr,
                Content = contentPtr,
                ButtonCount = (uint)buttons.Count,
                Buttons = nativeButtons,
                DefaultButton = buttons[^1].Id,
                RadioButtonCount = 0,
                RadioButtons = IntPtr.Zero,
                DefaultRadioButton = 0,
                VerificationText = IntPtr.Zero,
                ExpandedInformation = expandedPtr,
                ExpandedControlText = IntPtr.Zero,
                CollapsedControlText = IntPtr.Zero,
                FooterIcon = IntPtr.Zero,
                Footer = IntPtr.Zero,
                Callback = IntPtr.Zero,
                CallbackData = IntPtr.Zero,
                Width = 0,
            };

            var hresult = TaskDialogIndirect(ref config, out var pressedButton, out _, out _);
            if (hresult != 0)
            {
                // Échec d'affichage (ex. comctl32 v5 non activé par
                // manifeste) — jamais un crash du launcher pour un simple
                // problème d'UI : on dégrade vers "Fermer" plutôt que de
                // planter, l'appelant (Program) garde toujours un code de
                // sortie déterministe indépendamment de l'affichage.
                return UserAction.Close;
            }

            return pressedButton switch
            {
                RepairButtonId => UserAction.Repair,
                DetailsButtonId => UserAction.Details,
                _ => UserAction.Close,
            };
        }
        finally
        {
            foreach (var ptr in buttonPtrs) Marshal.FreeHGlobal(ptr);
            if (nativeButtons != IntPtr.Zero) Marshal.FreeHGlobal(nativeButtons);
            if (titlePtr != IntPtr.Zero) Marshal.FreeHGlobal(titlePtr);
            if (mainPtr != IntPtr.Zero) Marshal.FreeHGlobal(mainPtr);
            if (contentPtr != IntPtr.Zero) Marshal.FreeHGlobal(contentPtr);
            if (expandedPtr != IntPtr.Zero) Marshal.FreeHGlobal(expandedPtr);
        }
    }

    public void ShowInfo(string title, string message) => MessageBoxW(IntPtr.Zero, message, title, 0x00000040 /* MB_ICONINFORMATION */);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [DllImport("comctl32.dll", CharSet = CharSet.Unicode)]
    private static extern int TaskDialogIndirect(
        ref TaskDialogConfig config, out int buttonPressed, out int radioButtonPressed, out bool verificationChecked);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct TaskDialogButton
    {
        public int ButtonId;
        public IntPtr ButtonText;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct TaskDialogConfig
    {
        public uint Size;
        public IntPtr ParentWindow;
        public IntPtr Instance;
        public uint Flags;
        public uint CommonButtons;
        public IntPtr WindowTitle;
        public IntPtr MainIcon;
        public IntPtr MainInstruction;
        public IntPtr Content;
        public uint ButtonCount;
        public IntPtr Buttons;
        public int DefaultButton;
        public uint RadioButtonCount;
        public IntPtr RadioButtons;
        public int DefaultRadioButton;
        public IntPtr VerificationText;
        public IntPtr ExpandedInformation;
        public IntPtr ExpandedControlText;
        public IntPtr CollapsedControlText;
        public IntPtr FooterIcon;
        public IntPtr Footer;
        public IntPtr Callback;
        public IntPtr CallbackData;
        public uint Width;
    }
}
