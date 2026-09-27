"""Phone access: the NewAl page on a phone next to the computer, over any local link between them: the same
Wi-Fi, the phone's hotspot, the computer's hotspot or a USB cable (USB tethering). No internet is needed.

A second listener (its own port) answers the other devices; the main one stays on 127.0.0.1. Every request
from it needs the phone key: the QR code carries it once, then it lives in a cookie."""

import hmac
import io
import os
import secrets
import socket
import threading
from http.server import ThreadingHTTPServer

from . import config

_server = None
_error = ""
_lock = threading.Lock()


def key():
    if not config.get("phone_key"):
        config.update({"phone_key": secrets.token_urlsafe(18)})
    return config.get("phone_key")


def valid(candidate):
    return bool(candidate) and hmac.compare_digest(str(candidate), key())


def addresses():
    """This computer's IPv4 addresses the phone could reach, with what kind of link each one probably is."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))           # no packet is sent: this only picks the default route
            found.append(s.getsockname()[0])
    except OSError:
        pass
    out, seen = [], set()
    for ip in found:
        if ip in seen or ip.startswith(("127.", "169.254.", "0.")):
            continue
        seen.add(ip)
        out.append({"ip": ip, "kind": kind_of(ip)})
    return out


def kind_of(ip):
    if ip.startswith("192.168.137."):
        return "نقطة اتصال الكمبيوتر"
    if ip.startswith(("192.168.42.", "192.168.73.")):
        return "كابل USB"
    if ip.startswith(("192.168.43.", "172.20.10.")):
        return "نقطة اتصال الهاتف"
    return "الشبكة (واي فاي، نقطة اتصال أو كابل)"


def qr_svg(text):
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    return svg[svg.index("<svg"):]


def port():
    return int(config.get("phone_port"))


def running():
    return _server is not None


def start():
    global _server, _error
    from .server import Handler
    with _lock:
        if _server:
            return True
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port()), Handler)
        except OSError as e:
            _error = "المنفذ %d مشغول: %s" % (port(), e)
            return False
        httpd.phone = True
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        _server, _error = httpd, ""
        return True


def stop():
    global _server
    with _lock:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None


def configure(enabled, new_key=False):
    if new_key:
        config.update({"phone_key": secrets.token_urlsafe(18)})
    config.update({"phone_access": enabled})
    if enabled:
        start()
    else:
        stop()
    return status()


def status():
    urls = []
    if running():
        for a in addresses():
            url = "http://%s:%d/?k=%s" % (a["ip"], port(), key())
            urls.append(dict(a, url=url, svg=qr_svg(url)))
    return {"enabled": bool(config.get("phone_access")), "running": running(), "port": port(), "urls": urls,
            "error": _error, "windows": os.name == "nt"}
