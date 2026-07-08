from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging import tags


ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "tcanny"
UPSTREAM_DLL = "TCanny.dll"
DEFAULT_PREBUILT_URL = (
    "https://github.com/HomeOfVapourSynthEvolution/VapourSynth-TCanny/"
    "releases/download/r14/TCanny-r14-win64.7z"
)
DEFAULT_PREBUILT_SHA256 = "4ff024b35da0f2320328d6c3166e943fbc9436543fef891c71bda3725c6d2527"


def _supports_prebuilt() -> bool:
    return sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}


def _prebuilt_source() -> str:
    from os import environ

    return environ.get("TCANNY_PREBUILT_URL") or DEFAULT_PREBUILT_URL


def _expected_sha256() -> str:
    from os import environ

    return environ.get("TCANNY_PREBUILT_SHA256") or DEFAULT_PREBUILT_SHA256


def _fetch_prebuilt_archive(source: str, destination: Path) -> None:
    candidate = Path(source)
    if candidate.exists():
        shutil.copy2(candidate, destination)
        return

    request = urllib.request.Request(source, headers={"User-Agent": "vapoursynth-tcanny-build-hook"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _check_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"{path.name} sha256 mismatch: expected {expected}, got {actual}")


def _find_7z() -> str | None:
    found = shutil.which("7z") or shutil.which("7z.exe")
    if found:
        return found
    for candidate in [
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def _extract_7z(archive_path: Path, extract_dir: Path) -> None:
    try:
        import py7zr
    except ModuleNotFoundError:
        seven_zip = _find_7z()
        if seven_zip is None:
            raise RuntimeError("extracting the upstream .7z asset requires py7zr or a 7z executable") from None
        extract_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([seven_zip, "x", str(archive_path), f"-o{extract_dir}", "-y"], check=True)
        return

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        archive.extractall(path=extract_dir)


def _write_manifest(target_dir: Path) -> None:
    (target_dir / "manifest.vs").write_text(
        "[VapourSynth Manifest V1]\n"
        f"{PLUGIN_NAME}\n",
        encoding="ascii",
        newline="\n",
    )


def _stage_prebuilt_plugin(target_dir: Path) -> None:
    if not _supports_prebuilt():
        raise RuntimeError("TCanny release-backed wheel builds are currently supported only on Windows x86_64")

    source = _prebuilt_source()
    expected_sha256 = _expected_sha256()
    with tempfile.TemporaryDirectory(prefix="tcanny-prebuilt-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        archive_path = temp_dir / (Path(source).name or "TCanny-r14-win64.7z")
        extract_dir = temp_dir / "extract"
        _fetch_prebuilt_archive(source, archive_path)
        _check_sha256(archive_path, expected_sha256)
        _extract_7z(archive_path, extract_dir)

        plugin_dll = extract_dir / UPSTREAM_DLL
        if not plugin_dll.exists():
            raise FileNotFoundError(f"prebuilt archive did not contain {UPSTREAM_DLL}")

        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_dll, target_dir / f"{PLUGIN_NAME}.dll")
        shutil.copy2(ROOT / "LICENSE", target_dir / "LICENSE")
        _write_manifest(target_dir)

    print(f"TCanny wheel build: using upstream release asset {source}")


class CustomHook(BuildHookInterface[Any]):
    dist_dir = ROOT / "vapoursynth" / "plugins" / PLUGIN_NAME

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{next(tags.platform_tags())}"

        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
        _stage_prebuilt_plugin(self.dist_dir)

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        shutil.rmtree(self.dist_dir.parent.parent, ignore_errors=True)
