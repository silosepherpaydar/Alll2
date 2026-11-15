#!/usr/bin/env python3
"""Build a narrated video from plan.json with free tools only.

Pictures come from the public Pollinations image endpoint (no key).
Speech comes from the free Microsoft neural voices via edge-tts.
ffmpeg moves the camera across each still and joins the clips.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IMAGE_ENDPOINT = "https://image.pollinations.ai/prompt/"
FRAME_W, FRAME_H = 1280, 720
FPS = 24
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def ensure_packages() -> None:
    missing = []
    for module, package in (
        ("edge_tts", "edge-tts"),
        ("PIL", "pillow"),
        ("arabic_reshaper", "arabic-reshaper"),
        ("bidi", "python-bidi"),
    ):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)
    if missing:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])


def font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("No usable font found for captions.")


def has_arabic(text: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in text)


def layout_kwargs(text: str, font, anchor: str) -> dict:
    options = {"font": font, "anchor": anchor}
    if has_arabic(text):
        options["direction"] = "rtl"
        options["language"] = "fa"
    return options


def text_size(draw, text: str, font) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, **layout_kwargs(text, font, "lt"))
    return box[2] - box[0], box[3] - box[1]


def run(cmd: list[str]) -> None:
    if cmd and cmd[0] == "ffmpeg":
        cmd = [cmd[0], "-hide_banner", "-loglevel", "error", *cmd[1:]]
    subprocess.run(cmd, check=True)


def needs_file(path: Path) -> bool:
    return (not path.exists()) or path.stat().st_size == 0


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def fetch_image(prompt: str, dest: Path, seed: int) -> None:
    query = urllib.parse.urlencode(
        {"width": FRAME_W, "height": FRAME_H, "seed": seed, "nofeed": "true"}
    )
    url = IMAGE_ENDPOINT + urllib.parse.quote(prompt, safe="") + "?" + query
    last_error = "no response"
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cursor-free-video/1.0"})
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
            if body[:2] == b"\xff\xd8" or body[:8] == b"\x89PNG\r\n\x1a\n":
                dest.write_bytes(body)
                return
            last_error = f"not an image ({content_type}, {len(body)} bytes)"
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code in (402, 429, 503):
                wait = int(error.headers.get("Retry-After") or "20")
                print(f"free image slot busy, waiting {wait}s", flush=True)
                time.sleep(wait + 1)
                continue
        except urllib.error.URLError as error:
            last_error = str(error.reason)
        time.sleep(5 + attempt * 3)
    raise SystemExit(f"Could not download a picture for: {prompt}\n{last_error}")


def speak(text: str, dest: Path, voice: str) -> None:
    import edge_tts

    async def _save() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(dest))

    asyncio.run(_save())


def wrap_lines(text: str, draw, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if text_size(draw, trial, font)[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_caption(text: str, dest: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path(), 40)
    lines = wrap_lines(text, draw, font, FRAME_W - 180)
    heights = [text_size(draw, line, font)[1] for line in lines]
    block_h = sum(heights) + 10 * (len(lines) - 1)
    top = FRAME_H - block_h - 72
    draw.rounded_rectangle(
        (64, top - 16, FRAME_W - 64, top + block_h + 16),
        radius=18,
        fill=(8, 8, 10, 168),
    )
    y = top
    for line, height in zip(lines, heights):
        draw.text((FRAME_W / 2, y), line, fill=(255, 255, 255, 255), **layout_kwargs(line, font, "ma"))
        y += height + 10
    image.save(dest)


def find_portrait(project: Path, plan: dict) -> Path | None:
    named = plan.get("portrait")
    candidates = []
    if named:
        path = Path(named)
        candidates.append(path if path.is_absolute() else project / path)
    candidates.extend(
        [
            Path.cwd() / "me" / "portrait.jpg",
            project.parent.parent / "me" / "portrait.jpg",
            project / "portrait.jpg",
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def rounded_portrait(path: Path, box_w: int, box_h: int):
    from PIL import Image, ImageDraw, ImageOps

    source = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    scale = max(box_w / source.width, box_h / source.height)
    resized = source.resize(
        (max(box_w, int(source.width * scale)), max(box_h, int(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - box_w) // 2)
    top = max(0, (resized.height - box_h) // 2 - int(resized.height * 0.04))
    if top + box_h > resized.height:
        top = resized.height - box_h
    cropped = resized.crop((left, top, left + box_w, top + box_h))
    mask = Image.new("L", (box_w, box_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((1, 1, box_w - 2, box_h - 2), radius=40, fill=255)
    portrait = cropped.convert("RGBA")
    portrait.putalpha(mask)
    return portrait


def render_thumbnail(background: Path, headline: str, dest: Path, portrait: Path | None = None) -> None:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont

    image = Image.open(background).convert("RGB")
    image = ImageEnhance.Color(image).enhance(1.15)
    image = image.resize((FRAME_W, FRAME_H))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    text_right = FRAME_W - 70
    if portrait is not None:
        box_w, box_h = 470, 600
        face = rounded_portrait(portrait, box_w, box_h)
        face_x = 56
        face_y = (FRAME_H - box_h) // 2
        overlay.paste(face, (face_x, face_y), face)
        draw.rounded_rectangle(
            (face_x - 6, face_y - 6, face_x + box_w + 6, face_y + box_h + 6),
            radius=46,
            outline=(255, 246, 220, 230),
            width=6,
        )
        text_left = face_x + box_w + 48
    else:
        text_left = 70
    font = ImageFont.truetype(font_path(), 72 if portrait is None else 64)
    lines = wrap_lines(headline, draw, font, text_right - text_left)
    heights = [text_size(draw, line, font)[1] for line in lines]
    widths = [text_size(draw, line, font)[0] for line in lines]
    block_h = sum(heights) + 12 * (len(lines) - 1)
    y = (FRAME_H - block_h) / 2
    widest = max(widths)
    if portrait is None:
        band_left = (FRAME_W - widest) / 2 - 32
        band_right = (FRAME_W + widest) / 2 + 32
        anchor_x = FRAME_W / 2
        anchor = "ma"
    else:
        band_right = text_right + 8
        band_left = band_right - widest - 64
        anchor_x = text_right
        anchor = "ra"
    draw.rounded_rectangle(
        (band_left, y - 22, band_right, y + block_h + 26),
        radius=22,
        fill=(8, 8, 10, 168),
    )
    for line, height in zip(lines, heights):
        draw.text(
            (anchor_x, y),
            line,
            fill=(255, 246, 220, 255),
            **layout_kwargs(line, font, anchor),
        )
        y += height + 12
    image.paste(overlay, mask=overlay.split()[-1])
    image.save(dest, quality=92)


def make_clip(image: Path, audio: Path, caption: Path, dest: Path, zoom_in: bool) -> None:
    frames = max(int(probe_duration(audio) * FPS), FPS)
    if zoom_in:
        zoom = "min(1+0.00055*on,1.06)"
        drift = "iw/2-(iw/zoom/2)+on*0.12"
    else:
        zoom = "max(1.06-0.00055*on,1)"
        drift = "iw/2-(iw/zoom/2)-on*0.12"
    video_filter = (
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"zoompan=z='{zoom}':x='{drift}':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={FRAME_W}x{FRAME_H}:fps={FPS},"
        "eq=contrast=1.05:saturation=0.9:gamma=0.98,"
        "vignette=PI/5,format=yuv420p[bg];"
        "[bg][1:v]overlay=0:0:format=auto"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image),
            "-loop",
            "1",
            "-i",
            str(caption),
            "-i",
            str(audio),
            "-filter_complex",
            video_filter,
            "-map",
            "2:a",
            "-t",
            f"{frames / FPS:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
    )


def srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_srt(cues: list[tuple[float, float, str]], dest: Path) -> None:
    blocks = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    dest.write_text("\n".join(blocks), encoding="utf-8")


def build(project: Path) -> None:
    ensure_packages()
    plan = json.loads((project / "plan.json").read_text(encoding="utf-8"))
    shots = plan["shots"]
    if not shots:
        raise SystemExit("plan.json needs at least one shot.")
    voice = plan.get("voice") or (
        "fa-IR-DilaraNeural" if has_arabic(" ".join(shot["line"] for shot in shots)) else "en-US-AriaNeural"
    )
    pictures = project / "pictures"
    audio_dir = project / "audio"
    clips = project / "clips"
    for folder in (pictures, audio_dir, clips):
        folder.mkdir(parents=True, exist_ok=True)

    cues: list[tuple[float, float, str]] = []
    cursor = 0.0
    clip_paths: list[Path] = []
    for index, shot in enumerate(shots, start=1):
        stem = f"{index:02}"
        picture = pictures / f"{stem}.jpg"
        audio = audio_dir / f"{stem}.mp3"
        caption = clips / f"{stem}-caption.png"
        clip = clips / f"{stem}.mp4"
        print(f"picture {index}/{len(shots)}", flush=True)
        if needs_file(picture):
            fetch_image(shot["prompt"], picture, seed=1000 + index)
        print(f"voice {index}/{len(shots)}", flush=True)
        if needs_file(audio):
            speak(shot["line"], audio, voice)
        render_caption(shot["line"], caption)
        if not clip.exists():
            make_clip(picture, audio, caption, clip, zoom_in=index % 2 == 1)
        length = probe_duration(clip)
        cues.append((cursor, cursor + length, shot["line"]))
        cursor += length
        clip_paths.append(clip)

    concat_list = project / "clips.txt"
    concat_list.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in clip_paths),
        encoding="utf-8",
    )
    video = project / "video.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(video),
        ]
    )
    thumb_src = pictures / "thumb-src.jpg"
    if not thumb_src.exists():
        fetch_image(plan["thumbnail_prompt"], thumb_src, seed=7)
    render_thumbnail(
        thumb_src,
        plan["headline"],
        project / "thumbnail.jpg",
        find_portrait(project, plan),
    )
    script = project / "script.md"
    script.write_text(
        "# " + plan["headline"] + "\n\n" + "\n\n".join(f"- {shot['line']}" for shot in shots) + "\n",
        encoding="utf-8",
    )
    write_srt(cues, project / "script.srt")
    docx = Path(__file__).with_name("md_to_docx.py")
    subprocess.check_call([sys.executable, str(docx), str(script), str(project / "script.docx")])
    print(f"ready {video}", flush=True)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_free_video.py PROJECT_DIR")
    build(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
