# APEX Setup — moteur (Phase 1 : Setup Core Contracts)

Solution .NET 8 séparée du Runtime Python. Aucun GUI, aucun `Setup.exe`
dans cette phase — uniquement le moteur testable décrit en conception
(document « APEX SETUP », approuvé).

## Structure

```
setup/
  ApexSetup.sln
  src/ApexSetup.Core/           bibliothèque — le moteur
    Contracts/                  types fortement typés (Version/Doctor/Gate/HardwareProfile/ModelManifest/...)
    RuntimeAdapter/              exécution subprocess + classification d'erreur
    Json/                       parsing strict, aucune désérialisation aveugle
    Hardware/                   profils déclaratifs + sélection + compatibilité modèle
    Integrity/                  vérification SHA-256
  tests/ApexSetup.Core.Tests/   xUnit — unitaires + intégration réelle (vrais sous-processus)
  docs/CONTRACTS.md             documentation détaillée des contrats
```

## Construire et tester

```
cd setup
dotnet build
dotnet test
```

## Principe non négociable

`ApexSetup.Core` ne référence **jamais** de code Python. Toute
communication avec le Runtime passe par `RuntimeAdapter` → sous-processus →
`--json` → parsing strict. Voir `docs/CONTRACTS.md`.
