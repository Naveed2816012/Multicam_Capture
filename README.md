# Multi-Cam + Screen Recorder

AMCap-style capture tool, but for an arbitrary number of simultaneous
cameras plus the screen, with synchronized recording for behavior
experiments.

## Easiest install (no Python needed): download the .exe

1. Go to this repo's **Releases** page on GitHub.
2. Download `MulticamCapture.exe` from the latest release.
3. Install ffmpeg and put `ffmpeg.exe` in the same folder as
   `MulticamCapture.exe` (or anywhere on PATH).
4. Double-click `MulticamCapture.exe`. No Python, no pip, no venv.

If the single `.exe` does not open on a different laptop, download
`MulticamCapture-Windows-Portable.zip` from the same release, extract the
whole folder, and double-click the `MulticamCapture.exe` inside it. The
portable ZIP is less likely to be blocked by antivirus tools because it
does not have to unpack itself into a temporary folder first.

If Windows blocks the downloaded app, right-click the `.exe`, choose
**Properties**, tick **Unblock** if it appears, then try again. Startup
crashes are written to:

`%LOCALAPPDATA%\MulticamCapture\crash.log`

A new .exe is built automatically every time a version tag (`v1.0`,
`v1.1`, ...) is pushed to GitHub — see `.github/workflows/build.yml`.

## Setup (Windows) — from source

### Easiest: double-click run.bat
1. Install ffmpeg and make sure `ffmpeg.exe` is on PATH (test with
   `ffmpeg -version` in a terminal).
2. Double-click **`run.bat`**. The first time, it creates a `venv`
   folder next to the scripts and installs everything from
   `requirements.txt` into it, then launches the app. Every time after
   that, it just activates the existing `venv` and launches — no
   separate setup step needed.

### Manual / if you prefer the terminal
1. Install ffmpeg as above.
2. `pip install -r requirements.txt`
   - `pygrabber` gives you real camera names in the device list. Without
     it, the tool still works but lists cameras as "Camera 0", "Camera 1", etc.
3. `python main.py`

## Using it

- **Cameras**: click Refresh to enumerate, check the ones you want.
- **Screen**: check "Capture screen", pick which monitor.
- **Output**: choose a folder and session name. Keep the session name
  short — combined with your B:\ drive's existing deep folder structure,
  long names can hit the Windows 260-char path limit.
- **Start Recording**: spins up one ffmpeg encoder per selected source,
  writes `<source>.mp4` + `<source>_timestamps.csv` for each, plus one
  `session_sync.json` for the whole session.
- **Stop Recording**: drains any buffered frames before closing files
  (no truncated last second).

Live preview keeps running for all sources whether or not you're
recording — useful for framing/focus checks before you hit Start.

## The dropped-frame problem (why this is more reliable than Bonsai)

On a weak laptop, flaky USB hub, or overloaded port, a camera capture
loop can stall for tens to hundreds of milliseconds. The naive way to
write video (one output frame per frame the camera delivers) turns
every stall into **missing time**: the resulting file plays back at a
constant frame rate but represents *less real time than actually
elapsed*. You only find out when you compare the video length to the
timestamp CSV and they don't match -- by then the experiment is over.

This tool does not write video that way. Each source's `PacedWriter`
runs on its own fixed wall-clock schedule (`t0 + frame_index / fps`),
completely decoupled from the camera. At every scheduled tick it asks
the capture thread "what's your most recent frame?" and writes it --
repeating the last frame if nothing new arrived. The result:

**`frames_written / fps` always equals real recording duration, for
every source, regardless of how unreliable the camera is.** A stall
becomes a visible freeze in the footage instead of an invisible
timeline compression, and it's tagged in that source's
`_timestamps.csv` (`is_duplicate` column) when it happens.

When you click Stop, a `reliability_report.json` is written into the
session folder and summarized in a popup, e.g.:

```json
{
  "cam_0_Logitech": {
    "frames_written": 9000,
    "video_duration_seconds": 300.0,
    "duplicate_frames": 42,
    "duplicate_pct": 0.47,
    "total_dropped_seconds": 1.4,
    "worst_single_freeze_seconds": 0.3
  }
}
```

`video_duration_seconds` is the number you actually care about --
it will match your real recording length even if `duplicate_pct` is
high. `worst_single_freeze_seconds` tells you whether you had one bad
half-second or it was scattered/negligible.

## Quality / file size

Three presets (CRF-based for the default software encoder):

| Preset | Use when |
|---|---|
| Small file | Long sessions, many simultaneous streams, disk space is tight |
| Balanced (default) | Most behavior recording |
| High quality | Need to zoom in / score fine detail later |

**"Try hardware encoder"** checkbox: if checked, the tool smoke-tests
nvenc (Nvidia), QSV (Intel), then AMF (AMD) at session start, and uses
whichever one actually works on your machine -- silently falling back
to software (libx264) if none do. This matters beyond just speed: with
4+ streams encoding simultaneously on a weak CPU, *software encoding
itself* can be the thing starving the camera/USB threads and causing
drops in the first place. Offloading encoding to the GPU reduces that
pressure. Worth turning on if you have a dedicated GPU; leave off if
you don't (the smoke test adds a small delay at Start with no benefit).

## How synchronization across sources works

Cameras and the screen run on independent capture loops (different
fps, different OS scheduling), so they can't be perfectly frame-locked
at capture time. Instead, every frame written to disk is timestamped
with `time.time()` (Unix epoch, microsecond precision) in that source's
`_timestamps.csv`. `session_sync.json` records the session's `t0_unix`.

To align two sources after recording:
1. Open both `_timestamps.csv` files.
2. Find each source's first logged timestamp.
3. The difference between those two first-timestamps is the offset —
   trim that many seconds off the front of whichever source started
   first (e.g. `ffmpeg -ss <offset> -i source.mp4 ...`).
4. From there, frame N in one source corresponds to whichever frame in
   the other source has the closest timestamp in its CSV — not
   necessarily frame N, if fps differ.

This is good enough for behavior-scoring alignment (e.g. matching a
top-view camera to a side-view camera to a screen recording of a
stimulus). It is not frame-accurate hardware sync — if you need that
(e.g. for electrophysiology-grade timing), you'd want a hardware
trigger/genlock setup instead, which is a different (and more
expensive) problem than what this tool solves.

## Known limitations / things to adjust for your setup

- Camera resolution/fps defaults to 1280x720 @ 30fps in
  `camera_manager.CameraCaptureThread.__init__`. If a camera doesn't
  support that, OpenCV will give you whatever it can — `actual_width`/
  `actual_height` reflect what was actually granted, and the writer
  uses those, so it won't crash, but check your footage if you need a
  specific resolution.
- Screen capture defaults to 15fps (cheaper than camera fps, screen
  content usually doesn't need 30). Change in `main.py` where
  `add_screen(..., fps=15)` is called if you need more.
- `-preset veryfast` in `ffmpeg_writer.build_encode_args()` trades file
  size for low CPU load, since you may be encoding 4+ streams at once.
  Drop to `medium` there if you have CPU headroom and want smaller files
  at a given quality preset.
- No audio capture currently (behavior rigs usually don't need it; say
  if you do and I'll add it per-source).
