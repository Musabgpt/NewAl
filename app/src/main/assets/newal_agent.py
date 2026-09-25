"""NewAl execution agent. Runs inside Termux and executes project code for the app.

Started by the app through Termux's RUN_COMMAND service (`python -c <this file>`),
with NEWAL_TOKEN / NEWAL_PORT prepended by the app. Listens on 127.0.0.1 only and
requires the token before accepting any other request.

Wire format (both directions): 9-byte header  >B op, >I id, >I length  + payload.
"""
import asyncio
import hashlib
import json
import os
import shutil
import signal
import struct
import sys
import time

TOKEN = globals().get("NEWAL_TOKEN") or os.environ.get("NEWAL_TOKEN", "")
PORT = int(globals().get("NEWAL_PORT") or os.environ.get("NEWAL_PORT", "47811"))
HOME = os.environ.get("NEWAL_HOME") or os.path.join(os.path.expanduser("~"), "newal")
ROOT = os.path.join(HOME, "projects")
VERSION = 1

HELLO, PING, FOPEN, FWRITE, FCLOSE, SYNC, VALIDATE, EXEC, KILL = 0x01, 0x02, 0x10, 0x11, 0x12, 0x14, 0x20, 0x30, 0x31
HELLO_OK, PONG, FCLOSED, SYNC_RESULT, VALIDATE_RESULT = 0x81, 0x82, 0x92, 0x94, 0xA0
STARTED, STDOUT, STDERR, EXIT, ERROR = 0xB0, 0xB1, 0xB2, 0xB3, 0xFF
HEADER = struct.Struct(">BII")
READ_CHUNK = 65536


def log(msg):
    try:
        with open(os.path.join(HOME, "agent.log"), "a") as f:
            f.write("%.3f %s\n" % (time.time(), msg))
    except OSError:
        pass


def project_path(project, rel=""):
    """Resolves a project-relative path, refusing anything that escapes the project."""
    if not project or "/" in project or project.startswith("."):
        raise ValueError("bad project id")
    base = os.path.join(ROOT, project)
    if not rel:
        return base
    if os.path.isabs(rel):
        raise ValueError("absolute path not allowed: " + rel)
    full = os.path.normpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        raise ValueError("path escapes project: " + rel)
    return full


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(READ_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def validate_file(path, rel):
    """Cheap in-process checks that catch obvious failures without spawning a process."""
    ext = os.path.splitext(rel)[1].lower()
    if ext not in (".py", ".json"):
        return None
    with open(path, "rb") as f:
        src = f.read()
    try:
        if ext == ".py":
            compile(src, rel, "exec", dont_inherit=True)
        else:
            json.loads(src.decode("utf-8"))
    except SyntaxError as e:
        return {"path": rel, "line": e.lineno or 0, "col": e.offset or 0,
                "msg": "%s: %s" % (type(e).__name__, e.msg), "text": (e.text or "").rstrip()}
    except (ValueError, UnicodeDecodeError) as e:
        return {"path": rel, "line": getattr(e, "lineno", 0), "col": getattr(e, "colno", 0),
                "msg": "%s: %s" % (type(e).__name__, e), "text": ""}
    return None


class Connection:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.files = {}
        self.procs = {}
        self.authed = False

    def send(self, op, rid, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif isinstance(payload, dict):
            payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.writer.write(HEADER.pack(op, rid, len(payload)) + payload)

    async def serve(self):
        try:
            while True:
                header = await self.reader.readexactly(HEADER.size)
                op, rid, length = HEADER.unpack(header)
                payload = await self.reader.readexactly(length) if length else b""
                if not self.authed:
                    if op == HELLO and payload.decode("utf-8", "replace") == TOKEN and TOKEN:
                        self.authed = True
                        self.send(HELLO_OK, rid, {"version": VERSION, "root": ROOT, "pid": os.getpid(),
                                                  "python": sys.version.split()[0]})
                        continue
                    self.send(ERROR, rid, "unauthorized")
                    await self.writer.drain()
                    return
                try:
                    await self.dispatch(op, rid, payload)
                except Exception as e:  # report every failure to the app; never die silently
                    log("error op=%d id=%d: %r" % (op, rid, e))
                    self.send(ERROR, rid, "%s: %s" % (type(e).__name__, e))
                if self.writer.transport.get_write_buffer_size() > 1 << 20:
                    await self.writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            for f in self.files.values():
                os.close(f[0])
            self.files.clear()
            self.writer.close()

    async def dispatch(self, op, rid, payload):
        if op == PING:
            self.send(PONG, rid, payload)
        elif op == FOPEN:
            req = json.loads(payload)
            path = project_path(req["project"], req["path"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            self.files[rid] = (fd, hashlib.sha256(), req["path"], path)
        elif op == FWRITE:
            fd, digest, _, _ = self.files[rid]
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            digest.update(payload)
        elif op == FCLOSE:
            fd, digest, rel, path = self.files.pop(rid)
            os.close(fd)
            sha = digest.hexdigest()
            expected = payload.decode("ascii")
            self.send(FCLOSED, rid, {"path": rel, "sha": sha, "size": os.path.getsize(path),
                                     "ok": not expected or expected == sha})
        elif op == SYNC:
            req = json.loads(payload)
            stale = []
            for rel, sha in req["files"].items():
                path = project_path(req["project"], rel)
                if not os.path.isfile(path) or sha256_file(path) != sha:
                    stale.append(rel)
            self.send(SYNC_RESULT, rid, {"stale": stale})
        elif op == VALIDATE:
            req = json.loads(payload)
            errors = []
            for rel in req.get("files", []):
                path = project_path(req["project"], rel)
                if not os.path.isfile(path):
                    errors.append({"path": rel, "line": 0, "col": 0, "msg": "file missing", "text": ""})
                    continue
                err = validate_file(path, rel)
                if err:
                    errors.append(err)
            missing = [c for c in req.get("commands", []) if shutil.which(c) is None]
            self.send(VALIDATE_RESULT, rid, {"errors": errors, "missing_commands": missing})
        elif op == EXEC:
            asyncio.ensure_future(self.run(rid, json.loads(payload)))
        elif op == KILL:
            proc = self.procs.get(rid)
            if proc:
                terminate(proc)
        else:
            raise ValueError("unknown op %d" % op)

    async def run(self, rid, req):
        loop = asyncio.get_running_loop()
        cwd = project_path(req["project"])
        os.makedirs(cwd, exist_ok=True)
        env = dict(os.environ)
        # Unbuffered, uncoloured output so the app sees every line as soon as it is printed.
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "TERM": "dumb",
                    "NO_COLOR": "1", "PY_COLORS": "0", "CLICOLOR": "0"})
        env.update(req.get("env") or {})
        timeout = max(1, req.get("timeout_ms", 60000)) / 1000.0
        # Scripted keyboard input for interactive programs; None means no stdin at all.
        stdin_data = req.get("stdin")

        # stdout goes through a pty so C/Node/etc. line-buffer instead of block-buffer;
        # stderr stays a separate pipe so the two streams are never mixed.
        master = slave = None
        try:
            import pty
            import termios
            master, slave = pty.openpty()
            attrs = termios.tcgetattr(slave)
            attrs[1] &= ~termios.OPOST  # no \n -> \r\n translation
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except Exception:
            master = slave = None
        err_r, err_w = os.pipe()
        out_r, out_w = (master, slave) if master is not None else os.pipe()

        start = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", req["command"], cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
                stdout=out_w, stderr=err_w,
                start_new_session=True)
        except Exception:
            for fd in (out_r, out_w, err_r, err_w):
                os.close(fd)
            raise
        os.close(out_w)
        os.close(err_w)
        self.procs[rid] = proc
        self.send(STARTED, rid, {"pid": proc.pid, "start_ms": int(start * 1000)})
        if stdin_data is not None:
            async def feed():
                try:
                    proc.stdin.write(stdin_data.encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the program exited before reading everything
                finally:
                    proc.stdin.close()
            asyncio.ensure_future(feed())

        open_fds = {out_r: STDOUT, err_r: STDERR}
        drained = asyncio.Event()

        def on_readable(fd):
            try:
                data = os.read(fd, READ_CHUNK)
            except OSError:  # EIO on a pty master once the child side is closed
                data = b""
            if data:
                self.send(open_fds[fd], rid, data)
                return
            loop.remove_reader(fd)
            os.close(fd)
            del open_fds[fd]
            if not open_fds:
                drained.set()

        for fd in list(open_fds):
            os.set_blocking(fd, False)
            loop.add_reader(fd, on_readable, fd)

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            terminate(proc)
            await proc.wait()
        end = time.time()
        # Output still in flight is delivered; a background grandchild holding the
        # pipes open must not block completion forever.
        try:
            await asyncio.wait_for(drained.wait(), 0.5)
        except asyncio.TimeoutError:
            for fd in list(open_fds):
                loop.remove_reader(fd)
                os.close(fd)
            open_fds.clear()
        self.procs.pop(rid, None)
        code = proc.returncode
        self.send(EXIT, rid, {"code": code if code >= 0 else 128 - code,
                              "signal": -code if code < 0 else 0, "timed_out": timed_out,
                              "start_ms": int(start * 1000), "end_ms": int(end * 1000),
                              "duration_ms": int((end - start) * 1000)})


def terminate(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return

    def force():
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    asyncio.get_running_loop().call_later(1.5, force)


def replace_previous_agent():
    pid_file = os.path.join(HOME, "agent.pid")
    try:
        with open(pid_file) as f:
            old = int(f.read().strip())
        with open("/proc/%d/cmdline" % old, "rb") as f:
            if b"newal-agent" in f.read() and old != os.getpid():
                os.kill(old, signal.SIGTERM)
                for _ in range(50):
                    time.sleep(0.02)
                    os.kill(old, 0)
                os.kill(old, signal.SIGKILL)
                time.sleep(0.05)
    except (OSError, ValueError):
        pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))


async def main():
    os.makedirs(ROOT, exist_ok=True)
    replace_previous_agent()

    async def on_client(reader, writer):
        sock = writer.get_extra_info("socket")
        if sock is not None:
            import socket
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        await Connection(reader, writer).serve()

    server = await asyncio.start_server(on_client, "127.0.0.1", PORT, reuse_address=True)
    log("listening on %d pid=%d" % (PORT, os.getpid()))
    async with server:
        await server.serve_forever()


if __name__ == "__main__" or "newal-agent" in sys.argv:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
