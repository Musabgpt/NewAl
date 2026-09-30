# PyInstaller spec for NewAl Code on Windows, macOS and Linux: the app (its own window where there is a WebView, the
# browser elsewhere) and the newal-code command line, side by side in one folder ("NewAl Code.app" on macOS).
#   pyinstaller --noconfirm newal_code.spec
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("newal_code")
# The standard library that NewAl's plugins' programs use (newal_code/market), which the packaged app runs with
# its own Python (newal-code --newal-python) although newal_code itself may not import them.
hidden += ["platform", "heapq", "socket", "stat", "tempfile", "csv", "statistics", "zipfile", "xml.etree.ElementTree",
           "html", "html.parser", "math", "ctypes", "ctypes.wintypes", "runpy", "unittest", "http.server",
           "secrets", "urllib.request", "functools"] + (["winreg"] if sys.platform == "win32" else [])
datas = [("newal_code/ui", "newal_code/ui"), ("newal_code/market", "newal_code/market")]
icon = "assets/newal.ico" if sys.platform == "win32" else None

app = Analysis(["NewAlCode.py"], pathex=["."], hiddenimports=hidden, datas=datas)
cli = Analysis(["newal-code.py"], pathex=["."], hiddenimports=hidden, datas=datas)

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="NewAlCode", console=False, icon=icon)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="newal-code", console=True, icon=icon)
coll = COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas, name="NewAlCode")

if sys.platform == "darwin":
    app_bundle = BUNDLE(coll, name="NewAl Code.app", bundle_identifier="dev.newal.code",
                        info_plist={"CFBundleName": "NewAl Code", "CFBundleDisplayName": "NewAl Code",
                                    "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})
