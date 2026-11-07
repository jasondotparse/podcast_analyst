"""Install repository-owned files without disturbing Codex runtime state."""

from pathlib import Path
import shutil


def sync_codex_config(source: Path, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("config.toml", "AGENTS.md"):
        shutil.copyfile(source / name, home / name)

    # This app owns the entire skills directory; removed skills must disappear.
    skills = home / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    shutil.copytree(source / "skills", skills)
