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


DOC = ("Use the Android phone this runs on. action is one of: "
       "screen (what is on the screen now: numbered items; needs NewAl Code's accessibility service), "
       "tap (item: a number from the last screen, or text: words on it, or x and y), "
       "type (text into the focused field, or into item), swipe or scroll (direction: up, down, left, right), "
       "key (name: back, home, recents, notifications, quick_settings, lock, screenshot), "
       "open_app (name), open_url (url), apps (the installed apps), "
       "alarm (hour, minute, label), timer (seconds, label), torch (on: true/false), volume (level 0-100), "
       "battery, device, clipboard (text sets it; without text, reads it), notify (title, text), share (text), "
       "sms (number, text: the message opens ready, the user sends it), call (number: the dialer opens), "
       "settings (page: wifi, bluetooth, internet, display, sound, battery, apps, location, accessibility, "
       "notifications, storage, date), intent (intent: {action, data, type, package, extras}), "
       "wait (seconds: let the screen settle). After an action that changes the screen, call screen to see it.")

PARAMS = {
    "action": {"type": "string", "enum": list(ACTIONS), "description": "what to do"},
    "item": {"type": "integer", "description": "tap/type: an item number from the last screen"},
    "text": {"type": "string", "description": "tap: words on the screen; type, clipboard, notify, share, sms: the text"},
    "x": {"type": "integer", "description": "tap: x in pixels"},
    "y": {"type": "integer", "description": "tap: y in pixels"},
    "direction": {"type": "string", "description": "swipe/scroll: up, down, left or right"},
    "name": {"type": "string", "description": "open_app: the app's name or package; key: which key"},
    "url": {"type": "string", "description": "open_url: the address"},
    "hour": {"type": "integer", "description": "alarm: 0-23"},
    "minute": {"type": "integer", "description": "alarm: 0-59"},
    "seconds": {"type": "integer", "description": "timer, wait: how long"},
    "label": {"type": "string", "description": "alarm, timer: its label"},
    "on": {"type": "boolean", "description": "torch: on or off"},
    "level": {"type": "integer", "description": "volume: 0-100"},
    "number": {"type": "string", "description": "sms, call: the phone number"},
    "title": {"type": "string", "description": "notify: the title"},
    "page": {"type": "string", "description": "settings: which page"},
    "intent": {"type": "object", "description": "intent: {action, data, type, package, extras}"},
}
