from __future__ import annotations

from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
CHANGELOG_URL = "/connections/reference/changelog"
UNKNOWN_VERSION = "0.0.0+unknown"


def _read_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return UNKNOWN_VERSION

    tool = payload.get("tool", {})
    poetry = tool.get("poetry", {})
    version = str(poetry.get("version", "")).strip()
    return version or UNKNOWN_VERSION


def get_release_info() -> dict[str, str]:
    return {
        "version": _read_version(),
        "changelog_url": CHANGELOG_URL,
    }


# %%
if __name__ == "__main__":
    info = get_release_info()
    assert info["version"]
    assert info["changelog_url"] == CHANGELOG_URL
    print("Check Passed")
