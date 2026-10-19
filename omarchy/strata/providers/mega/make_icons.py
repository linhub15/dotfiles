#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Generate original high-contrast status artwork (build-time Cairo only)."""
import math
from pathlib import Path
import cairo

OUT = Path(__file__).resolve().parent / "icons"
OUT.mkdir(exist_ok=True)
for name, color in (("synced", (0.12, 0.65, 0.32)), ("syncing", (0.12, 0.48, 0.90)),
                    ("pending", (0.80, 0.48, 0.04)), ("paused", (0.80, 0.48, 0.04)),
                    ("ignored", (0.42, 0.46, 0.51)), ("error", (0.80, 0.18, 0.18)),
                    ("matched", (0.12, 0.65, 0.32)), ("different", (0.80, 0.48, 0.04)),
                    ("missing", (0.42, 0.46, 0.51)), ("unknown", (0.42, 0.46, 0.51)),
                    ("cloud-folder", (0.12, 0.48, 0.90))):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
    c = cairo.Context(surface)
    c.arc(32, 32, 29, 0, math.tau)
    c.set_source_rgb(*color)
    c.fill_preserve()
    c.set_source_rgb(1, 1, 1)
    c.set_line_width(3)
    c.stroke()
    c.set_line_width(6)
    c.set_line_cap(cairo.LINE_CAP_ROUND)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    if name in ("matched", "different"):
        c.move_to(19, 25); c.line_to(45, 25)
        c.move_to(19, 39); c.line_to(45, 39)
        if name == "different":
            c.move_to(39, 17); c.line_to(25, 47)
    elif name == "unknown":
        c.arc(32, 25, 9, math.pi, math.tau + math.pi / 2); c.stroke()
        c.move_to(32, 34); c.line_to(32, 37); c.stroke()
        c.arc(32, 46, 2.5, 0, math.tau); c.fill()
    elif name == "cloud-folder":
        c.move_to(17, 44); c.line_to(17, 21); c.line_to(28, 21)
        c.line_to(32, 26); c.line_to(47, 26); c.line_to(47, 44); c.close_path()
    elif name == "error":
        c.move_to(32, 18); c.line_to(32, 35); c.stroke()
        c.arc(32, 45, 3, 0, math.tau); c.fill()
    elif name == "synced":
        c.move_to(17, 32); c.line_to(27, 42); c.line_to(47, 22)
    elif name == "pending":
        c.arc(32, 32, 16, 0, math.tau); c.stroke()
        c.move_to(32, 21); c.line_to(32, 32); c.line_to(40, 36)
    elif name == "paused":
        c.move_to(25, 20); c.line_to(25, 44)
        c.move_to(39, 20); c.line_to(39, 44)
    elif name in ("ignored", "missing"):
        c.move_to(19, 32); c.line_to(45, 32)
    else:
        c.arc(32, 32, 16, math.pi * 1.05, math.pi * 1.9); c.stroke()
        c.move_to(47, 19); c.line_to(47, 29); c.line_to(37, 29); c.stroke()
        c.arc(32, 32, 16, math.pi * 0.05, math.pi * 0.9); c.stroke()
        c.move_to(17, 45); c.line_to(17, 35); c.line_to(27, 35)
    c.stroke()
    surface.write_to_png(str(OUT / f"{name}.png"))
