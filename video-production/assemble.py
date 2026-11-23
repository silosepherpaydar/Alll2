#!/usr/bin/env python3
"""Assemble SILO SEPHER Episode 1 vertical cinematic video."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

PROD = Path("/agent/repos/Alll2/video-production")
FRAMES = PROD / "frames"
AUDIO = PROD / "audio"
SFX = PROD / "sfx"
OUT = PROD / "out"
CLIPS = PROD / "clips"
SUBS = PROD / "subs"
PREP = PROD / "prep"
W, H = 1080, 1920
FPS = 30

for d in (OUT, CLIPS, SUBS, PREP):
    d.mkdir(exist_ok=True)


def run(cmd: list[str]) -> None:
    print("+", " ".join(str(c) for c in cmd[:12]), ("..." if len(cmd) > 12 else ""))
    subprocess.check_call(cmd)


def fa(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def prepare_still(src: Path, dest: Path) -> None:
    """Fit/cover to 1080x1920 with light grade, save as high-quality jpg."""
    im = Image.open(src).convert("RGB")
    src_w, src_h = im.size
    scale = max(W / src_w, H / src_h) * 1.15  # headroom for zoom
    nw, nh = int(src_w * scale), int(src_h * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - W) // 2
    top = (nh - H) // 2
    # Keep oversized for zoompan source: 1296x2304 (~1.2x)
    ow, oh = int(W * 1.2), int(H * 1.2)
    scale2 = max(ow / src_w, oh / src_h)
    # reopen original for clean oversized
    im = Image.open(src).convert("RGB")
    src_w, src_h = im.size
    nw, nh = int(src_w * scale2) + 2, int(src_h * scale2) + 2
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - ow) // 2
    top = (nh - oh) // 2
    im = im.crop((left, top, left + ow, top + oh))
    im = ImageEnhance.Color(im).enhance(1.08)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    im = ImageEnhance.Brightness(im).enhance(1.02)
    im.save(dest, quality=95)


def ken_burns(
    prep: Path,
    dest: Path,
    duration: float,
    zoom_start: float = 1.0,
    zoom_end: float = 1.08,
    pan: str = "center",
) -> None:
    frames = max(1, int(duration * FPS))
    # zoompan on 1296x2304 -> 1080x1920
    # z goes from zoom_start to zoom_end
    z_expr = f"min({zoom_start}+({zoom_end}-{zoom_start})*on/{frames},{zoom_end}+0.001)"
    if pan == "in":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih*0.52-(ih/zoom/2)-on*{2.0/frames}"
    elif pan == "up":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih*0.56-(ih/zoom/2)-on*{3.5/frames}"
    elif pan == "left":
        x_expr = f"iw*0.54-(iw/zoom/2)-on*{2.5/frames}"
        y_expr = "ih/2-(ih/zoom/2)"
    elif pan == "right":
        x_expr = f"iw*0.46-(iw/zoom/2)+on*{2.5/frames}"
        y_expr = "ih/2-(ih/zoom/2)"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    vf = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={W}x{H}:fps={FPS},"
        f"eq=contrast=1.03:saturation=1.05,"
        f"unsharp=5:5:0.4:5:5:0.0,"
        f"format=yuv420p"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(prep),
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "17",
            "-preset",
            "fast",
            str(dest),
        ]
    )


def render_subtitle_png(text: str, path: Path, max_width: int = 900) -> None:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", 42
    )
    # Wrap on original Persian words, then reshape each line
    words = text.split()
    raw_lines: list[str] = []
    cur: list[str] = []
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    def width_of(raw: str) -> int:
        shaped = fa(raw)
        bbox = probe.textbbox((0, 0), shaped, font=font)
        return bbox[2] - bbox[0]

    for w in words:
        trial = " ".join(cur + [w])
        if cur and width_of(trial) > max_width:
            raw_lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        raw_lines.append(" ".join(cur))

    lines = [fa(ln) for ln in raw_lines]
    line_h = 58
    pad_x, pad_y = 30, 20
    widths = []
    for ln in lines:
        bbox = probe.textbbox((0, 0), ln, font=font)
        widths.append(bbox[2] - bbox[0])
    tw = max(widths) if widths else 120
    img = Image.new("RGBA", (tw + pad_x * 2, len(lines) * line_h + pad_y * 2), (0, 0, 0, 0))
    bar = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar)
    bd.rounded_rectangle(
        (0, 0, img.size[0] - 1, img.size[1] - 1),
        radius=20,
        fill=(8, 4, 2, 155),
    )
    img = Image.alpha_composite(img, bar)
    draw = ImageDraw.Draw(img)
    y = pad_y
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        lw = bbox[2] - bbox[0]
        x = (img.size[0] - lw) // 2
        draw.text((x + 2, y + 2), ln, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), ln, font=font, fill=(255, 238, 214, 255))
        y += line_h
    img.save(path)


def overlay_sub(video: Path, sub_png: Path | None, dest: Path, duration: float) -> None:
    if sub_png is None:
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video),
                "-c:v",
                "copy",
                "-t",
                str(duration),
                str(dest),
            ]
        )
        return
    fc = f"[0:v][1:v]overlay=(W-w)/2:H-h-130:enable='between(t,0.12,{max(0.4, duration - 0.08)})'[v]"
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(sub_png),
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "17",
            "-preset",
            "fast",
            "-t",
            str(duration),
            str(dest),
        ]
    )


def concat_videos(paths: list[Path], dest: Path) -> None:
    lst = OUT / "concat.txt"
    with lst.open("w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "17",
            "-preset",
            "fast",
            str(dest),
        ]
    )


def mix_audio(vo_items: list[dict], total: float, dest: Path) -> None:
    inputs = ["-i", str(AUDIO / "music_bed.wav")]
    filters = [
        f"[0:a]volume=0.20,afade=t=in:st=0:d=1.2,afade=t=out:st={max(0.5, total - 2.8)}:d=2.6[music]"
    ]
    labels = ["[music]"]
    idx = 1
    for item in vo_items:
        inputs += ["-i", str(item["path"])]
        delay_ms = int(item["start"] * 1000)
        vol = item.get("volume", 1.0)
        lab = f"a{idx}"
        filters.append(
            f"[{idx}:a]volume={vol},adelay={delay_ms}|{delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=mono[{lab}]"
        )
        labels.append(f"[{lab}]")
        idx += 1

    sfx_items = [
        (SFX / "whoosh.wav", 8.4, 0.22),
        (SFX / "whoosh.wav", 20.3, 0.2),
        (SFX / "whoosh.wav", 35.0, 0.18),
        (SFX / "beans_pour.wav", 54.0, 0.4),
        (SFX / "whoosh.wav", 57.2, 0.28),
    ]
    for path, start, vol in sfx_items:
        inputs += ["-i", str(path)]
        delay_ms = int(start * 1000)
        lab = f"a{idx}"
        filters.append(
            f"[{idx}:a]volume={vol},adelay={delay_ms}|{delay_ms},"
            f"aformat=sample_rates=44100:channel_layouts=mono[{lab}]"
        )
        labels.append(f"[{lab}]")
        idx += 1

    n = len(labels)
    filters.append(
        "".join(labels)
        + f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[aout]"
    )
    fc = ";".join(filters)
    run(
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex",
            fc,
            "-map",
            "[aout]",
            "-t",
            str(total),
            "-ar",
            "44100",
            "-ac",
            "2",
            str(dest),
        ]
    )


def main() -> None:
    shots = [
        # id, image, dur, zoom_s, zoom_e, pan, subtitle
        ("s1a", "s1-wide-meadow.jpg", 4.3, 1.0, 1.07, "in", "گاهی یک اتفاق بزرگ، از یک روز کاملاً معمولی شروع می‌شه..."),
        ("s1b", "s1-wide-closer.jpg", 4.3, 1.0, 1.06, "up", "از یک گفت‌وگوی ساده بین سه دوست قدیمی."),
        ("s2a", "s2-arash-speaking.jpg", 3.2, 1.0, 1.05, "center", "من همیشه به دنیای قهوه علاقه داشتم..."),
        ("s2b", "s2-beans-cutaway.jpg", 1.8, 1.0, 1.12, "in", None),
        ("s2c", "s2-arash-speaking.jpg", 2.4, 1.02, 1.07, "right", "به دستگاه‌ها، آسیاب‌ها، دانه قهوه"),
        ("s2d", "s2-espresso-cutaway.jpg", 1.6, 1.0, 1.1, "in", None),
        ("s2e", "s2-grinder-cutaway.jpg", 1.5, 1.0, 1.1, "in", None),
        ("s2f", "s2-arash-speaking.jpg", 1.4, 1.04, 1.08, "center", "و همه چیزهایی که پشت یک فنجون قهوه خوبه."),
        ("s3a", "s3-shahab-speaking.jpg", 6.3, 1.0, 1.05, "center", "خب، اگه قراره وارد این کار بشیم، شما تأمین و آماده‌سازی رو انجام بدین؛ فروشش با من."),
        ("s3b", "s3-mahyar-speaking.jpg", 8.4, 1.0, 1.05, "center", "تو از فردا شروع کن و اطلاعات جمع کن. سرمایه و امکانات اولیه رو من فراهم می‌کنم."),
        ("s4a", "s4-arash-thinking.jpg", 3.6, 1.0, 1.07, "in", "شاید این حرف‌ها می‌تونست مثل هزاران گفت‌وگوی دیگه، همون‌جا تموم بشه..."),
        ("s4b", "s4-mahyar-horizon.jpg", 3.6, 1.0, 1.06, "up", None),
        ("s4c", "s4-shahab-smile.jpg", 3.6, 1.0, 1.05, "center", "اما این بار، قرار نبود فقط یک رؤیا باقی بمونه."),
        ("s5a", "s5-arash-research.jpg", 2.8, 1.0, 1.07, "in", "از فردای اون روز، آرش شروع کرد."),
        ("s5b", "s5-mahyar-planning.jpg", 2.8, 1.0, 1.06, "center", "مهیار مسئولیت سرمایه و امکانات رو پذیرفت"),
        ("s5c", "s5-shahab-sales.jpg", 2.8, 1.0, 1.06, "right", "و شهاب آماده فروش شد."),
        ("s5d", "s5-beans-bag.jpg", 3.1, 1.0, 1.12, "in", "این‌طوری، مسیر سیلو سپهر آغاز شد."),
        ("brand", "brand_card.png", 3.5, 1.0, 1.04, "center", None),
    ]

    clip_paths: list[Path] = []
    t = 0.0
    meta = []
    for sid, img_name, dur, zs, ze, pan, sub in shots:
        src = FRAMES / img_name
        prep = PREP / f"{sid}.jpg"
        raw = CLIPS / f"{sid}_raw.mp4"
        final = CLIPS / f"{sid}.mp4"
        print(f"== {sid} {dur}s ==")
        prepare_still(src, prep)
        ken_burns(prep, raw, dur, zs, ze, pan)
        if sub:
            sp = SUBS / f"{sid}.png"
            render_subtitle_png(sub, sp)
            overlay_sub(raw, sp, final, dur)
        else:
            overlay_sub(raw, None, final, dur)
        clip_paths.append(final)
        meta.append({"id": sid, "start": round(t, 3), "dur": dur, "sub": sub})
        t += dur

    total = t
    print("TOTAL", total)
    (OUT / "timeline.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    video_silent = OUT / "video_silent.mp4"
    concat_videos(clip_paths, video_silent)

    # Audio cue points from cumulative durations
    def cue_after(n: int) -> float:
        return sum(s[2] for s in shots[:n])

    s1, s2, s3, s3b, s4, s5 = (
        0.0,
        cue_after(2),
        cue_after(8),
        cue_after(9),
        cue_after(10),
        cue_after(13),
    )
    print("Audio cues", s1, s2, s3, s3b, s4, s5)

    audio_mix = OUT / "audio_mix.wav"
    mix_audio(
        [
            {"path": AUDIO / "narr_01.wav", "start": s1 + 0.12, "volume": 1.2},
            {"path": AUDIO / "arash_01.wav", "start": s2 + 0.08, "volume": 1.25},
            {"path": AUDIO / "shahab_01.wav", "start": s3 + 0.12, "volume": 1.25},
            {"path": AUDIO / "mahyar_01.wav", "start": s3b + 0.08, "volume": 1.25},
            {"path": AUDIO / "narr_02.wav", "start": s4 + 0.12, "volume": 1.2},
            {"path": AUDIO / "narr_03.wav", "start": s5 + 0.08, "volume": 1.2},
        ],
        total,
        audio_mix,
    )

    final = OUT / "SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_silent),
            "-i",
            str(audio_mix),
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
    # also copy deliverable to repo root out
    deliver = PROD / "SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4"
    run(["cp", str(final), str(deliver)])
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=width,height,codec_name",
            "-of",
            "json",
            str(final),
        ],
        text=True,
    )
    print(probe)
    print("DONE", final)


if __name__ == "__main__":
    main()
