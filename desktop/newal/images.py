"""🎨 Pictures from a description, on this computer: stable-diffusion.cpp (sd-cli) with SD-Turbo, which draws a 512x512
picture in one step (under a minute on a laptop CPU). SD-Turbo reads English only: the brain writes the English
description (its tool argument), or the user's words are translated first."""

import os
import re
import time

from . import catalog, config, connectors

OUT = os.path.join(config.WORKSPACE, "images")


def tool():
    return config.find_tool("sd-cli") or config.find_tool("sd")


def ready():
    return bool(tool()) and catalog.available("image")


def generate(prompt, width=512, height=512, steps=1, seed=-1, negative=""):
    """Draws `prompt` (English). Returns the PNG path; raises with the reason."""
    exe = tool()
    if not exe:
        raise RuntimeError("محرك الصور (stable-diffusion.cpp) غير موجود بهالنسخة")
    if not catalog.available("image"):
        raise RuntimeError("نزّل «🎨 الصور» من النماذج أولاً")
    prompt = re.sub(r"\s+", " ", prompt or "").strip()
    if not prompt:
        raise ValueError("شو بدك ترسم؟")
    # sizes SD-Turbo handles well: multiples of 64 between 256 and 768
    width = max(256, min(768, int(width) // 64 * 64))
    height = max(256, min(768, int(height) // 64 * 64))
    steps = max(1, min(8, int(steps)))
    os.makedirs(OUT, exist_ok=True)
    words = "-".join(re.findall(r"[A-Za-z0-9]+", prompt)[:5]).lower() or "image"
    out = os.path.join(OUT, "%s-%s.png" % (time.strftime("%Y%m%d-%H%M%S"), words[:40]))
    args = [exe, "-m", catalog.path("image"), "-p", prompt, "-o", out, "-W", str(width), "-H", str(height),
            "--steps", str(steps), "--cfg-scale", "1.0", "--sampling-method", "euler_a",
            "-t", str(config.threads()), "-s", str(seed)]
    if negative:
        args += ["-n", negative]
    started = time.time()
    code, log = connectors.run(args, cwd=os.path.dirname(exe), timeout=900)
    if code != 0 or not os.path.exists(out):
        raise RuntimeError("ما قدرت ارسم الصورة (%s):\n%s" % (code, log.strip()[-600:]))
    return {"path": out, "seconds": round(time.time() - started, 1), "prompt": prompt, "width": width,
            "height": height, "steps": steps}
