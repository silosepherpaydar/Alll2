#!/usr/bin/env python3
"""Reburn correct RTL Persian subtitles and the brand card.

Pillow + Noto Naskh already shapes and orders Persian. Passing the
string through arabic_reshaper + python-bidi reverses it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from assemble import (
    AUDIO,
    CLIPS,
    FRAMES,
    OUT,
    PREP,
    PROD,
    SUBS,
    W,
    H,
    concat_videos,
    ken_burns,
    overlay_sub,
    prepare_still,
    render_subtitle_png,
)

FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"


def build_brand_card(dest: Path) -> None:
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = W / 2, H * 0.40
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    v = np.clip(1 - d / 950, 0, 1)
    arr = np.stack(
        [
            (12 + v * 70).astype(np.uint8),
            (5 + v * 32).astype(np.uint8),
            (2 + v * 10).astype(np.uint8),
        ],
        axis=-1,
    )
    img = Image.fromarray(arr, "RGB").convert("RGBA")
    rng = np.random.default_rng(7)
    bokeh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bokeh)
    for _ in range(55):
        x = int(rng.integers(30, W - 30))
        y = int(rng.integers(60, H - 120))
        rad = int(rng.integers(6, 60))
        a = int(rng.integers(20, 100))
        bd.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(255, 165, 55, a))
    img = Image.alpha_composite(img, bokeh.filter(ImageFilter.GaussianBlur(14)))

    logo = Image.open(PROD / "refs" / "logo.png").convert("RGBA")
    logo = logo.resize((480, 480), Image.Resampling.LANCZOS)
    la = np.array(logo).astype(np.float32)
    lum = la[:, :, 0] * 0.3 + la[:, :, 1] * 0.5 + la[:, :, 2] * 0.2
    ys, xs = np.ogrid[:480, :480]
    mask = np.clip(1 - ((xs - 240) ** 2 + (ys - 240) ** 2) ** 0.5 / 230, 0, 1) ** 1.2
    alpha = np.clip((lum - 18) / 40, 0, 1) * mask * 255
    la[:, :, 3] = np.minimum(la[:, :, 3], alpha)
    logo = Image.fromarray(la.astype(np.uint8), "RGBA")
    img.paste(logo, ((W - 480) // 2, int(H * 0.18)), logo)

    draw = ImageDraw.Draw(img)
    items = [
        ("سیلو سپهر", ImageFont.truetype(FONT_BOLD, 96), (255, 200, 120, 255), int(H * 0.58)),
        ("از ایده تا فنجان", ImageFont.truetype(FONT_REG, 54), (255, 214, 160, 255), int(H * 0.655)),
        ("ادامه دارد...", ImageFont.truetype(FONT_REG, 42), (230, 180, 110, 255), int(H * 0.73)),
    ]
    for text, font, color, y in items:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (W - tw) // 2
        draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), text, font=font, fill=color)
    draw.line([(W * 0.28, H * 0.635), (W * 0.72, H * 0.635)], fill=(200, 140, 60, 160), width=2)
    img.convert("RGB").save(dest, quality=95)


def main() -> None:
    timeline = json.loads((OUT / "timeline.json").read_text())
    build_brand_card(FRAMES / "brand_card.png")
    print("brand card rewritten")

    finals: list[Path] = []
    for item in timeline:
        sid = item["id"]
        dur = float(item["dur"])
        sub = item.get("sub")
        raw = CLIPS / f"{sid}_raw.mp4"
        final = CLIPS / f"{sid}.mp4"
        if sid == "brand":
            prep = PREP / "brand.jpg"
            prepare_still(FRAMES / "brand_card.png", prep)
            ken_burns(prep, raw, dur, 1.0, 1.04, "center")
            overlay_sub(raw, None, final, dur)
        elif sub:
            sp = SUBS / f"{sid}.png"
            render_subtitle_png(sub, sp)
            overlay_sub(raw, sp, final, dur)
        else:
            overlay_sub(raw, None, final, dur)
        finals.append(final)
        print("ok", sid)

    silent = OUT / "video_silent.mp4"
    concat_videos(finals, silent)
    final = OUT / "SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4"
    audio = OUT / "audio_mix.wav"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(audio),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "16",
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )
    root = PROD.parent / "SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4"
    subprocess.check_call(["cp", str(final), str(root)])
    print("DONE", final)


if __name__ == "__main__":
    main()
