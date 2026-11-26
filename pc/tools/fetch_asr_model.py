"""下载并解压流式中文识别模型到 .pio/asr_models。

只做一次，之后工作台离线加载。用法：
    .venv/Scripts/python.exe pc/tools/fetch_asr_model.py [zipformer-zh]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stream_asr import DEFAULT_MODEL, MODELS, MODEL_ROOT, model_files

BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


def fetch(name: str) -> Path:
    if name not in MODELS:
        raise SystemExit(f"未知模型：{name}，可选：{', '.join(MODELS)}")
    folder, archive = MODELS[name]
    target = MODEL_ROOT / folder
    if target.is_dir():
        try:
            model_files(name)
            print(f"已存在：{target}")
            return target
        except RuntimeError:
            shutil.rmtree(target)
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    package = MODEL_ROOT / archive
    print(f"下载 {archive} ...", flush=True)
    urllib.request.urlretrieve(BASE_URL + archive, package)
    print("解压 ...", flush=True)
    with tarfile.open(package, "r:bz2") as bundle:
        bundle.extractall(MODEL_ROOT, filter="data")
    package.unlink()
    model_files(name)
    print(f"完成：{target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", default=DEFAULT_MODEL)
    fetch(parser.parse_args().model)
