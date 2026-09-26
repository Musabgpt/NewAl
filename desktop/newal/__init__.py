"""NewAl desktop: a local multi-model assistant."""

__version__ = "0.1.1"


def _trust_system_certificates():
    """HTTPS verification like the browser does.

    A packaged Python on Windows only sees the root certificates already in the machine store and cannot
    fetch missing ones on demand, and antivirus HTTPS scanning adds its own root: both end in
    CERTIFICATE_VERIFY_FAILED. truststore verifies with Windows itself; certifi is the fallback."""
    try:
        import truststore
        truststore.inject_into_ssl()
        return "truststore"
    except Exception:  # noqa: BLE001
        pass
    try:
        import ssl
        import urllib.request

        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.load_default_certs()
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)))
        return "certifi"
    except Exception:  # noqa: BLE001
        return "default"


TLS = _trust_system_certificates()
