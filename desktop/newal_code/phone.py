"""The phone as a tool: NewAl Code Lite on Android (or NewAl Code in Termux beside it) controls the phone it runs on.

The app runs a small local server (NEWAL_PHONE_URL, with the app's key) that does what only an Android app may do:
open apps, links and settings pages, set alarms and timers, the torch and the volume, post notifications, read and
set the clipboard, share, prepare a message or a call (the user sends it), and, once NewAl Code's accessibility
service is turned on (Android's Settings > Accessibility), read what is on the screen and tap, type, swipe and press
back or home there. Looking (the screen, the apps, the battery) never asks; everything else asks first unless the
thread runs in full-auto (see permissions.decide)."""

import json
import os
import urllib.error
import urllib.request

from . import settings

LOOK = ("screen", "apps", "battery", "device")
ACTIONS = ("screen", "tap", "type", "swipe", "scroll", "key", "open_app", "open_url", "apps", "alarm", "timer",
           "torch", "volume", "battery", "device", "clipboard", "notify", "share", "sms", "call", "settings",
           "intent", "wait")


class PhoneError(RuntimeError):
    pass


def config():
    """(the app's phone server, its key): from the app's environment (NewAl Code Lite starts NewAl Code with them),
    or from phone.json that the Termux setup writes (NewAl Code in Termux)."""
    url = os.environ.get("NEWAL_PHONE_URL") or ""
    key = os.environ.get("NEWAL_PHONE_KEY") or os.environ.get("NEWAL_SERVER_KEY") or ""
    if not url:
        try:
            with open(os.path.join(settings.HOME, "phone.json"), encoding="utf-8") as f:
                d = json.load(f)
            url, key = d.get("url") or "", d.get("key") or ""
        except (OSError, ValueError):
            pass
    return url.rstrip("/"), key


def available():
    url, key = config()
    return bool(url and key)


def looks_only(args):
    action = str((args or {}).get("action") or "")
    return action in LOOK or (action == "clipboard" and not (args or {}).get("text"))


def call(action, timeout=90, **args):
    """Asks the app to do one action; returns its answer ({"ok", "text", ...})."""
    url, key = config()
    if not url:
        raise PhoneError("no phone here: this runs outside NewAl Code Lite (and its Termux link)")
    body = json.dumps(dict(args, action=action)).encode("utf-8")
    req = urllib.request.Request(url + "/phone", body, {"Content-Type": "application/json", "X-NewAl-Key": key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 127.0.0.1: never a proxy
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read() or b"{}")
        except ValueError:
            d = {}
        raise PhoneError(d.get("error") or "the phone app answered HTTP %d" % e.code)
    except OSError as e:
        raise PhoneError("the phone app does not answer (%s): is NewAl Code Lite open?" % e)


# Short on purpose: a phone's small model reads this description at the start of every thread.
DOC = ("Use the Android phone this runs on: open apps and links, alarms, settings, and what is on the screen (screen "
       "lists it as numbered items; it needs NewAl Code's accessibility service). After an action that changes the "
       "screen, call screen to see it. Not for files or code: write files with write, run programs with bash.")

# Added to a failed phone action: a small model that took "phone" in a request for the tool (a coding task that
# mentions the phone) finds its way back to the file tools instead of repeating the same failure.
WRONG_TOOL = ("If the task is a file or a program (code to save or run), the phone tool is the wrong one: write the "
              "file with the write tool and run it with bash.")

PARAMS = {
    "action": {"type": "string", "enum": list(ACTIONS), "description": (
        "tap (item from the last screen, text on it, or x,y); type (text); swipe, scroll (direction); key (name: back, "
        "home, recents, notifications, quick_settings, lock, screenshot); open_app (name); open_url (url); alarm "
        "(hour, minute, label); timer (seconds); torch (on); volume (level 0-100); clipboard (text sets it); notify "
        "(title, text); share (text); sms, call (number, text: the user sends); settings (page: wifi, bluetooth, "
        "internet, display, sound, battery, apps, location, accessibility, notifications, storage, date); intent "
        "({action, data, type, package, extras}); wait (seconds)")},
    "item": {"type": "integer"},
    "text": {"type": "string"},
    "x": {"type": "integer"},
    "y": {"type": "integer"},
    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
    "name": {"type": "string"},
    "url": {"type": "string"},
    "hour": {"type": "integer"},
    "minute": {"type": "integer"},
    "seconds": {"type": "integer"},
    "label": {"type": "string"},
    "on": {"type": "boolean"},
    "level": {"type": "integer"},
    "number": {"type": "string"},
    "title": {"type": "string"},
    "page": {"type": "string"},
    "intent": {"type": "object"},
}
