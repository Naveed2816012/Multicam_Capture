"""sync_videos.py — frame-accurate multi-source alignment + timecodes

Usage
-----
    python sync_videos.py  <session_folder>             # align only
    python sync_videos.py  <session_folder>  --burn-tc  # + burn timecode into video

What it produces in  <session_folder>/aligned/
-----------------------------------------------
  <name>_aligned.mp4          frame-accurate trimmed video (stream-copy, instant)
  <name>_timecodes.csv        per-frame: frame_index, session_time, unix_ts, duplicate
  <name>_timecodes.srt        same data as SRT subtitles (open in VLC etc.)
  master_timecodes.csv        cross-source table: session_time → frame index in each source
  alignment_report.json       what trim was applied and why

Why frame-accurate (not just wall-clock seconds)?
-------------------------------------------------
Trimming by fractional seconds lands between frames, causing a sub-frame
offset that accumulates as drift when comparing sources.  This script always
trims to an exact frame boundary:

    trim_frames = round(common_start_session_s * fps)
    trim_seconds = trim_frames / fps   ← exact multiple of 1/fps

At the common start point, frame 0 in every aligned video corresponds to the
same session-relative time.  For sources with different FPS:

    cam_a (30 fps): frame N  ←→  session time  N/30 s
    cam_b (15 fps): frame M  ←→  session time  M/15 s
    → cam_a frame N matches cam_b frame round(N * 15/30)

The master_timecodes.csv encodes all cross-source correspondences explicitly
so you never have to calculate this yourself.

Alignment algorithm
-------------------
1. For each source, read the timestamp CSV.
2. Find the first non-duplicate frame (= first frame where the camera actually
   delivered real data, not a startup repeat).
3. Its session_time_s column gives the session-relative time of that frame.
4. common_start_s = max(session_time_s of first real frame) across all sources.
5. For each source: trim_frames = round(common_start_s * fps)
                    trim_seconds = trim_frames / fps   (frame-aligned)
6. ffmpeg -ss trim_seconds -i input.mp4 -c copy output.mp4
"""

import os
import sys
import csv
import json
import math
import shutil
import subprocess


# ── helpers ───────────────────────────────────────────────────────────────

def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p is None:
        sys.exit("ffmpeg not found on PATH. Install from ffmpeg.org and add to PATH.")
    return p


def tc(seconds):
    """Format seconds as HH:MM:SS.mmm (SRT uses comma: HH:MM:SS,mmm)."""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return h, m, s, ms


def srt_time(seconds):
    h, m, s, ms = tc(seconds)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def display_time(seconds):
    h, m, s, ms = tc(seconds)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


# ── CSV readers ───────────────────────────────────────────────────────────

def read_timestamps(csv_path):
    """Return list of dicts from a PacedWriter _timestamps.csv."""
    rows = []
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                # support both old format (no session_time_s) and new
                try:
                    rows.append({
                        "frame_index": int(row["frame_index"]),
                        "session_time_s": float(
                            row.get("session_time_s") or
                            # fallback: derive from scheduled and t0 if available
                            row.get("scheduled_time_unix", "0")
                        ),
                        "scheduled_time_unix": float(
                            row.get("scheduled_time_unix", 0)
                        ),
                        "source_capture_timestamp_unix": float(
                            row["source_capture_timestamp_unix"]
                        ) if row.get("source_capture_timestamp_unix") else None,
                        "is_duplicate": int(row.get("is_duplicate", 1)),
                    })
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return rows


def derive_fps(rows):
    """Derive fps robustly from the last frame's index and session time.

    Using consecutive-row diffs fails when early rows are duplicates
    (identical session_time_s).  Using last_frame_index / last_session_time
    is exact regardless of duplicates at start or end:
        frame_index N at session time N/fps  →  fps = N / (N/fps) = fps
    """
    if len(rows) < 2:
        return 30.0
    last = rows[-1]
    idx  = last["frame_index"]
    t    = last["session_time_s"]
    if idx > 0 and t > 0:
        return round(idx / t, 3)
    return 30.0


def first_real(rows):
    """Return the row dict of the first non-duplicate frame, or None."""
    for r in rows:
        if r["is_duplicate"] == 0:
            return r
    return None


# ── output writers ────────────────────────────────────────────────────────

def write_timecode_csv(path, rows, trim_frames, fps, t0_unix):
    """Write a timecode CSV for the ALIGNED video (frame indices restarted)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "aligned_frame_index",
            "session_time_s",
            "session_time_display",
            "unix_timestamp",
            "is_duplicate",
        ])
        for r in rows[trim_frames:]:
            aligned_idx = r["frame_index"] - trim_frames
            sess_s      = r["session_time_s"]
            unix_ts     = r["scheduled_time_unix"] if r["scheduled_time_unix"] else (
                t0_unix + sess_s if t0_unix else "")
            w.writerow([
                aligned_idx,
                f"{sess_s:.6f}",
                display_time(sess_s),
                f"{unix_ts:.6f}" if unix_ts else "",
                r["is_duplicate"],
            ])


def write_srt(path, rows, trim_frames, fps):
    """Write an SRT subtitle file for the ALIGNED video showing per-frame
    session time and duplicate status.  Loadable in VLC, MPC-HC, ffplay etc."""
    frame_dur = 1.0 / fps
    with open(path, "w") as f:
        seq = 1
        for r in rows[trim_frames:]:
            aligned_idx = r["frame_index"] - trim_frames
            sess_s      = r["session_time_s"]
            t_start     = srt_time(sess_s)
            t_end       = srt_time(sess_s + frame_dur)
            flag        = "●" if r["is_duplicate"] == 0 else "○ dup"
            f.write(f"{seq}\n{t_start} --> {t_end}\n"
                    f"{display_time(sess_s)}  fr#{aligned_idx}  {flag}\n\n")
            seq += 1


def write_master_timecode(path, sources_info):
    """Write a cross-source CSV mapping session_time → frame index in each source.

    Resolution = the highest fps source; one row per frame of that source.
    Other sources: nearest frame = round(session_time * their_fps).
    """
    if not sources_info:
        return
    max_fps = max(s["fps"] for s in sources_info.values())
    # duration = min across sources so the table ends where all are valid
    max_session_s = min(
        (len(s["rows"]) - s["trim_frames"]) / s["fps"]
        for s in sources_info.values()
        if len(s["rows"]) > s["trim_frames"]
    )
    n_frames = int(max_session_s * max_fps)
    names = list(sources_info)

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        header = ["session_time_s", "session_time_display"] + \
                 [f"{n}_frame" for n in names]
        w.writerow(header)
        for i in range(n_frames):
            sess_s = i / max_fps
            row = [f"{sess_s:.6f}", display_time(sess_s)]
            for n in names:
                fps = sources_info[n]["fps"]
                row.append(round(sess_s * fps))
            w.writerow(row)


# ── main ──────────────────────────────────────────────────────────────────

def main(session_dir, burn_tc=False):
    if not os.path.isdir(session_dir):
        sys.exit(f"Not a directory: {session_dir}")

    ffmpeg = find_ffmpeg()

    # ── discover sources ─────────────────────────────────────────────────
    sources = {}
    for fname in sorted(os.listdir(session_dir)):
        if fname.endswith("_timestamps.csv"):
            name  = fname[: -len("_timestamps.csv")]
            video = os.path.join(session_dir, f"{name}.mp4")
            csv_p = os.path.join(session_dir, fname)
            if os.path.exists(video):
                sources[name] = {"video": video, "csv": csv_p}

    if not sources:
        sys.exit("No source videos + timestamp logs found.")

    print(f"Found {len(sources)} source(s): {', '.join(sources)}\n")

    # ── read + analyse each source ───────────────────────────────────────
    # Also read session t0 from sync stub if available
    t0_unix = None
    sync_path = os.path.join(session_dir, "session_sync.json")
    if os.path.exists(sync_path):
        try:
            with open(sync_path) as f:
                t0_unix = json.load(f).get("t0_unix")
        except Exception:
            pass

    info = {}
    for name, s in sources.items():
        rows = read_timestamps(s["csv"])
        if not rows:
            print(f"  ⚠  {name}: timestamp log empty or unreadable — skipping.")
            continue
        fps   = derive_fps(rows)
        first = first_real(rows)
        if first is None:
            print(f"  ⚠  {name}: no non-duplicate frames — skipping.")
            continue
        info[name] = {
            "video":   s["video"],
            "rows":    rows,
            "fps":     fps,
            "first_session_s": first["session_time_s"],
        }
        print(f"  {name}:  fps={fps}  first real frame @ session {display_time(first['session_time_s'])}")

    if not info:
        sys.exit("No usable sources. Aborting.")

    # ── common alignment point ────────────────────────────────────────────
    common_start_s = max(v["first_session_s"] for v in info.values())
    print(f"\nAlignment point: {display_time(common_start_s)} "
          f"(latest first-real-frame across all sources)\n")

    # ── frame-accurate trim per source ───────────────────────────────────
    out_dir = os.path.join(session_dir, "aligned")
    os.makedirs(out_dir, exist_ok=True)

    report      = {}
    sources_info = {}

    for name, s in info.items():
        fps         = s["fps"]
        # Round to nearest frame boundary (not floor, not wall-clock seconds)
        trim_frames = round(common_start_s * fps)
        trim_s      = trim_frames / fps          # exact multiple of 1/fps

        s["trim_frames"] = trim_frames
        sources_info[name] = s

        video_in  = s["video"]
        video_out = os.path.join(out_dir, f"{name}_aligned.mp4")
        tc_csv    = os.path.join(out_dir, f"{name}_timecodes.csv")
        srt_path  = os.path.join(out_dir, f"{name}_timecodes.srt")

        print(f"  {name}: trim {trim_frames} frames "
              f"({trim_s*1000:.2f} ms at {fps} fps) → {os.path.basename(video_out)}")

        # ── video trim (stream copy, no re-encode) ───────────────────────
        cmd = [ffmpeg, "-y", "-loglevel", "error",
               "-ss", f"{trim_s:.9f}",
               "-i", video_in,
               "-c", "copy", video_out]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"    ⚠  ffmpeg trim failed for {name}.")
            continue

        # ── optional: burn session timecode into video ───────────────────
        if burn_tc:
            burnt = os.path.join(out_dir, f"{name}_aligned_tc.mp4")
            # pts starts at 0 after trim, which equals common_start_s in session.
            # Offset expr: (pts + common_start_s) gives absolute session time.
            offset  = common_start_s
            tc_expr = (
                f"drawtext=fontsize=22:fontcolor=white"
                f":box=1:boxcolor=black@0.6:x=10:y=10"
                f":text='%{{pts\\:hms\\:{offset}}}'"
            )
            cmd_tc = [ffmpeg, "-y", "-loglevel", "error",
                      "-i", video_out, "-vf", tc_expr,
                      "-c:v", "libx264", "-preset", "veryfast",
                      "-c:a", "copy", burnt]
            subprocess.run(cmd_tc)
            print(f"    → timecode burn: {os.path.basename(burnt)}")

        # ── per-file timecode CSV ─────────────────────────────────────────
        write_timecode_csv(tc_csv, s["rows"], trim_frames, fps, t0_unix)
        print(f"    → timecodes: {os.path.basename(tc_csv)}")

        # ── per-file SRT subtitles ────────────────────────────────────────
        write_srt(srt_path, s["rows"], trim_frames, fps)
        print(f"    → subtitles: {os.path.basename(srt_path)}")

        report[name] = {
            "fps":              fps,
            "trim_frames":      trim_frames,
            "trim_seconds":     round(trim_s, 9),
            "common_start_s":   round(common_start_s, 6),
            "first_real_s":     round(s["first_session_s"], 6),
            "offset_from_common_ms": round((s["first_session_s"] - common_start_s) * 1000, 3),
        }

    # ── master cross-source timecode table ───────────────────────────────
    master_path = os.path.join(out_dir, "master_timecodes.csv")
    write_master_timecode(master_path, sources_info)
    print(f"\n  → master timecodes: {os.path.basename(master_path)}")

    # ── alignment report ─────────────────────────────────────────────────
    report["_alignment"] = {
        "common_start_session_s":  round(common_start_s, 6),
        "common_start_display":    display_time(common_start_s),
        "session_t0_unix":         t0_unix,
        "note": (
            "All aligned videos start at common_start_session_s. "
            "Frame 0 in every aligned file corresponds to the same "
            "wall-clock instant. For different-fps sources: "
            "cam_a frame N matches cam_b frame round(N * fps_b / fps_a). "
            "master_timecodes.csv encodes all correspondences explicitly."
        ),
    }
    with open(os.path.join(out_dir, "alignment_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n✓ All files written to:  {out_dir}")
    print("\nEvery aligned video:")
    print("  • starts at the same session instant (frame 0 = common clock)")
    print("  • has a _timecodes.csv  (frame → session time + Unix timestamp)")
    print("  • has a _timecodes.srt  (load in VLC: Subtitles → Add Subtitle File)")
    print("  • master_timecodes.csv  maps session time → frame # in each source")
    if burn_tc:
        print("  • _aligned_tc.mp4 has the timecode overlaid on the video itself")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage:  python {os.path.basename(sys.argv[0])} <session_folder> [--burn-tc]")
        sys.exit(1)
    main(sys.argv[1], burn_tc="--burn-tc" in sys.argv)
