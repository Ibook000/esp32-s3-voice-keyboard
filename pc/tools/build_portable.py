"""Build the Windows x64 workbench folder and ZIP from the installed PC dependencies."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import distribution
from pathlib import Path, PureWindowsPath
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PC = Path(__file__).resolve().parents[1]
PROJECT = PC.parent
sys.path.insert(0, str(PC))
from stream_asr import DEFAULT_MODEL, model_dir, model_files  # noqa: E402

PC_MODULES = (
    "keyboard_app.py", "keymap_client.py", "keymap_protocol.py", "speech_process.py", "stream_asr.py",
    "voice_audio.py", "voice_background.py", "voice_discovery.py", "voice_protocol.py",
    "voice_receiver.py", "web_config.py", "windows_input.py",
)
WEB_ASSETS = ("app.js", "index.html", "model.js", "style.css", "voice.js")


def stage_python(runtime: Path) -> None:
    base = Path(sys.base_prefix)
    version = f"{sys.version_info.major}{sys.version_info.minor}"
    runtime.mkdir()
    for pattern in ("python.exe", "pythonw.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt"):
        for source in base.glob(pattern):
            shutil.copy2(source, runtime / source.name)
    shutil.copytree(
        base / "DLLs", runtime / "DLLs", ignore=shutil.ignore_patterns("_test*", "_ctypes_test*")
    )

    library = base / "Lib"
    excluded = {"site-packages", "test", "ensurepip", "idlelib", "turtledemo"}
    with ZipFile(runtime / f"python{version}.zip", "w", ZIP_DEFLATED) as archive:
        for source in library.rglob("*"):
            relative = source.relative_to(library)
            if relative.parts[0] in excluded or "__pycache__" in relative.parts:
                continue
            if source.is_file():
                archive.write(source, relative.as_posix())
    (runtime / f"python{version}._pth").write_text(
        f"python{version}.zip\nDLLs\nLib/site-packages\n../pc\nimport site\n", encoding="ascii"
    )


def stage_dependencies(target: Path) -> None:
    required = []
    for requirements in (PC / "requirements.txt", PC / "requirements-asr.txt"):
        for raw in requirements.read_text(encoding="utf-8").splitlines():
            line = raw.partition("#")[0].strip()
            if line and not line.startswith("-r "):
                required.append(Requirement(line))

    copied: set[str] = set()
    while required:
        requirement = required.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        package = distribution(name)
        if package.version not in requirement.specifier:
            raise RuntimeError(f"Installed {name} {package.version} does not meet {requirement}")
        if name in copied:
            continue
        for filename in package.files or ():
            relative = PureWindowsPath(str(filename))
            if relative.anchor or ".." in relative.parts or relative.suffix == ".pyc":
                continue
            # Installed-wheel metadata can contain absolute source paths and cache entries.
            if relative.name in {"direct_url.json", "RECORD"}:
                continue
            source = Path(package.locate_file(filename))
            if source.is_file():
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        required.extend(Requirement(value) for value in package.requires or ())
        copied.add(name)


def build(folder: Path, compiler: str) -> None:
    runtime = folder / "runtime"
    stage_python(runtime)
    stage_dependencies(runtime / "Lib" / "site-packages")

    bundled_pc = folder / "pc"
    for name in PC_MODULES + tuple(f"web/{name}" for name in WEB_ASSETS) + (
        "tools/__init__.py", "tools/background.py", "tools/fetch_asr_model.py",
    ):
        destination = bundled_pc / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PC / name, destination)

    model = model_dir(DEFAULT_MODEL)
    bundled_model = folder / model.relative_to(PROJECT)
    bundled_model.mkdir(parents=True)
    for source in model_files(DEFAULT_MODEL):
        shutil.copy2(source, bundled_model / source.name)
    if (model / "README.md").is_file():
        shutil.copy2(model / "README.md", bundled_model / "README.md")

    launcher = folder / "ibook Keyboard.exe"
    subprocess.run(
        [compiler, "-O2", "-municode", "-mwindows", "-static", "-finput-charset=UTF-8",
         str(PC / "tools" / "windows_launcher.c"), "-o", str(launcher), "-luser32"],
        check=True,
    )
    subprocess.run(
        [str(runtime / "python.exe"), "-B", "-c",
         "import bleak, faster_whisper, sherpa_onnx, web_config; from stream_asr import model_files; model_files()"],
        check=True,
    )
    (folder / "使用说明.txt").write_text(
        "ibook Keyboard PC 工作台（Windows x64）\n\n"
        "完整解压后双击 ibook Keyboard.exe。已内置默认中文流式模型。\n"
        "如果旧工作台正在运行，请先在旧页面点「退出服务」，再启动便携版。\n"
        "设置和录音分别保存在 pc/runtime 和 pc/recordings。\n",
        encoding="utf-8-sig",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist" / "ibook-keyboard-win64")
    parser.add_argument("--compiler", default="gcc", help="Windows x64 MinGW-w64 GCC")
    args = parser.parse_args()
    if sysconfig.get_platform() != "win-amd64":
        parser.error("Run this build with Windows x64 Python.")
    if not shutil.which(args.compiler):
        parser.error(f"Compiler not found: {args.compiler}")
    destination = args.output.resolve()
    archive_path = destination.with_name(destination.name + ".zip")
    if destination.exists() or archive_path.exists():
        parser.error("Choose a new --output path for this build.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ibook-build-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        folder = staging / destination.name
        folder.mkdir()
        build(folder, args.compiler)
        zip_output = staging / archive_path.name
        with ZipFile(zip_output, "w", ZIP_DEFLATED, compresslevel=3) as archive:
            for source in sorted(folder.rglob("*")):
                if source.is_file():
                    archive.write(source, source.relative_to(staging).as_posix())
        with ZipFile(zip_output) as archive:
            if bad_entry := archive.testzip():
                raise RuntimeError(f"ZIP validation failed for {bad_entry}")
        folder.rename(destination)
        zip_output.rename(archive_path)
    print(archive_path)


if __name__ == "__main__":
    main()
