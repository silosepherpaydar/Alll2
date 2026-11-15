---
name: youtube-one-prompt
description: >
  Build a YouTube video from one prompt the way the Higgsfield connector demo
  does: Persian or user-language script, Seedance 2 B-roll, GPT Image 2
  thumbnail, files saved in the project, Virality Predictor, title, description,
  and tags. Also finish the parts that demo still left to a person: voiceover,
  assembly, and subtitles. Use when the user asks to make, generate, or produce
  a YouTube video, B-roll, thumbnail, or the same workflow as the Higgsfield
  Claude video, including Persian requests like ویدیو بساز, ویدیو یوتیوب,
  بی‌رول, تامنیل, با یک پرامپت, or Higgsfield.
---

# YouTube video from one prompt

Cursor is the brain. Higgsfield is the hands. One user message is enough. Do not ask them to write shot prompts, pick models, or click through each generation.

Reply in the user's language.

## What "done" means

Two products. Pick from the message. Do not ask which one if the message already implies it.

**Finished video** (default). They said to make the video, or that Cursor should do the work. Deliver a watchable 16:9 file plus thumbnail, script, titles, description, tags, and a virality note.

**On-camera package.** They said they will talk, film, or record themselves. Deliver the script in spoken segments, 4 to 6 B-roll inserts, a thumbnail, a shot list that says which spoken line each clip covers, and the YouTube title, description, and tags. Do not invent a presenter and do not generate a voice that pretends to be them.

## Defaults, taken from the demo

Use these unless the user overrides them.

- Spoken language: the language of the request. Persian if they wrote Persian.
- Frame: 16:9, 720p. Six B-roll clips, 5 seconds each. That is about 30 seconds of picture.
- B-roll model: Seedance 2.0, job type `seedance_2_0`. This is the model the demo named. If `higgsfield model get seedance_2_0 --json` says it is gone, use `seedance_2_5` and say so in one sentence.
- B-roll audio: off (`--generate_audio false`) so the only voice is the generated narration or the user's own recording. If the flag is rejected, strip the audio with ffmpeg.
- Thumbnail model: GPT Image 2, job type `gpt_image_2`, 16:9, quality high, resolution 2k. If that id is missing, use `gpt_image_2_5` or `gpt_image_2_6` and say so.
- Thumbnail: one image, with a truthful 2 to 4 word headline in the video's language baked into the picture. If they attached a face photo, lock that identity. Never invent their face, and never put a real person's face on the thumbnail without a photo they supplied.
- Virality Predictor: technical job `brain_activity`, customer name Virality Predictor. Run it on the first 15 seconds after a watchable file exists.
- Length: if they name a duration, one 5-second clip per 5 seconds, hard cap 12 clips. Above 12, say the credit jump in one line and still build the 12-clip cut unless they already named a higher cap.

A named model stays in force for the rest of the job.

## Before any paid generation

1. Prefer the connected Higgsfield MCP tools (`balance`, `models_explore`, `generate_video`, `generate_image`, `generate_audio` or the voice tool the server lists, `virality_predictor` or `brain_activity`, `job_status`). The MCP server is `https://mcp.higgsfield.ai/mcp`.
2. If those tools are not connected, use the CLI. Install only when `higgsfield` is missing:

```bash
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh
```

3. If `higgsfield account status` says not authenticated or the MCP connection has no tools, stop. Tell them the single sign-in and do not generate stand-in media. Desktop Cursor: Customize, Marketplace, Higgsfield, Add, then sign in, and on the first approval choose always allow. CLI: they run `higgsfield auth login` and finish the browser step. A paid Higgsfield subscription is required. Credits are charged for every generation.
4. Read the live schema once with `higgsfield model get <id> --json` (or MCP `models_explore`) before the first submit. Pass only flags that schema still accepts.
5. Create `videos/<slug>/` and keep every file there. Slug from the topic, lowercase ASCII.

Then start. Do not pause for a separate confirmation when they already asked to build the video. One short line is enough: how many clips, which models, that Higgsfield credits will be spent.

## Write the plan first, on disk

Write `videos/<slug>/script.md` before generating media.

- Hook in the first sentence. Short spoken lines, not an essay.
- For a finished video, the words must fit the picture length. About two spoken words per second.
- For an on-camera package, write the full spoken script in segments, then mark the 4 to 6 lines a B-roll will cover.
- Each B-roll is one continuous shot, no cuts inside the clip, no readable text, no logos, no watermark. Open on a visual metaphor for the idea, the way the demo opened on glowing hands assembling film frames. Later clips show the subject itself: a place, an object, a process, files landing in a folder, a studio working. Concrete camera, light, and motion in every prompt.
- Shot list in `videos/<slug>/shots.md`: filename, the spoken line it covers, and the exact generation prompt.

Also write `videos/<slug>/youtube.md` with three title options, one description, and tags. The titles must be true to the video. Do not promise a result the pictures do not show.

## Generate

Submit one job per clip. Do not invent durations or aspect ratios the model schema rejected.

CLI shape, after the schema check:

```bash
higgsfield generate create seedance_2_0 \
  --prompt "<shot prompt>" \
  --aspect_ratio 16:9 \
  --duration 5 \
  --resolution 720p \
  --generate_audio false \
  --wait --json
```

Thumbnail, face photo first when they supplied one:

```bash
higgsfield generate create gpt_image_2 \
  --prompt "<thumbnail prompt>" \
  --image ./face.jpg \
  --aspect_ratio 16:9 \
  --quality high \
  --resolution 2k \
  --wait --json
```

Thumbnail prompt order: bold YouTube thumbnail, one subject, the exact headline in quotes, identity lock when a photo is attached (same face, no beautify), vivid light, readable at a small size. No extra text and no watermark.

Voice, finished video only. List voices first. Never invent a voice id.

```bash
higgsfield voices list --json
higgsfield generate create seed_audio \
  --prompt "<full narration>" \
  --voice_type preset \
  --voice_id "<id from the list>" \
  --format mp3 \
  --wait --json
```

`voice_type` and `voice_id` go together. Reuse the same pair for every line. If the narration is longer than the model allows, split on sentence boundaries and concatenate. If Seed Audio will not speak the language, retry once with `text2speech_v2 --variant seed_speech` and a voice id from the same list. Say which voice was used.

Download every result URL into the folder (`broll-01.mp4` …, `thumbnail.png`, `voice.mp3`). A generation that fails once gets the failure reason and one retry. Do not silently switch models.

On-camera package: stop after the files, the script, and `youtube.md`. Point at the shot list so they can drop the clips over their own recording.

## Assemble the finished video

Need `ffmpeg`. Scale every clip into one 1920x1080, 24 fps, silent picture, then add the voice. If the voice is longer than the picture, hold the last frame. Do not cut the voice off and do not speed it up.

```bash
ffmpeg -y -f concat -safe 0 -i list.txt \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p" \
  -an -c:v libx264 picture.mp4
```

Write `script.srt` from the real voice duration (`ffprobe`) and the script lines weighted by length. Burn subtitles only when a font with Persian or Arabic coverage exists, such as `NotoNaskhArabic-Regular.ttf`. Check one frame. If the letters are reversed or disconnected, discard the burn and keep the clean `video.mp4` plus the `.srt`.

```bash
ffmpeg -y -i picture.mp4 -i voice.mp3 -map 0:v -map 1:a -c:v copy -c:a aac video.mp4
```

If the voice is longer than the picture, do not use `-shortest`. Pad the picture by cloning the last frame for the extra seconds, then mux.

## Virality Predictor

Cut the first 15 seconds, or the whole file if it is shorter:

```bash
ffmpeg -y -i video.mp4 -t 15 -c copy hook.mp4
higgsfield generate create brain_activity --video ./hook.mp4 --wait
```

Tell them the overall score out of 100, where attention drops, and one concrete fix. Scores are a forecast, not a promise. If the score is under 50, rewrite and regenerate only the opening clip and the opening line, assemble again, score once more, and keep the better hook. Do not loop a third time.

## Script document

Write `script.docx` next to `script.md` with the standard-library writer in `scripts/md_to_docx.py` beside this skill:

```bash
python3 .cursor/skills/youtube-one-prompt/scripts/md_to_docx.py \
  videos/<slug>/script.md videos/<slug>/script.docx
```

## Hand back

One message, in their language:

- Path to `video.mp4` or, for the on-camera package, the folder.
- Thumbnail path.
- The three titles, the description, and the tags.
- Virality score and the single fix, if a finished file exists.
- Which voice and models were used, in one line.
- Tell them a replacement is one sentence: name the clip or the thumbnail and what to change. Regenerate only that asset.

Do not dump job ids, raw JSON, or schema internals unless they ask.
