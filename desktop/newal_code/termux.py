"""NewAl Code in Termux, linked with NewAl Code Lite (the Android app).

Termux has a whole Linux (git, compilers, any package, the user's projects in its home). Linked, NewAl Code runs
there, with the app as its window (the app shows NewAl Code from Termux as it shows its own), its model (the GGUF the
app runs, through the app's OpenAI-compatible /v1) and its hands on the phone (the phone tool, through the app's
phone server).

The setup is one command the app copies for the user to paste into Termux: `curl ...?once=... | bash`. The token in
it works once, for 15 minutes; the script it fetches carries the app's key into Termux's own files. It installs
Python and git in Termux when they are missing, NewAl Code (the app's own copy), a `newal` command, and
`newal-termux start|stop|update`; it allows the app to start NewAl Code in Termux later (Termux's
allow-external-apps), and starts it on 127.0.0.1:8791."""

import io
import os
import secrets
import threading
import time
import zipfile

PORT = int(os.environ.get("NEWAL_TERMUX_PORT") or 8791)
PHONE_PORT = 8793
_once = {}
_lock = threading.Lock()


def link_command(app_port):
    """The command the user pastes into Termux (a token that works once, for 15 minutes)."""
    token = secrets.token_urlsafe(18)
    with _lock:
        now = time.time()
        for t in [t for t, exp in _once.items() if exp < now]:
            _once.pop(t, None)
        _once[token] = now + 900
    return "curl -fsSL 'http://127.0.0.1:%d/termux/setup?once=%s' | bash" % (app_port, token)


def use_token(token):
    with _lock:
        exp = _once.pop(token or "", 0)
    return exp > time.time()


def package_zip():
    """NewAl Code itself (this copy), for Termux."""
    here = os.path.dirname(os.path.abspath(__file__))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(here):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for f in filenames:
                if f.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, f)
                z.write(full, os.path.join("newal_code", os.path.relpath(full, here)))
    return buf.getvalue()


def _sh(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def setup_script(app_port, key):
    """The bash script Termux runs (key: the app's; it goes into Termux's private files only)."""
    return SCRIPT.replace("@KEY@", _sh(key)).replace("@APP@", _sh("http://127.0.0.1:%d" % app_port)) \
        .replace("@PORT@", str(PORT)).replace("@PHONE@", _sh("http://127.0.0.1:%d" % PHONE_PORT))


SCRIPT = r'''#!/data/data/com.termux/files/usr/bin/bash
# NewAl Code in Termux, linked with NewAl Code Lite. Made by the app for this phone; safe to run again.
set -e
KEY=@KEY@
APP=@APP@
H="$HOME/.newal-code/termux"
echo "NewAl Code: setting up in Termux..."
need=""
command -v python > /dev/null 2>&1 || need="$need python"
command -v git > /dev/null 2>&1 || need="$need git"
if [ -n "$need" ]; then
  echo "Installing$need (Termux's packages)..."
  yes | pkg install -y $need > "$HOME/.newal-code-install.log" 2>&1 || { pkg update -y > /dev/null 2>&1 || true; yes | pkg install -y $need; }
fi
mkdir -p "$H" "$HOME/projects"
chmod 700 "$HOME/.newal-code" "$H"
printf '%s' "$KEY" > "$H/key"
chmod 600 "$H/key"
curl -fsSL --retry 20 --retry-connrefused --retry-delay 3 -H "X-NewAl-Key: $KEY" "$APP/termux/app.zip" -o "$H/app.zip"
rm -rf "$H/app.new"
python -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$H/app.zip" "$H/app.new"
rm -rf "$H/app" && mv "$H/app.new" "$H/app"
printf '{"url": "%s", "key": "%s", "app": "%s"}\n' @PHONE@ "$KEY" "$APP" > "$HOME/.newal-code/phone.json"
chmod 600 "$HOME/.newal-code/phone.json"
# The phone's own model (the GGUF NewAl Code Lite runs) through the app, unless a model is chosen already.
PYTHONPATH="$H/app" NEWAL_CODE_HOME="$HOME/.newal-code" python - "$KEY" "$APP" <<'PY'
import sys
from newal_code import settings
key, app = sys.argv[1], sys.argv[2]
cfg = settings.user()
models = dict(cfg.get("models") or {})
models["phone"] = {"provider": "openai", "base_url": app + "/v1", "model": "phone", "api_key": key,
                   "name": "The phone's model (NewAl Code Lite)", "context": 16384}
values = {"models": models, "recent_projects": cfg.get("recent_projects") or []}
if not cfg.get("model") or cfg.get("model") == "auto":
    values["model"] = "phone"
settings.save(values)
PY
cat > "$PREFIX/bin/newal-termux" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# NewAl Code in Termux, linked with NewAl Code Lite: newal-termux start | stop | status | update
H="$HOME/.newal-code/termux"
export PYTHONPATH="$H/app" NEWAL_CODE_HOME="$HOME/.newal-code" NEWAL_SERVER_KEY="$(cat "$H/key")"
up() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:@PORT@/icon.svg"; }
case "${1:-start}" in
  start)
    if up; then echo "NewAl Code runs in Termux already."; exit 0; fi
    cd "$HOME"
    nohup python -m newal_code app --port @PORT@ --no-browser >> "$H/server.log" 2>&1 &
    for i in $(seq 1 40); do up && break; sleep 0.5; done
    if up; then echo "NewAl Code runs in Termux: go back to NewAl Code Lite."; else echo "NewAl Code did not start: $H/server.log"; exit 1; fi ;;
  stop) pkill -f "newal_code app --port @PORT@" && echo "stopped" ;;
  status) if up; then echo "running"; else echo "stopped"; fi ;;
  update)
    APP="$(python -c 'import json,os; print(json.load(open(os.path.expanduser("~/.newal-code/phone.json")))["app"])')"
    curl -fsSL --retry 20 --retry-connrefused --retry-delay 3 -H "X-NewAl-Key: $NEWAL_SERVER_KEY" "$APP/termux/app.zip" -o "$H/app.zip" && rm -rf "$H/app.new" &&
      python -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$H/app.zip" "$H/app.new" &&
      rm -rf "$H/app" && mv "$H/app.new" "$H/app" && "$0" stop; "$0" start ;;
  *) echo "newal-termux start | stop | status | update"; exit 2 ;;
esac
SH
cat > "$PREFIX/bin/newal" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
H="$HOME/.newal-code/termux"
export PYTHONPATH="$H/app" NEWAL_CODE_HOME="$HOME/.newal-code" NEWAL_SERVER_KEY="$(cat "$H/key")"
exec python -m newal_code "$@"
SH
chmod 700 "$PREFIX/bin/newal-termux" "$PREFIX/bin/newal"
# Let NewAl Code Lite start it next time (Termux's RUN_COMMAND, with the permission the user gives the app).
mkdir -p "$HOME/.termux"
grep -qs '^ *allow-external-apps *= *true' "$HOME/.termux/termux.properties" || echo 'allow-external-apps = true' >> "$HOME/.termux/termux.properties"
command -v termux-reload-settings > /dev/null 2>&1 && termux-reload-settings || true
newal-termux stop > /dev/null 2>&1 || true
newal-termux start
'''
