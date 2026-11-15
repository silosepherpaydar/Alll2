---
name: youtube-one-prompt
description: >
  Make a finished YouTube-style video from one prompt for free, with no paid
  video account. Write the script, generate stills, Persian or other voiceover,
  camera moves, captions, a thumbnail, titles, and a description, then save the
  file in the project. Use when the user asks to make a YouTube video, B-roll,
  thumbnail, or the same kind of video as a one-prompt AI demo, including
  Persian requests like ویدیو بساز, ویدیو یوتیوب, رایگان, بی‌رول, or تامنیل.
---

# Free YouTube video from one prompt

The user already has Cursor. Do not send them to a paid generator, and do not ask them to create an account. One message is the whole brief.

Reply in the user's language.

Pictures come from the public Pollinations endpoint. A small Pollinations mark stays on the picture. Speech is a free neural voice. The camera slowly pushes in or pulls out across each still, then the clips are cut together. Say that plainly when you deliver the file. This is a finished narrated video, not footage from a paid video model.

## Done

A folder `videos/<slug>/` containing `video.mp4`, `thumbnail.jpg`, `script.md`, `script.docx`, `script.srt`, and `youtube.md`.

Default: four shots, 16:9. If they name a count, use that, with a cap of six. More than six stills makes the free image slot wait a long time.

If they say they will film or record themselves, still build the silent pictures and the script, and skip the generated voice. Set no `voice` job: write the shot list and stop before `build_free_video.py`.

Leave people out of the generated pictures. If `me/portrait.jpg` exists, or `plan.json` has a `portrait` path, the thumbnail places that real photograph on the frame. Never upload that photo to Pollinations and never ask an image model to redraw the face.

## Steps

1. Write the spoken lines in their language. Short lines, about one breath each. The first line is the hook.
2. Write one English picture prompt per line. One subject, concrete light and place, no letters, no logos, no watermark text. The picture has to be something a single still can show.
3. Write a 2 to 4 word headline in their language, true to the video.
4. Save `videos/<slug>/plan.json`:

```json
{
  "headline": "دو تا چهار کلمه",
  "voice": "fa-IR-DilaraNeural",
  "thumbnail_prompt": "English still, no text, no letters",
  "shots": [
    {"prompt": "English still, no text", "line": "جملهٔ گفته‌شده."}
  ]
}
```

Persian default voice is `fa-IR-DilaraNeural`. Use `fa-IR-FaridNeural` when they ask for a male voice. For another language, pick a voice from `python3 -m edge_tts --list-voices`.

5. Build:

```bash
python3 .cursor/skills/youtube-one-prompt/scripts/build_free_video.py videos/<slug>
```

The script installs `edge-tts`, `pillow`, `arabic-reshaper`, and `python-bidi` if they are missing. It waits when the free image endpoint answers 402. Let it finish. It keeps a picture, a voice file, or a clip that is already on disk. To redo one shot, delete `pictures/0N.jpg`, `audio/0N.mp3`, and `clips/0N.mp4` for that number, edit the prompt in `plan.json`, then run the builder again.

6. Watch the first frame of `video.mp4` and the thumbnail. If a caption is garbled or a picture ignores the prompt, fix that shot and rebuild.
7. Write `videos/<slug>/youtube.md` with three true titles, one description, and tags.
8. In `youtube.md`, add a short hook note in their language: what the opening line promises, whether the first picture shows it, and one change that would hold attention. Do not invent a numeric virality score.

## Hand back

Give the path of `video.mp4` and `thumbnail.jpg`, the three titles, and the one hook change. Offer to redo a single shot from one sentence. To redo one shot, edit only that item in `plan.json` and run the builder again.
