"""Speed: the brain is loaded and has read the fixed start of every conversation before the first question, and
what each model call cost is kept for the ⚡ report."""

import os
import threading
import time

from . import catalog, config
from .engine import pool

_state = {"state": "", "role": "", "message": "", "seconds": 0.0, "at": 0.0}
_lock = threading.Lock()


def state():
    return dict(_state)


def _set(**kw):
    _state.update(kw, at=time.time())


def brain():
    """The role that answers most requests: the brain in one-brain mode, else the everyday chat model."""
    if config.get("one_brain") and catalog.available("coder"):
        return "coder"
    return catalog.pick("agent") or catalog.pick("coder")


def warm_up(wait=False):
    """Loads the brain (and the small search model) and lets it read the system prompts once: llama.cpp keeps what it
    read, so the first question only costs reading the question itself. Runs in the background at start-up."""
    if not config.get("preload"):
        return
    t = threading.Thread(target=_warm, daemon=True)
    t.start()
    if wait:
        t.join()


def _warm():
    if not _lock.acquire(blocking=False):
        return                                   # already warming
    try:
        role = brain()
        if not role:
            return
        from . import agent
        started = time.time()
        _set(state="loading", role=role, message="تحميل %s…" % catalog.MODELS[role]["title"])
        pool.get(role)
        if catalog.available("embed"):
            try:
                pool.get("embed")
                from . import router
                router.warm()          # the routing examples' vectors (9 s on the first question otherwise)
            except Exception:  # noqa: BLE001 - search is a helper
                pass
        _set(state="warming", message="يقرأ التعليمات مسبقاً…")
        for prompt in agent.warm_prompts(role):
            try:
                pool.chat(role, prompt["messages"], tools=prompt.get("tools"), max_tokens=1, temperature=0,
                          extra=prompt.get("extra"))
            except Exception:  # noqa: BLE001 - warming is an optimisation, never an error
                pass
        _set(state="ready", message="جاهز", seconds=round(time.time() - started, 1))
    except Exception as e:  # noqa: BLE001
        _set(state="error", message=str(e)[:200])
    finally:
        _lock.release()


def report(n=30):
    """The last model calls: what each read, found cached and wrote, and how fast."""
    calls = list(pool.calls)[-n:]
    out = []
    for c in calls:
        out.append(dict(c, title=catalog.MODELS.get(c["role"], {}).get("title", c["role"]),
                        read_tps=round(c["prompt"] / (c["prompt_ms"] / 1000), 1) if c["prompt_ms"] else 0,
                        write_tps=round(c["generated"] / (c["gen_ms"] / 1000), 1) if c["gen_ms"] else 0))
    return out


def power():
    """Windows power: on battery or a power-saving plan the CPU runs far slower (turbo off)."""
    if not config.IS_WINDOWS:
        return {}
    info = {}
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                        ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        st = Status()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            info["on_battery"] = st.ACLineStatus == 0
            info["saver"] = bool(st.SystemStatusFlag & 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import connectors
        code, out = connectors.run(["powercfg", "/getactivescheme"], timeout=10)
        if code == 0:
            info["plan"] = out.strip().split("(")[-1].rstrip(")").strip() if "(" in out else out.strip()
            info["plan_guid"] = (out.split(":", 1)[-1].strip().split() or [""])[0]
    except Exception:  # noqa: BLE001
        pass
    return info


HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"


def set_power(high):
    """Switches the Windows power plan to High performance (or back to Balanced)."""
    if not config.IS_WINDOWS:
        return {"ok": False, "message": "ويندوز فقط"}
    from . import connectors
    code, out = connectors.run(["powercfg", "/setactive", HIGH_PERFORMANCE if high else BALANCED], timeout=15)
    if code != 0 and high:
        # Some laptops hide the plan: restore it from the built-in template first.
        connectors.run(["powercfg", "-duplicatescheme", HIGH_PERFORMANCE], timeout=15)
        code, out = connectors.run(["powercfg", "/setactive", HIGH_PERFORMANCE], timeout=15)
    return {"ok": code == 0, "message": "تم" if code == 0 else out.strip()[:200], "power": power()}


def cpu_name():
    try:
        if config.IS_WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("PROCESSOR_IDENTIFIER", "")
