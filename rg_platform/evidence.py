"""Preserve the exact inputs used by an isolated lab run."""
import hashlib
import os
from pathlib import Path


def archive_configs(output, configs):
    """Copy both runtime files before processes start; fail if evidence cannot persist."""
    if set(configs) != {"ap", "sta"}:
        raise ValueError("both ap and sta runtime configs are required")
    contents = {role: Path(path).read_bytes() for role, path in configs.items()}
    destination = Path(output) / "runtime-configs"
    destination.mkdir(mode=0o700, exist_ok=False)
    manifest = {}
    for role, data in contents.items():
        archived = destination / (role + ".conf")
        with archived.open("xb") as stream:
            stream.write(data)
        os.chmod(archived, 0o600)
        manifest[role] = {"runtime_path": str(configs[role]),
                          "evidence_path": "runtime-configs/" + role + ".conf",
                          "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return manifest
