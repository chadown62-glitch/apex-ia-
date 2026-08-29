# ADR-0009 — Décodage des sorties de processus

**Statut :** Acceptée — phase 1, issue de la première mesure réelle (machine #1)

## Contexte

Premier Gate exécuté sur une machine physique. GATE-01 a échoué sur
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0x82 in position 59`
en appelant `cmd.exe /c ver`.

`cmd.exe` écrit dans la page de code OEM de la console Windows — CP850 en
français, où 0x82 est « é » — et non en UTF-8. `subprocess.run(text=True)`
délègue le décodage à Python, qui utilise l'encodage local (UTF-8 sous WSL) en
mode **strict**.

Le défaut n'était pas dans GATE-01 : toute commande Windows appelée via
l'interop WSL aurait produit le même crash.

## Décision

`SystemProbe.run` capture des **octets** (`text=False`) et décode explicitement
via `decode_process_output`, selon une stratégie ordonnée : UTF-16 si BOM ou
octets nuls, puis UTF-8 strict, puis les pages OEM (CP850, CP437, CP1252),
puis latin-1 en dernier recours — un codec qui ne peut pas échouer.

`CommandResult` porte l'encodage retenu, et GATE-01 le publie dans ses faits
(`output_encoding`).

## Conséquence

Aucun appel de sonde ne peut plus échouer à cause d'un encodage. Le dernier
recours n'est pas un contournement : l'encodage retenu est visible dans le
rapport, ce qui permet à un lecteur de juger si la sortie est exploitable.

L'ordre compte : UTF-8 est tenté avant les pages OEM, sinon une sortie Linux
accentuée serait décodée en CP850. Un test le vérifie.

## Écarté

`errors="replace"` sur un décodage UTF-8 unique : aurait masqué le problème en
produisant des caractères de remplacement, sans jamais signaler que la sortie
était dans un autre encodage. C'est précisément le contournement à éviter.
