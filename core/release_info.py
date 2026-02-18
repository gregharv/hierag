from __future__ import annotations

import re
from pathlib import Path

import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
CHANGELOG_PATH = PROJECT_ROOT / "docs" / "changelog.qmd"
CHANGELOG_URL = "/connections/reference/changelog"
UNKNOWN_VERSION = "0.0.0+unknown"
VERSION_HEADING_RE = re.compile(
    r"^\s*##\s+v(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\s*$"
)


def _read_version_from_changelog(changelog_path: Path = CHANGELOG_PATH) -> str | None:
    try:
        content = changelog_path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in content.splitlines():
        match = VERSION_HEADING_RE.match(line)
        if match:
            version = str(match.group("version")).strip()
            return version or None
    return None


def _read_version_from_pyproject(pyproject_path: Path = PYPROJECT_PATH) -> str:
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return UNKNOWN_VERSION

    tool = payload.get("tool", {})
    poetry = tool.get("poetry", {})
    version = str(poetry.get("version", "")).strip()
    return version or UNKNOWN_VERSION


def _read_version(
    changelog_path: Path = CHANGELOG_PATH,
    pyproject_path: Path = PYPROJECT_PATH,
) -> str:
    changelog_version = _read_version_from_changelog(changelog_path)
    if changelog_version:
        return changelog_version
    return _read_version_from_pyproject(pyproject_path)


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
