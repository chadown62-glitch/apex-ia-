import pathlib

p = pathlib.Path("apex/gate/checks.py")
s = p.read_text()

OLD = '''def gate_11_disk(ctx: GateContext) -> GateCheckResult:
    title = "Espace disque"
    target = ctx.config.paths.data_root if ctx.config else "/"
    free = ctx.probe.disk_free_gb(target)
    if free is None:
        return _degraded("GATE-11", title, f"espace libre ind\\u00e9termin\\u00e9 sur {target}",
                     facts={"disk_free_gb": None, "path": target})
    facts = {"disk_free_gb": round(free, 1), "path": target}
    if free < MIN_FREE_GB:
        return _degraded(
            "GATE-11", title, f"{free:.0f} Go libres \\u2014 {MIN_FREE_GB:.0f} Go recommand\\u00e9s",
            remediation=(
                "Poids de mod\\u00e8les (~20 Go par mod\\u00e8le Q4), images de conteneurs et journaux "
                "d'\\u00e9valuation cumulent vite. Le disque virtuel WSL2 (.vhdx) cro\\u00eet sans se "
                "r\\u00e9duire seul : pr\\u00e9voir un compactage p\\u00e9riodique."
            ),
            facts=facts,
        )
    return _ok("GATE-11", title, f"{free:.0f} Go libres sur {target}", facts=facts)'''

NEW = '''def gate_11_disk(ctx: GateContext) -> GateCheckResult:
    """Espace disque du systeme de fichiers qui portera les donnees d'APEX.

    K1 : la version precedente mesurait directement `paths.data_root`. Ce
    repertoire est cree a l'usage, pas a l'installation — il n'existe donc pas
    sur une machine neuve, `shutil.disk_usage` levait, et le controle
    rapportait « espace libre indetermine ». C'etait un faux DEGRADED : la
    machine pouvait avoir largement assez de place.

    La sonde remonte desormais au premier ancetre existant. C'est le meme
    point de montage, donc la mesure est valide pour le futur chemin, et
    aucun repertoire n'est cree.
    """
    title = "Espace disque"
    target = ctx.config.paths.data_root if ctx.config else "/"
    probe_result = ctx.probe.disk_probe(target)

    facts: dict[str, Any] = {
        "path_requested": target,
        "path_measured": probe_result.measured_path,
        "path_exists": probe_result.requested_exists,
        "measured_on_ancestor": probe_result.fell_back_to_ancestor,
        "disk_free_gb": round(probe_result.free_gb, 1) if probe_result.free_gb else None,
        "disk_total_gb": round(probe_result.total_gb, 1) if probe_result.total_gb else None,
        "disk_error": probe_result.error,
    }

    # Cas 4 : erreur systeme reelle. Ne jamais masquer.
    if probe_result.error and probe_result.error != "aucun ancetre existant":
        return _degraded(
            "GATE-11", title,
            f"mesure impossible sur {probe_result.measured_path} : {probe_result.error}",
            remediation=(
                "Erreur systeme, distincte d'un repertoire absent. Verifier les permissions "
                "et l'etat du peripherique avant de conclure sur l'espace disponible."
            ),
            facts=facts,
        )

    # Cas 3 : aucune mesure possible.
    if not probe_result.measured:
        return _unknown(
            "GATE-11", title, f"aucun systeme de fichiers mesurable pour {target}",
            reason="unmeasurable", facts=facts,
        )

    free = probe_result.free_gb or 0.0
    where = probe_result.measured_path
    origin = (
        f"{free:.0f} Go libres sur {where}"
        if not probe_result.fell_back_to_ancestor
        else f"{free:.0f} Go libres sur {where} (le chemin cible {target} n'existe pas encore)"
    )

    # Cas 2 : espace reellement insuffisant.
    if free < MIN_FREE_GB:
        return _degraded(
            "GATE-11", title,
            f"{origin} \\u2014 {MIN_FREE_GB:.0f} Go recommandes",
            remediation=(
                "Poids de modeles (~20 Go par modele Q4), images de conteneurs et journaux "
                "d'evaluation cumulent vite. Le disque virtuel WSL2 (.vhdx) croit sans se "
                "reduire seul : prevoir un compactage periodique."
            ),
            facts=facts,
        )

    # Cas 1 : mesure valide et suffisante.
    return _ok("GATE-11", title, origin, facts=facts)'''

assert OLD in s, "bloc GATE-11 introuvable"
s = s.replace(OLD, NEW)
p.write_text(s)
print("GATE-11 reecrit")
