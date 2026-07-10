"""Startup wrapper used by packaged builds.

It keeps double-click failures visible by writing a crash log and showing a
small Windows message box instead of silently exiting.
"""
import os
import sys
import traceback


APP_NAME = "MulticamCapture"


def app_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    if base:
        folder = os.path.join(base, APP_NAME)
    else:
        folder = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
    os.makedirs(folder, exist_ok=True)
    return folder


def crash_log_path():
    return os.path.join(app_data_dir(), "crash.log")


def write_crash_log():
    path = crash_log_path()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{APP_NAME} failed to start.\n")
        handle.write(f"Executable: {sys.executable}\n")
        handle.write(f"Arguments: {sys.argv}\n")
        handle.write(f"Python: {sys.version}\n\n")
        traceback.print_exc(file=handle)
    return path


def show_startup_error(error, log_path):
    if os.environ.get("CI"):
        return

    message = (
        "Multicam Capture could not start on this computer.\n\n"
        f"Error: {error}\n\n"
        f"A crash log was saved here:\n{log_path}\n\n"
        "If Windows blocked the downloaded file, right-click the .exe, open "
        "Properties, and choose Unblock. If the log mentions VCRUNTIME or "
        "a DLL load failure, install the Microsoft Visual C++ 2015-2022 "
        "Redistributable and try again."
    )

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Multicam Capture failed to start", message)
        root.destroy()
    except Exception:
        pass


def run_self_test():
    import cv2  # noqa: F401
    import mss  # noqa: F401
    import numpy  # noqa: F401
    from PIL import Image, ImageTk  # noqa: F401

    from camera_manager import list_audio_devices, list_cameras  # noqa: F401
    from ffmpeg_writer import locate_ffmpeg  # noqa: F401
    from screen_capture import ScreenCaptureThread, list_monitors  # noqa: F401
    from session import RecordingSession  # noqa: F401

    return 0


def main():
    try:
        if "--self-test" in sys.argv:
            return run_self_test()

        from main import App

        App().mainloop()
        return 0
    except Exception as error:
        log_path = write_crash_log()
        show_startup_error(error, log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
