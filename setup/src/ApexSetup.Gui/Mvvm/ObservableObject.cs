using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace ApexSetup.Gui.Mvvm;

/// <summary>
/// Base MVVM MINIMALE, écrite à la main plutôt que d'ajouter une
/// dépendance externe (CommunityToolkit.Mvvm ou ReactiveUI) — voir règle
/// explicite « ne pas introduire une dépendance externe inutile ». Une
/// vingtaine de lignes suffit à ce dont ce GUI a besoin ; Avalonia
/// lui-même reste la seule dépendance non triviale, déjà justifiée comme
/// le framework GUI lui-même.
/// </summary>
public abstract class ObservableObject : INotifyPropertyChanged
{
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
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
}
