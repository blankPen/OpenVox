"""openvox_worker — the OpenVox voice-agent runtime.

Installable via ``pip install -e ./apps/voice-agent`` the package exposes the
``openvox`` console script (defined in ``pyproject.toml: [project.scripts]``)
which delegates to :mod:`openvox_worker.cli`. The LiveKit worker itself
lives in :mod:`openvox_worker.main` and is launched by
``openvox start`` via ``python -m openvox_worker.main``.
"""
__version__ = "0.2.0"
