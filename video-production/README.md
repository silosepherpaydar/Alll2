# SILO SEPHER — Episode 1: A Spring Evening

**Title:** Three Friends, One Dream (سه دوست، یک رؤیا)  
**Duration:** 61 seconds · **Format:** 1080×1920 (9:16) · **Language:** Persian

## Deliverable

[`../SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4`](../SILO_SEPHER_EP1_Three_Friends_One_Dream.mp4) — ready for Instagram Reels / YouTube Shorts.

## Story beats

| Time | Scene | Content |
|------|-------|---------|
| 0:00–0:08 | Spring evening | Establishing meadow shot + narrator |
| 0:08–0:20 | The idea | Arash speaks; coffee beans / espresso / grinder cutaways |
| 0:20–0:35 | Three strengths | Shahab (sales) + Mahyar (capital/facilities) |
| 0:35–0:46 | The decision | Dusk close-ups + narrator |
| 0:46–0:57 | The beginning | Research / planning / sales / sample bag montage |
| 0:57–1:01 | Brand card | Real SILO SEPHER logo + «از ایده تا فنجان» |

## Characters

- **Arash** — mustache, beige jacket; coffee technology passion  
- **Mahyar** — charcoal sweater; capital & facilities  
- **Shahab** — leather jacket; sales & negotiation  

## Rebuild

```bash
pip install edge-tts pillow arabic-reshaper python-bidi numpy
python3 assemble.py
```

Requires `ffmpeg`. Voice-over uses Microsoft Edge neural Persian TTS (`fa-IR-FaridNeural`) with distinct pitch/rate per character. Music/SFX are original synthesized beds.

## Technique note

Picture is cinematic 3D keyframe animation with Ken Burns camera moves, graded stills, cross-scene continuity via shared character references, burned-in RTL Persian subtitles, mixed VO + music + SFX, and a final brand card composited with the official logo asset (not AI lettering).
