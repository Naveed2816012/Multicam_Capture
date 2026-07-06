"""PacedWriter: wall-clock-paced video writer with optional audio.

Runs on its own fixed schedule (t0 + n/fps), completely decoupled from the
capture thread. If the source stalls and delivers no new frame, the last
frame is repeated — keeping output duration always equal to wall-clock time.

Optional audio_device (DirectShow device name string) adds a live audio
stream via ffmpeg's DirectShow input, muxed into the same .mp4.
"""
import threading
import time
import csv
import subprocess
import numpy as np
import cv2

from ffmpeg_writer import (find_ffmpeg, pick_encoder,
                            build_video_args, build_audio_args, even)


class PacedWriter(threading.Thread):
    def __init__(self, name, source, output_video_path, output_log_path,
                 width, height, fps, t0,
                 quality_preset="balanced", use_hardware=False,
                 audio_device=None):
        super().__init__(daemon=True)
        self.name   = name
        self.source = source
        self.output_video_path = output_video_path
        self.width  = even(width)
        self.height = even(height)
        self.fps    = fps
        self.t0     = t0
        self._stop_event = threading.Event()
        self.audio_device = audio_device

        self.frames_written          = 0
        self.duplicate_frames        = 0
        self.consecutive_duplicates  = 0
        self.max_consecutive_duplicates = 0
        self._paused      = False
        self._pause_start = None
        self._pause_lock  = threading.Lock()

        ffmpeg     = find_ffmpeg()
        self.codec = pick_encoder(use_hardware, self.width, self.height, fps)
        has_audio  = bool(audio_device)

        # Build command ─────────────────────────────────────────────────
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            # video stream from stdin pipe
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}", "-r", str(fps), "-i", "-",
        ]
        if has_audio:
            cmd += ["-f", "dshow", "-i", f"audio={audio_device}"]

        cmd += build_video_args(self.codec, quality_preset)
        cmd += build_audio_args(has_audio)
        cmd += [output_video_path]

        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

        self.log_file   = open(output_log_path, "w", newline="")
        self.log_writer = csv.writer(self.log_file)
        self.log_writer.writerow([
            "frame_index",
            "session_time_s",          # seconds since t0 (common clock)
            "scheduled_time_unix",     # absolute wall-clock of this frame
            "source_capture_timestamp_unix",  # when camera actually delivered it
            "is_duplicate",            # 1 = source stalled, frame repeated
        ])
        self._last_source_ts = None
        self._blank          = None

    def run(self):
        frame_index = 0
        while not self._stop_event.is_set():
            # While paused: spin-wait at low cost; don't advance frame_index
            with self._pause_lock:
                paused = self._paused
            if paused:
                time.sleep(0.02)
                continue

            scheduled = self.t0 + frame_index / self.fps
            now = time.time()
            if scheduled > now:
                time.sleep(min(scheduled - now, 0.02))
                continue

            frame, src_ts = self.source.get_latest()
            is_dup = src_ts is not None and src_ts == self._last_source_ts

            if frame is None:
                if self._blank is None:
                    self._blank = np.zeros(
                        (self.height, self.width, 3), dtype=np.uint8)
                frame = self._blank
                is_dup = True
            elif frame.shape[1] != self.width or frame.shape[0] != self.height:
                frame = cv2.resize(frame, (self.width, self.height))

            try:
                self.proc.stdin.write(frame.tobytes())
            except (BrokenPipeError, OSError):
                break

            self.log_writer.writerow([
                frame_index,
                f"{scheduled - self.t0:.6f}",          # session_time_s
                f"{scheduled:.6f}",                     # scheduled_time_unix
                "" if src_ts is None else f"{src_ts:.6f}",
                int(is_dup),
            ])

            if is_dup:
                self.duplicate_frames      += 1
                self.consecutive_duplicates += 1
                self.max_consecutive_duplicates = max(
                    self.max_consecutive_duplicates,
                    self.consecutive_duplicates)
            else:
                self.consecutive_duplicates = 0
                self._last_source_ts = src_ts

            frame_index += 1
            self.frames_written = frame_index

        self._close()

    def stop(self):
        self._stop_event.set()

    def pause(self):
        """Pause writing. Preview capture keeps running; no frames are written."""
        with self._pause_lock:
            if not self._paused:
                self._paused      = True
                self._pause_start = time.time()

    def resume(self):
        """Resume writing. Shifts t0 forward so no catch-up flood occurs."""
        with self._pause_lock:
            if self._paused and self._pause_start is not None:
                self.t0    += time.time() - self._pause_start   # absorb pause gap
                self._paused      = False
                self._pause_start = None

    @property
    def is_paused(self):
        with self._pause_lock:
            return self._paused

    def _close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait()
        self.log_file.close()

    def summary(self):
        drop_s  = self.duplicate_frames / self.fps if self.fps else 0
        worst_s = (self.max_consecutive_duplicates / self.fps
                   if self.fps else 0)
        pct = round(100 * self.duplicate_frames / max(1, self.frames_written), 2)
        return {
            "name":                      self.name,
            "codec_used":                self.codec,
            "audio_device":              self.audio_device or "none",
            "frames_written":            self.frames_written,
            "video_duration_seconds":    round(self.frames_written / self.fps, 2),
            "duplicate_frames":          self.duplicate_frames,
            "duplicate_pct":             pct,
            "total_dropped_seconds":     round(drop_s,  2),
            "worst_single_freeze_seconds": round(worst_s, 2),
        }
