"""Session: per-source writer lifecycle, output folder, sync log."""
import os
import json
import time

from paced_writer import PacedWriter


class RecordingSession:
    def __init__(self, output_dir, session_name):
        self.session_dir = os.path.join(output_dir, session_name)
        os.makedirs(self.session_dir, exist_ok=True)
        self.writers = {}
        self.t0      = None

    def start_writer(self, name, src,
                     output_label=None,
                     out_width=None, out_height=None,
                     fps_override=None,
                     quality_preset="balanced",
                     use_hardware=False,
                     audio_device=None,
                     shared_t0=None):
        """Start recording one source.  shared_t0: pass the same value to all
        writers in a session so their frame schedules share one reference clock
        — this is what makes Start-All recordings auto-alignable."""
        if name in self.writers:
            return
        if self.t0 is None:
            self.t0 = shared_t0 or time.time()
            self._write_sync_stub()

        t0     = shared_t0 or time.time()
        width  = out_width  or src.actual_width
        height = out_height or src.actual_height
        fps    = fps_override or src.fps
        label  = (output_label or name).replace(" ", "_")

        video_path = os.path.join(self.session_dir, f"{label}.mp4")
        log_path   = os.path.join(self.session_dir, f"{label}_timestamps.csv")

        w = PacedWriter(name, src, video_path, log_path,
                        width, height, fps, t0,
                        quality_preset=quality_preset,
                        use_hardware=use_hardware,
                        audio_device=audio_device)
        w.start()
        self.writers[name] = w

    def pause_writer(self, name):
        w = self.writers.get(name)
        if w: w.pause()

    def resume_writer(self, name):
        w = self.writers.get(name)
        if w: w.resume()

    def is_paused(self, name):
        w = self.writers.get(name)
        return w.is_paused if w else False

    def stop_writer(self, name):
        w = self.writers.pop(name, None)
        if w is None:
            return None
        w.stop()
        w.join(timeout=10)
        summary = w.summary()
        self._append_reliability(name, summary)
        return summary

    def stop_all(self):
        for name in list(self.writers):
            self.stop_writer(name)

    def is_recording(self, name):
        return name in self.writers

    def any_recording(self):
        return bool(self.writers)

    def _write_sync_stub(self):
        meta = {
            "session_dir": self.session_dir,
            "t0_unix": self.t0,
            "note": (
                "Each source's video duration equals wall-clock recording "
                "duration. See <label>_timestamps.csv for per-frame "
                "is_duplicate flags."
            ),
        }
        with open(os.path.join(self.session_dir, "session_sync.json"), "w") as f:
            json.dump(meta, f, indent=2)

    def _append_reliability(self, name, summary):
        path = os.path.join(self.session_dir, "reliability_report.json")
        try:
            with open(path) as f:
                report = json.load(f)
        except (OSError, json.JSONDecodeError):
            report = {}
        report[name] = summary
        with open(path, "w") as f:
            json.dump(report, f, indent=2)

    def read_reliability_report(self):
        path = os.path.join(self.session_dir, "reliability_report.json")
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
