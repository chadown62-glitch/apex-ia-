# Game Graphics candidate — Issue #25

Qualification in progress. This branch is stacked on PR #24 (`10bd282`), which is
stacked on #22 then #19. No merge, release, Windows installation or model training
has been performed. Frozen benchmark criteria: `benchmark.json`.

Implemented: source-grounded bounded plan coverage, explicit unavailable specialized
builders, persistent active-time and attempt budgets, worker file leases, owned
process receipts, existing Specialty catalogue contract, Game Studio frontend
planning for every request, technical best-version retention, and scoped material
checks. The composed specialist is Qwen + fixed Blender operations + contracts +
judges, not a new checkpoint.

The eleven historical Tool Engine execution failures are reproduced unchanged.
See `sandbox-diagnostic.json`: detection runs a real isolated container, but the
execution launcher sanitizes PATH to `/usr/bin:/bin` while Docker is installed at
`/usr/local/bin/docker`. No candidate code executes in those tests. The shared fix
is proposed in Issue #21; neither sandbox implementation nor Claude tests changed.

Remaining qualifications: true Windows launcher, CUDA memory/performance, brain
quiescence ACK and registration, UE5.8 editor/physics, TripoSR mission dispatch,
reference-image comprehension, artistic fidelity outside explicit rule scope.
Full measured results and reproducible artifact manifest will follow in this PR.
