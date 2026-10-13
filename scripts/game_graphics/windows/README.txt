APEX UE5.8 - native qualification launcher (not yet executed in Unreal)

Extract the complete ZIP into a normal local folder outside C:\GameProject.
Double-click Run-APEX-UE58.cmd at the root of the extracted bundle.
No administrator rights required. Close other Unreal/Blender/model sessions yourself first;
the launcher refuses competing graphics/model work and never closes somebody else's editor.

The launcher looks for installed UE5.8 and Python x64 3.11-3.13 with venv/ensurepip
(first the Python delivered with Unreal). If none works, install Python 3.12 x64
from https://www.python.org/downloads/windows/ and retry, or pass -PythonExe.
It installs three hash-pinned host libraries (about 21 MB) from PyPI HTTPS into a
new TEMP venv. It does not install Unreal, models, CUDA, or modify engine files.

Every invocation uses a new %TEMP%\APEX-UE58-<uuid> directory; there is no project-path
option. Previous runs remain. Each real editor is limited to 15 minutes and sampled
12 GB process-tree RAM; captures have an 8-minute timeout. VRAM readings are whole-GPU
NVIDIA samples, not exact peaks or renderer FPS. Keep the Windows session open.

The accepted PR16 scene plan is fixed input: road, building, three props, three PBR
materials and two lights. No new AI inference is claimed by this launcher.
Expected chain: baseline PASS -> native road displacement FAIL -> targeted correction
PASS, separate editor processes and four native views at every inspected version.
Only one correction is attempted. Missing evidence or unqualified APIs stops the run.

Results: validation-report.json and the three versioned projects in the TEMP run folder;
archive alongside it as APEX-UE58-<uuid>.zip. Even failures preserve logs and diagnostics.
No automatic upload or deletion. The archive may contain your Windows username in paths:
review it, then return the archive for diagnosis/validation if desired.

See docs\game_graphics\ue\WINDOWS.md for optional engine/Python paths, criteria and limits.
