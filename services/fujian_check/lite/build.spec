# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 规格：把"标书检查 Lite"打成一个文件夹（onedir）。
# 用法：cd 仓库根目录; .venv\Scripts\pyinstaller.exe services\fujian_check\lite\build.spec --noconfirm
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

import shutil

ROOT = Path(SPECPATH).resolve().parents[2]          # 仓库根
# 平台的 services/__init__.py 会导入数据库等重依赖；打包时用一个只含 fujian_check 的空壳 services 包代替。
STAGE = ROOT / "build" / "lite_stage"
if STAGE.exists():
    shutil.rmtree(STAGE)
(STAGE / "services").mkdir(parents=True)
(STAGE / "services" / "__init__.py").write_text("", encoding="utf-8")
shutil.copytree(ROOT / "services" / "fujian_check", STAGE / "services" / "fujian_check",
                ignore=shutil.ignore_patterns("tests", "__pycache__", "skill.py", "build.spec"))
LITE = STAGE / "services" / "fujian_check" / "lite"
import sys
sys.path.insert(0, str(STAGE))   # collect_submodules 要能 import 到空壳 services 包，否则动态装载的 rules/profiles 不会被打进去
ICON = STAGE / "icon.ico"
import importlib.util
_spec = importlib.util.spec_from_file_location("lite_server", LITE / "server.py")
_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod)
_mod._make_icon(ICON)

hidden = (
    collect_submodules("services.fujian_check")     # rules/profiles 是 import_module 动态装载的
    + collect_submodules("pdfplumber") + collect_submodules("pdfminer")
    + collect_submodules("docx") + collect_submodules("openai")
    + ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
       "uvicorn.lifespan.on", "multipart", "python_multipart", "anyio._backends._asyncio"]
)
datas = [(str(LITE / "index.html"), "services/fujian_check/lite")]
datas += collect_data_files("pdfminer") + collect_data_files("docx") + collect_data_files("pymupdf", include_py_files=False)

a = Analysis(
    [str(LITE / "server.py")],
    pathex=[str(STAGE)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL.ImageQt", "PyQt5", "PySide2", "IPython", "notebook", "pytest",
              "sqlalchemy", "asyncpg", "alembic", "celery", "redis", "torch", "transformers"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="标书检查",
    console=False,                # 不带黑窗口；日志写 lite_data/server.log，页面上有"退出程序"
    icon=str(ICON),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="标书检查Lite")
