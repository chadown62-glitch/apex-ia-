"""Retain ContainerSandbox isolation; resolve its actual host CLI before launch."""

import shutil

from apex.sandbox.container import ContainerSandbox
from apex.sandbox.model import SandboxSpec


class AbsoluteDockerSandbox(ContainerSandbox):
    def _build_argv(self, spec: SandboxSpec, name: str) -> list[str]:
        argv = super()._build_argv(spec, name)
        executable = shutil.which("docker")
        if executable is None:
            raise FileNotFoundError("Docker CLI absent")
        argv[0] = executable
        return argv
