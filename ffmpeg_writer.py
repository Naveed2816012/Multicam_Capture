"""ffmpeg helpers: binary location, quality presets, hardware-encoder
smoke-test, and codec argument builders (video + optional audio)."""
import subprocess
import shutil
import numpy as np

QUALITY_PRESETS = {
    "compact":  {"crf": 28, "hw_bitrate": "2M"},
    "balanced": {"crf": 23, "hw_bitrate": "6M"},
    "maximum":  {"crf": 18, "hw_bitrate": "12M"},
}

HARDWARE_ENCODERS = ["h264_nvenc", "h264_qsv", "h264_amf"]


def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path is None:
        raise FileNotFoundError(
            "ffmpeg not found on PATH. Install from ffmpeg.org and add "
            "ffmpeg.exe to your system PATH, then restart."
        )
    return path


def even(x):
    return x - (x % 2)


def build_video_args(codec, quality_preset):
    """Return ffmpeg args for video encoding only (no output path)."""
    q = QUALITY_PRESETS.get(quality_preset, QUALITY_PRESETS["balanced"])
    if codec == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast",
                "-crf", str(q["crf"]), "-pix_fmt", "yuv420p"]
    return ["-c:v", codec, "-b:v", q["hw_bitrate"], "-pix_fmt", "yuv420p"]


def build_audio_args(has_audio):
    """Return ffmpeg args for audio encoding + stream mapping."""
    if not has_audio:
        return []
    return ["-c:a", "aac", "-b:a", "128k", "-map", "0:v", "-map", "1:a"]


def _smoke_test(ffmpeg, codec, width, height, fps):
    blank = np.zeros((height, width, 3), dtype=np.uint8).tobytes()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-frames:v", "1", "-c:v", codec, "-pix_fmt", "yuv420p",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        proc.stdin.write(blank)
        proc.stdin.close()
        return proc.wait(timeout=5) == 0
    except Exception:
        return False


def pick_encoder(use_hardware, width, height, fps):
    ffmpeg = find_ffmpeg()
    if use_hardware:
        for codec in HARDWARE_ENCODERS:
            if _smoke_test(ffmpeg, codec, width, height, fps):
                return codec
    return "libx264"
