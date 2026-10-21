# PyInstaller 配置：生成 dist/日语学习助手.app
# 用法：packaging/macos/build.sh
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent.parent
VERSION = "0.1.0"

a = Analysis(
    [str(HERE / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=collect_data_files("nihongo"),
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("webview"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nihongo",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="nihongo")
app = BUNDLE(
    coll,
    name="日语学习助手.app",
    icon=str(HERE / "build" / "icon.icns"),
    bundle_identifier="com.liangrk5.nihongo-tutor",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "日语学习助手",
        "CFBundleShortVersionString": VERSION,
        "LSApplicationCategoryType": "public.app-category.education",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "发音练习需要录下你的朗读，交给 AI 老师评测。",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSMinimumSystemVersion": "12.0",
    },
)
