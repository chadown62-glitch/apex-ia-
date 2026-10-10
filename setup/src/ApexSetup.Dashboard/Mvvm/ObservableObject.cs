using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace ApexSetup.Dashboard.Mvvm;

/// <summary>Base MVVM minimale — même choix qu'ApexSetup.Gui.Mvvm.ObservableObject (copie délibérée plutôt qu'un partage inter-projet pour ~20 lignes, voir sa docstring).</summary>
public abstract class ObservableObject : INotifyPropertyChanged
{
    /// <summary>Créé sur le fil d'interface : ses notifications y retournent toujours (voir <see cref="InterfaceThread"/>).</summary>
    private readonly bool _ownedByInterface = InterfaceThread.IsCurrent();

    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }
        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    protected void OnPropertyChanged([CallerMemberName] string? propertyName = null) =>
        InterfaceThread.Notify(_ownedByInterface, () => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName)));
}
