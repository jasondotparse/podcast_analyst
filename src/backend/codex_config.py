"""Install repository-owned files without disturbing Codex runtime state."""

import json
from pathlib import Path
import shutil


CODEX_SOURCE = Path(__file__).resolve().parents[2] / "codex"


def sync_codex_config(source: Path, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("config.toml", "AGENTS.md"):
        shutil.copyfile(source / name, home / name)

    # This app owns the entire skills directory; removed skills must disappear.
    skills = home / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    shutil.copytree(source / "skills", skills)


def tool_specs(*names: str) -> list[dict]:
    """Dynamic-tool definitions from codex/tools/<name>.json, for a thread's `dynamicTools`."""
    return [json.loads((CODEX_SOURCE / "tools" / f"{name}.json").read_text()) for name in names]
