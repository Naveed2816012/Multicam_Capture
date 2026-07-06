"""Screen capture thread using mss. Same get_latest() pattern as
CameraCaptureThread so PacedWriter can treat any source identically."""
import threading
import time
import numpy as np
import mss


def list_monitors():
    """Return [(index, monitor_dict), ...]. Index 0 is 'all monitors
    combined' (mss convention); 1..N are individual physical monitors.
    """
    with mss.mss() as sct:
        return list(enumerate(sct.monitors))


class ScreenCaptureThread(threading.Thread):
    def __init__(self, monitor_index=1, fps=15):
        super().__init__(daemon=True)
        self.monitor_index = monitor_index
        self.fps = fps
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest_frame = None
        self._latest_ts = None
        self.frames_captured = 0
        self.actual_width = None
        self.actual_height = None

    def get_preview_frame(self):
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def get_latest(self):
        with self._lock:
            if self._latest_frame is None:
                return None, None
            return self._latest_frame.copy(), self._latest_ts

    def run(self):
        interval = 1.0 / self.fps
        with mss.mss() as sct:
            mon = sct.monitors[self.monitor_index]
            self.actual_width, self.actual_height = mon["width"], mon["height"]
            next_t = time.time()
            while not self._stop_event.is_set():
                shot = sct.grab(mon)
                frame = np.array(shot)[:, :, :3]  # BGRA -> BGR, drops alpha
                frame = np.ascontiguousarray(frame)
                ts = time.time()
                with self._lock:
                    self._latest_frame = frame
                    self._latest_ts = ts
                self.frames_captured += 1
                next_t += interval
                sleep_for = next_t - time.time()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_t = time.time()

    def stop(self):
        self._stop_event.set()
