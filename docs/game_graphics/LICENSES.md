# Licences vérifiées avant téléchargement

Décisions du 12 octobre 2026, utilisation locale en France. Les textes complets effectivement consultés et leurs empreintes sont conservés dans `evidence/licenses/`. Les poids Qwen sont hors dépôt.

| Composant | Source vérifiée | Décision |
|---|---|---|
| Qwen2.5-Coder-7B-Instruct Q5_K_M | `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF`, commit `13fb94bfda8c8cf22497dc57b78f391a9acb426a`, LICENSE Apache-2.0 | Retenu, dépôt non gated. Licence compatible avec la baseline locale en France ; conserver licence et notices lors d’une redistribution. |
| Base originale Qwen Coder 7B | `Qwen/Qwen2.5-Coder-7B-Instruct`, commit `c03e6d358207e414f1eca0bb1891e29f1db0e242`, Apache-2.0 | Candidat pour une future adaptation, poids originaux non téléchargés ici. |
| TripoSR | `stabilityai/TripoSR`, commit `5b521936b01fbe1890f6f9baed0254ab6351c04a`, metadata MIT et code `VAST-AI-Research/TripoSR` LICENSE MIT | Candidat légal retenu pour qualification, poids non téléchargés. Qualification GPU BLOCKED : aucune CUDA ici. Reconstruction CPU non mesurée. |
| Hunyuan3D 2.0 | Licence Tencent Hunyuan 3D 2.0 consultée | Exclu : territoire excluant Union européenne, Royaume-Uni et Corée du Sud ; clause interdisant aussi l’usage des outputs hors territoire. Aucun poids téléchargé. |
| llama-cpp-python 0.3.36 | Métadonnées PyPI et licence MIT | Runtime local CPU retenu. |
| Blender 4.3.2 | Binaire installé et copyright de la distribution Debian, GPL-2+ et licences des composants | Exécutable séparé ; pas de redistribution de son binaire ici. Les rendus et maillages originaux ne sont pas du code Blender réutilisé. |

La vérification ne transforme pas une licence open weights en garantie générale de tous les usages. Aucune ressource artistique tierce, aucune capture d’Unreal, aucun dataset commercial ou gated n’a été utilisé. Les demandes, scripts procéduraux et assets sont créés pour cette mission autorisée par l’utilisateur. Le dépôt ne fournit pas de licence générale : les exemples restent `LicenseRef-APEX-User-Authorized-Internal`, sans invention d’un droit de redistribution externe.

Artefact Qwen réellement utilisé : `qwen2.5-coder-7b-instruct-q5_k_m.gguf`, 5 444 831 232 octets, SHA-256 `586844eac4d6d6321689f0192c8aa8e69cd8625974a5cc2d925b1a03366e4d16`. SHA-256 du LICENSE officiel : `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e`. Le chargement vérifie le commit, la licence et l’empreinte du modèle ; le téléchargement explicite vérifie d’abord le texte de licence immuable.

TripoSR ne garantit pas des roues séparées, des pivots ou un rig automobile à partir d’une image. Pour ce premier contrat technique, le contrôleur de paramètres et Blender procédural produisent des pièces contrôlables. La claim amont d’environ 6 Go de VRAM pour TripoSR n’est pas une mesure de cette machine ni une preuve de compatibilité 8 Go avec Blender simultané. Il n’existe pas de comparaison de qualité mesurée avec TripoSR dans ce lot.
