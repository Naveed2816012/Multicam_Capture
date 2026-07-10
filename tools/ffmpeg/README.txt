This folder holds the vendored ffmpeg.exe that gets bundled into the built
app so the .exe never needs internet access or a system ffmpeg install.

The CI build (.github/workflows/build.yml) downloads a static ffmpeg build
here automatically before packaging -- nothing to do for release builds.

Running from source (python main.py) without a built .exe:
  - If ffmpeg is already on your system PATH, you don't need anything here.
  - Otherwise, drop a Windows ffmpeg.exe into this folder yourself
    (e.g. from https://www.gyan.dev/ffmpeg/builds/ or
    https://github.com/BtbN/FFmpeg-Builds/releases) and the app will find
    it automatically -- same lookup the built .exe uses.

This binary is intentionally NOT committed to git (see .gitignore) since
it's ~80-100MB and gets fetched fresh on every CI build.
