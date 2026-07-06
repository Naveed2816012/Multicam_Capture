"""Camera + audio device enumeration and per-camera capture thread."""
import subprocess
import threading
import time
import re
import cv2

try:
    from pygrabber.dshow_graph import FilterGraph
    _HAS_PYGRABBER = True
except Exception:
    _HAS_PYGRABBER = False

_MAX_PROBE   = 16
_PROBE_TIMEOUT = 3.0
_BACKENDS = [
    (cv2.CAP_DSHOW, "DSHOW"),
    (cv2.CAP_MSMF,  "MSMF"),
]


_BUILTIN_KEYWORDS = [
    "integrated", "built-in", "builtin", "internal", "facetime",
    "ir camera", "hd camera", "hp hd", "dell hd", "thinkpad", "surface",
    "intel(r) avc", "laptop", "notebook",
]

def is_builtin_camera(name):
    """Return True if the camera name suggests a built-in / integrated webcam."""
    n = name.lower()
    return any(kw in n for kw in _BUILTIN_KEYWORDS)



    for flag, name in _BACKENDS:
        try:
            cap = cv2.VideoCapture(index, flag)
            if not cap.isOpened():
                cap.release(); continue
            ok, _ = cap.read()
            cap.release()
            if ok:
                with lock:
                    results[index] = (flag, name)
                return
        except Exception:
            pass


def list_cameras(max_probe=_MAX_PROBE):
    """Return [(index, name, backend_flag), ...] for reachable cameras."""
    if _HAS_PYGRABBER:
        try:
            graph = FilterGraph()
            names = graph.get_input_devices()
            if names:
                return [(i, n, cv2.CAP_DSHOW) for i, n in enumerate(names)]
        except Exception:
            pass

    results, lock, threads = {}, threading.Lock(), []
    for i in range(max_probe):
        t = threading.Thread(target=_probe_index, args=(i, results, lock),
                             daemon=True)
        t.start(); threads.append(t)
    deadline = time.time() + _PROBE_TIMEOUT
    for t in threads:
        t.join(timeout=max(deadline - time.time(), 0))
    return sorted(
        [(idx, f"Camera {idx} ({bname})", bflag)
         for idx, (bflag, bname) in results.items()],
        key=lambda x: x[0],
    )


def list_audio_devices():
    """Return list of audio device name strings available via DirectShow.

    Runs  ffmpeg -list_devices true -f dshow -i dummy  and parses stderr.
    Returns [] if ffmpeg is missing or no audio devices found.
    """
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    try:
        result = subprocess.run(
            [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            timeout=6, text=True,
        )
        raw = result.stderr
    except Exception:
        return []

    devices, in_audio = [], False
    for line in raw.splitlines():
        if "audio devices" in line.lower():
            in_audio = True; continue
        if "video devices" in line.lower():
            in_audio = False; continue
        if in_audio:
            m = re.search(r'"([^"]+)"', line)
            if m:
                devices.append(m.group(1))
    return devices


class CameraCaptureThread(threading.Thread):
    """Continuously grabs frames; exposes latest (frame, timestamp) pair."""

    def __init__(self, index, name, backend=cv2.CAP_DSHOW,
                 width=1280, height=720, fps=30):
        super().__init__(daemon=True)
        self.index = index
        self.name  = name
        self.fps   = fps

        self.cap = cv2.VideoCapture(index, backend)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS,          fps)

        self.actual_width  = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or width
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height

        self._stop_event   = threading.Event()
        self._lock         = threading.Lock()
        self._latest_frame = None
        self._latest_ts    = None
        self.frames_captured = 0

    def get_preview_frame(self):
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def get_latest(self):
        with self._lock:
            if self._latest_frame is None:
                return None, None
            return self._latest_frame.copy(), self._latest_ts

    def run(self):
        while not self._stop_event.is_set():
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.01); continue
            ts = time.time()
            with self._lock:
                self._latest_frame = frame
                self._latest_ts    = ts
            self.frames_captured += 1
        self.cap.release()

    def stop(self):
        self._stop_event.set()
