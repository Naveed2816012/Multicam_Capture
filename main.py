"""Multi-Camera + Screen Recorder

Per-source settings panel (below each live preview):
  • Label            — custom output filename
  • Capture Resolution
  • Frame Rate
  • Encoding Quality
  • Microphone       — optional DirectShow audio device
  • Auto-Stop        — optional duration limit

Default: Balanced quality, GPU encoding auto-detected, 30 fps, 1280×720.
Opens maximised.
"""
import math
import re
import os
import time
import json
import threading
import queue as pyqueue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
from PIL import Image, ImageTk

from camera_manager import (list_cameras, list_audio_devices,
                              CameraCaptureThread, is_builtin_camera)
from screen_capture import list_monitors, ScreenCaptureThread
from session import RecordingSession

# ── colours ───────────────────────────────────────────────────────────────
REC_BG   = "#c0392b"
REC_FG   = "white"
IDLE_BG  = "#27ae60"
IDLE_FG  = "white"
PANEL_BG = "#f5f5f5"

PREVIEW_REFRESH_MS = 66   # ~15 fps UI redraw

# ── per-tile setting options ───────────────────────────────────────────────
QUAL_OPTIONS = {
    "Maximum quality":     "maximum",
    "Balanced (default)":  "balanced",
    "Compact (smallest file)": "compact",
}


# ── helpers ────────────────────────────────────────────────────────────────
def _aspect(w, h):
    if not w or not h: return "?:?"
    g = math.gcd(w, h)
    return f"{w//g}:{h//g}"

def _fit(sw, sh, bw, bh):
    if not sw or not sh: return bw, bh
    s = min(bw / sw, bh / sh)
    return max(1, int(sw * s)), max(1, int(sh * s))

def _clean_label(internal):
    """'cam_0_Camera_0_(DSHOW)' → 'Camera 0'"""
    s = internal.replace("_", " ")
    s = re.sub(r"^cam \d+ ", "", s)
    s = re.sub(r"\(DSHOW\)|\(MSMF\)", "", s).strip()
    return s or internal


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Multi-Cam + Screen Recorder")
        self.minsize(800, 560)
        try:
            self.state("zoomed")      # Windows: open maximised
        except tk.TclError:
            self.attributes("-zoomed", True)   # Linux fallback

        self.preview_sources: dict = {}
        self.tiles:           dict = {}
        self.session: RecordingSession | None = None

        self.cameras      = []
        self.monitor_map  = {}
        self.audio_devices = []

        self._cell_w = 320
        self._cell_h = 240
        self._resize_job  = None
        self._enum_queue  = pyqueue.Queue()
        self._drag_source    = None
        self._drag_over      = None
        self._maximized_tile = None   # name of tile currently expanded to fill area
        self._fps_track      = {}     # name -> {t, f, fps} for live fps computation
        self._builtin_info   = None

        self._build_top_bar()
        self._build_tile_area()
        self._build_status_bar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_resize)
        self.after(PREVIEW_REFRESH_MS, self._tick_previews)

        self.status_var.set("Detecting sources…")
        threading.Thread(target=self._enum_worker, daemon=True).start()
        self.after(120, self._poll_enum)

    # ══════════════════════════════════════════════════════════════════════
    # TOP BAR
    # ══════════════════════════════════════════════════════════════════════

    def _build_top_bar(self):
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        # Built-in camera (laptop webcam) ─────────────────────────────────
        bic = ttk.LabelFrame(bar, text="Built-in Camera")
        bic.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.builtin_var = tk.BooleanVar(value=False)
        self._builtin_chk = ttk.Checkbutton(
            bic, text="Add Built-in Webcam",
            variable=self.builtin_var,
            command=self._toggle_builtin, state=tk.DISABLED)
        self._builtin_chk.pack(anchor="w", padx=6, pady=4)
        self._builtin_label = ttk.Label(bic, text="Detecting…",
                                         foreground="#888",
                                         font=("Segoe UI", 8))
        self._builtin_label.pack(anchor="w", padx=6, pady=(0, 4))

        # Screen source ───────────────────────────────────────────────────
        scr = ttk.LabelFrame(bar, text="Screen Source")
        scr.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.screen_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(scr, text="Add Screen Capture",
                        variable=self.screen_var,
                        command=self._toggle_screen).pack(
                            anchor="w", padx=6, pady=4)
        self.monitor_choice      = tk.StringVar(value="Detecting…")
        self._mon_menu_frame     = ttk.Frame(scr)
        self._mon_menu_frame.pack(anchor="w", padx=6, pady=(0, 4))
        ttk.Label(self._mon_menu_frame,
                  textvariable=self.monitor_choice).pack(anchor="w")

        # Save location ───────────────────────────────────────────────────
        out = ttk.LabelFrame(bar, text="Save Location")
        out.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.out_dir = tk.StringVar(value=os.path.expanduser("~"))
        ttk.Entry(out, textvariable=self.out_dir,
                  width=30).pack(padx=6, pady=2)
        ttk.Button(out, text="Browse…",
                   command=self._browse).pack(padx=6, pady=2)
        self.session_name = tk.StringVar(
            value=time.strftime("session_%Y%m%d_%H%M%S"))
        ttk.Entry(out, textvariable=self.session_name,
                  width=30).pack(padx=6, pady=2)

        # Encoding ────────────────────────────────────────────────────────
        enc = ttk.LabelFrame(bar, text="Encoding")
        enc.pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.hw_var = tk.BooleanVar(value=True)   # ON by default
        ttk.Checkbutton(enc,
                        text="Use GPU Encoding (auto-detects, falls back to CPU)",
                        variable=self.hw_var).pack(
                            anchor="w", padx=6, pady=6)

        # Global rec buttons ──────────────────────────────────────────────
        ctrl = ttk.Frame(bar)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=14)
        self.start_all_btn = ttk.Button(ctrl, text="▶  Record All",
                                         command=self._start_all)
        self.start_all_btn.pack(fill=tk.X, pady=4)
        self.stop_all_btn = ttk.Button(ctrl, text="■  Stop All",
                                        command=self._stop_all,
                                        state=tk.DISABLED)
        self.stop_all_btn.pack(fill=tk.X, pady=4)

    # ══════════════════════════════════════════════════════════════════════
    # TILE AREA
    # ══════════════════════════════════════════════════════════════════════

    def _build_tile_area(self):
        self.tile_area = ttk.Frame(self)
        self.tile_area.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                             padx=6, pady=4)

    def _build_status_bar(self):
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(
            side=tk.BOTTOM, fill=tk.X, padx=8, pady=3)

    # ══════════════════════════════════════════════════════════════════════
    # ENUMERATION
    # ══════════════════════════════════════════════════════════════════════

    def _enum_worker(self):
        try:    cams = list_cameras()
        except: cams = []
        try:    mons = list_monitors()
        except: mons = []
        try:    auds = list_audio_devices()
        except: auds = []
        self._enum_queue.put((cams, mons, auds))

    def _poll_enum(self):
        try:
            cams, mons, auds = self._enum_queue.get_nowait()
        except pyqueue.Empty:
            self.after(120, self._poll_enum); return

        self.cameras       = cams
        self.audio_devices = auds
        self._fill_monitor_menu(mons)
        self._build_camera_tiles(cams)
        n = len(cams)
        self.status_var.set(
            f"Ready — {n} source(s) detected." if n
            else "Ready — no cameras detected. Screen capture is still available.")

    def _fill_monitor_menu(self, monitors):
        for w in self._mon_menu_frame.winfo_children():
            w.destroy()
        labels = [f"Monitor {i}: {m['width']}×{m['height']}"
                  for i, m in monitors if i != 0]
        self.monitor_map = {f"Monitor {i}: {m['width']}×{m['height']}": i
                             for i, m in monitors if i != 0}
        if labels:
            self.monitor_choice.set(labels[0])
            ttk.OptionMenu(self._mon_menu_frame, self.monitor_choice,
                           labels[0], *labels).pack(anchor="w")
        else:
            self.monitor_choice.set("No displays found")

    # ══════════════════════════════════════════════════════════════════════
    # TILE MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════

    def _build_camera_tiles(self, cams):
        builtin = None
        externals = []
        for entry in cams:
            index, cam_name, backend = entry
            if is_builtin_camera(cam_name) or (builtin is None and index == 0 and len(cams) > 1):
                if builtin is None:
                    builtin = entry
                    continue
            externals.append(entry)

        # If no name-match and only one camera total, treat index 0 as external
        if builtin is None and len(cams) == 1:
            externals = cams

        # Update built-in camera checkbox
        self._builtin_info = builtin
        if builtin:
            _, bname, _ = builtin
            self._builtin_label.config(text=_clean_label(bname))
            self._builtin_chk.config(state=tk.NORMAL)
        else:
            self._builtin_label.config(text="None detected")
            self._builtin_chk.config(state=tk.DISABLED)

        # External cameras → tiles immediately
        for index, cam_name, backend in externals:
            safe = f"cam_{index}_{cam_name}".replace(" ", "_")
            if safe in self.preview_sources:
                continue
            try:
                src = CameraCaptureThread(index, cam_name, backend=backend)
                src.start()
            except Exception as e:
                print(f"[preview start failed {safe}] {e}"); continue
            self.preview_sources[safe] = src
            self._add_tile(safe, src)

        if self.screen_var.get() and "screen" not in self.preview_sources:
            self._toggle_screen()
        self._relayout()

    def _add_tile(self, name, src):
        """Build one tile: dark header (drag + title + ✕) + preview + controls + settings."""
        cap_w = getattr(src, "actual_width",  0) or 0
        cap_h = getattr(src, "actual_height", 0) or 0
        clean = _clean_label(name)

        outer = tk.Frame(self.tile_area, relief="solid", bd=1, bg="#1a1a2e")
        outer.columnconfigure(0, weight=1)

        # ── dark header: drag handle + title + ✕ ─────────────────────────
        HDR = "#2c3e50"
        header = tk.Frame(outer, bg=HDR, cursor="fleur")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        drag_lbl = tk.Label(header, text="⠿", bg=HDR, fg="#7f8c8d",
                            font=("Segoe UI", 11), cursor="fleur")
        drag_lbl.grid(row=0, column=0, padx=(5, 2), pady=3)

        title_lbl = tk.Label(header, text=clean, bg=HDR, fg="white",
                             font=("Segoe UI", 9, "bold"), anchor="w",
                             cursor="fleur")
        title_lbl.grid(row=0, column=1, sticky="ew", padx=2, pady=3)

        max_btn = tk.Button(header, text="⊞", bg=HDR, fg="#bdc3c7",
                            activebackground="#2980b9", activeforeground="white",
                            relief="flat", bd=0,
                            font=("Segoe UI", 10),
                            command=lambda n=name: self._toggle_maximize(n))
        max_btn.grid(row=0, column=2, padx=(2, 2), pady=3)

        close_btn = tk.Button(header, text="✕", bg=HDR, fg="#e74c3c",
                              activebackground="#c0392b", activeforeground="white",
                              relief="flat", bd=0,
                              font=("Segoe UI", 10, "bold"),
                              command=lambda n=name: self._remove_tile(n))
        close_btn.grid(row=0, column=3, padx=(2, 5), pady=3)

        # Bind drag events to the header and its children (except close btn)
        for w in (header, drag_lbl, title_lbl):
            w.bind("<ButtonPress-1>",   lambda e, n=name: self._drag_start(e, n))
            w.bind("<B1-Motion>",        self._drag_motion)
            w.bind("<ButtonRelease-1>",  self._drag_end)

        # ── live preview ─────────────────────────────────────────────────
        img_lbl = tk.Label(outer, bg="#111111", anchor="center")
        img_lbl.grid(row=1, column=0, padx=2, pady=2, sticky="nsew")

        # ── record / pause buttons ────────────────────────────────────────
        btn_row = ttk.Frame(outer)
        btn_row.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 0))
        btn_row.columnconfigure(0, weight=3)
        btn_row.columnconfigure(1, weight=2)

        rec_btn = tk.Button(btn_row, text="● Record",
                            bg=IDLE_BG, fg=IDLE_FG, relief="flat",
                            font=("Segoe UI", 9, "bold"),
                            command=lambda n=name: self._toggle_record(n))
        rec_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        pause_btn = tk.Button(btn_row, text="⏸ Pause",
                              bg="#e67e22", fg="white", relief="flat",
                              font=("Segoe UI", 9, "bold"),
                              command=lambda n=name: self._toggle_pause(n))
        pause_btn.grid(row=0, column=1, sticky="ew")
        pause_btn.grid_remove()

        # ── status line ───────────────────────────────────────────────────
        status_lbl = ttk.Label(outer, text="Ready",
                               foreground="#888", font=("Segoe UI", 8, "bold"),
                               anchor="center")
        status_lbl.grid(row=3, column=0, sticky="ew", padx=4, pady=(1, 0))

        # ── settings panel ────────────────────────────────────────────────
        panel = ttk.Frame(outer, padding=(6, 4, 6, 6))
        panel.grid(row=4, column=0, sticky="ew")
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(3, weight=1)

        label_var  = tk.StringVar(value=clean)
        default_res = f"{cap_w}x{cap_h}" if cap_w and cap_h else ""
        res_var    = tk.StringVar(value=default_res)
        fps_var    = tk.StringVar(value="30")
        qual_var   = tk.StringVar(value="Balanced (default)")
        audio_en   = tk.BooleanVar(value=False)
        audio_dev  = tk.StringVar(value="")
        limit_en   = tk.BooleanVar(value=False)
        limit_mins = tk.StringVar(value="60")

        r = 0
        ttk.Label(panel, text="Label:", anchor="e").grid(
            row=r, column=0, sticky="e", pady=1)
        ttk.Entry(panel, textvariable=label_var).grid(
            row=r, column=1, columnspan=3, sticky="ew", padx=(4, 0), pady=1)
        r += 1

        ttk.Label(panel, text="Resolution:", anchor="e").grid(
            row=r, column=0, sticky="e", pady=1)
        ttk.Entry(panel, textvariable=res_var, width=14).grid(
            row=r, column=1, sticky="ew", padx=(4, 4), pady=1)
        ttk.Label(panel, text="FPS:", anchor="e").grid(
            row=r, column=2, sticky="e", pady=1)
        ttk.Entry(panel, textvariable=fps_var, width=5).grid(
            row=r, column=3, sticky="ew", padx=(4, 0), pady=1)
        r += 1

        ttk.Label(panel, text="Quality:", anchor="e").grid(
            row=r, column=0, sticky="e", pady=1)
        ttk.Combobox(panel, textvariable=qual_var,
                     values=list(QUAL_OPTIONS), state="readonly",
                     width=22).grid(row=r, column=1, columnspan=3,
                                     sticky="ew", padx=(4, 0), pady=1)
        r += 1

        ttk.Label(panel, text="Microphone:", anchor="e").grid(
            row=r, column=0, sticky="e", pady=1)
        mic_row = ttk.Frame(panel)
        mic_row.grid(row=r, column=1, columnspan=3, sticky="ew",
                     padx=(4, 0), pady=1)
        ttk.Checkbutton(mic_row, variable=audio_en).pack(side=tk.LEFT)
        aud_labels = ["(select device)"] + self.audio_devices
        ttk.Combobox(mic_row, textvariable=audio_dev, values=aud_labels,
                     state="readonly", width=20).pack(
                         side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
        if aud_labels:
            audio_dev.set(aud_labels[0])
        r += 1

        ttk.Label(panel, text="Auto-Stop:", anchor="e").grid(
            row=r, column=0, sticky="e", pady=1)
        stop_row = ttk.Frame(panel)
        stop_row.grid(row=r, column=1, columnspan=3, sticky="ew",
                      padx=(4, 0), pady=1)
        ttk.Checkbutton(stop_row, variable=limit_en).pack(side=tk.LEFT)
        ttk.Entry(stop_row, textvariable=limit_mins, width=5).pack(
            side=tk.LEFT, padx=(2, 2))
        ttk.Label(stop_row, text="minutes").pack(side=tk.LEFT)

        # ── info line ─────────────────────────────────────────────────────
        info_lbl = ttk.Label(outer, text=_make_info(name, cap_w, cap_h),
                              font=("Segoe UI", 7), foreground="#777",
                              anchor="center")
        info_lbl.grid(row=5, column=0, sticky="ew", padx=4, pady=(0, 4))

        self.tiles[name] = {
            "frame": outer, "header": header, "title_lbl": title_lbl,
            "max_btn": max_btn,
            "img_lbl": img_lbl, "rec_btn": rec_btn, "pause_btn": pause_btn,
            "status_lbl": status_lbl, "info_lbl": info_lbl, "panel": panel,
            "src_w": cap_w, "src_h": cap_h,
            "label_var": label_var, "res_var": res_var, "fps_var": fps_var,
            "qual_var": qual_var, "audio_en": audio_en, "audio_dev": audio_dev,
            "limit_en": limit_en, "limit_mins": limit_mins, "_limit_job": None,
        }

    def _toggle_maximize(self, name):
        """Expand one tile to fill the entire tile area, or restore the grid."""
        HDR_NORMAL   = "#2c3e50"
        HDR_MAXIMIZE = "#1a5276"
        if self._maximized_tile == name:
            # Restore grid
            self._maximized_tile = None
            tile = self.tiles.get(name)
            if tile:
                tile["max_btn"].config(text="⊞")
                self._set_header_color(name, HDR_NORMAL)
            for n, t in self.tiles.items():
                t["frame"].grid()
            self._relayout()
        else:
            # Collapse any previous maximized tile first
            if self._maximized_tile:
                prev = self.tiles.get(self._maximized_tile)
                if prev:
                    prev["max_btn"].config(text="⊞")
                    self._set_header_color(self._maximized_tile, HDR_NORMAL)
            self._maximized_tile = name
            tile = self.tiles.get(name)
            if tile:
                tile["max_btn"].config(text="⊟")
                self._set_header_color(name, HDR_MAXIMIZE)
            # Hide all other tiles, let this one fill the area
            for n, t in self.tiles.items():
                if n != name:
                    t["frame"].grid_remove()
                else:
                    for c in range(8): self.tile_area.columnconfigure(c, weight=0)
                    for r in range(8): self.tile_area.rowconfigure(r, weight=0)
                    self.tile_area.columnconfigure(0, weight=1)
                    self.tile_area.rowconfigure(0, weight=1)
                    t["frame"].grid(row=0, column=0, sticky="nsew",
                                    padx=4, pady=4)
            self.update_idletasks()
            self._cell_w = max(200, self.tile_area.winfo_width()  - 16)
            self._cell_h = max(150, self.tile_area.winfo_height() - self.FIXED_ROWS_H - 16)

    def _remove_tile(self, name):
        # If this tile is maximized, restore grid before removing
        if self._maximized_tile == name:
            self._maximized_tile = None
            for t in self.tiles.values():
                t["frame"].grid()
        if self.session and self.session.is_recording(name):
            self.session.stop_writer(name)
        src = self.preview_sources.pop(name, None)
        if src: src.stop()
        tile = self.tiles.pop(name, None)
        if tile: tile["frame"].destroy()
        self._fps_track.pop(name, None)
        self._relayout()
        self._update_global_btns()

    def _toggle_builtin(self):
        """Add or remove the built-in webcam tile."""
        if not self._builtin_info:
            return
        index, cam_name, backend = self._builtin_info
        safe = f"cam_{index}_{cam_name}".replace(" ", "_")
        if self.builtin_var.get():
            if safe not in self.preview_sources:
                try:
                    src = CameraCaptureThread(index, cam_name, backend=backend)
                    src.start()
                except Exception as e:
                    messagebox.showerror("Built-in camera failed", str(e))
                    self.builtin_var.set(False)
                    return
                self.preview_sources[safe] = src
                self._add_tile(safe, src)
                self._relayout()
        else:
            self._remove_tile(safe)

    # ── drag-and-drop tile reordering ─────────────────────────────────────

    def _drag_start(self, event, name):
        self._drag_source = name
        self._set_header_color(name, "#1a5276")  # blue tint = "picked up"

    def _drag_motion(self, event):
        if not self._drag_source:
            return
        rx = event.widget.winfo_rootx() + event.x
        ry = event.widget.winfo_rooty() + event.y
        target = self._tile_at(rx, ry)
        if target != self._drag_over:
            # Un-highlight old target
            if self._drag_over and self._drag_over != self._drag_source:
                self._set_header_color(self._drag_over, "#2c3e50")
            # Highlight new target
            if target and target != self._drag_source:
                self._set_header_color(target, "#117a65")  # green = valid drop
            self._drag_over = target

    def _drag_end(self, event):
        if not self._drag_source:
            return
        rx = event.widget.winfo_rootx() + event.x
        ry = event.widget.winfo_rooty() + event.y
        target = self._tile_at(rx, ry)
        # Restore all header colours
        for n in self.tiles:
            self._set_header_color(n, "#2c3e50")
        if target and target != self._drag_source:
            self._swap_tiles(self._drag_source, target)
        self._drag_source = None
        self._drag_over   = None

    def _tile_at(self, root_x, root_y):
        """Return the tile name whose frame contains (root_x, root_y)."""
        for name, tile in self.tiles.items():
            f = tile["frame"]
            try:
                fx, fy = f.winfo_rootx(), f.winfo_rooty()
                fw, fh = f.winfo_width(), f.winfo_height()
                if fx <= root_x <= fx + fw and fy <= root_y <= fy + fh:
                    return name
            except tk.TclError:
                pass
        return None

    def _set_header_color(self, name, color):
        tile = self.tiles.get(name)
        if not tile:
            return
        hdr = tile.get("header")
        if not hdr:
            return
        hdr.config(bg=color)
        for child in hdr.winfo_children():
            if isinstance(child, tk.Label):
                child.config(bg=color)

    def _swap_tiles(self, name_a, name_b):
        """Swap two tile positions in the ordered dict and relayout."""
        keys = list(self.tiles.keys())
        if name_a not in keys or name_b not in keys:
            return
        i, j = keys.index(name_a), keys.index(name_b)
        keys[i], keys[j] = keys[j], keys[i]
        self.tiles = {k: self.tiles[k] for k in keys}
        self._relayout()

    # ── grid layout ───────────────────────────────────────────────────────
    FIXED_ROWS_H = 155   # header + rec btn + status + settings + info

    def _relayout(self):
        n = len(self.tiles)
        if n == 0: return
        # If a tile is maximized, keep it expanded; don't re-grid others
        if self._maximized_tile and self._maximized_tile in self.tiles:
            self._toggle_maximize(self._maximized_tile)   # restore then re-maximize
            return

        self.update_idletasks()
        aw = max(self.tile_area.winfo_width(),  600)
        ah = max(self.tile_area.winfo_height(), 400)

        # Best column count: maximise preview image area
        avg_r = _avg_ratio(self.tiles)
        best_cols, best_area = 1, 0
        PAD = 8
        for cols in range(1, n + 1):
            rows  = math.ceil(n / cols)
            cw    = (aw - PAD * (cols + 1)) / cols
            ch    = (ah - PAD * (rows + 1)) / rows
            ih    = max(1, ch - self.FIXED_ROWS_H)
            iw    = min(cw, ih * avg_r)
            score = iw * ih * n
            if score > best_area:
                best_area = score; best_cols = cols

        nc = best_cols
        nr = math.ceil(n / nc)

        cw = (aw - PAD * (nc + 1)) // nc
        ch = (ah - PAD * (nr + 1)) // nr

        self._cell_w = cw
        self._cell_h = max(1, ch - self.FIXED_ROWS_H)

        for c in range(nc): self.tile_area.columnconfigure(c, weight=1)
        for r in range(nr): self.tile_area.rowconfigure(r,    weight=1)

        for i, (name, tile) in enumerate(self.tiles.items()):
            tile["frame"].grid(row=i // nc, column=i % nc,
                                padx=PAD // 2, pady=PAD // 2, sticky="nsew")

    # ══════════════════════════════════════════════════════════════════════
    # SCREEN SOURCE
    # ══════════════════════════════════════════════════════════════════════

    def _toggle_screen(self):
        if self.screen_var.get():
            mon_idx = self.monitor_map.get(self.monitor_choice.get(), 1)
            try:
                src = ScreenCaptureThread(monitor_index=mon_idx, fps=30)
                src.start()
                for _ in range(50):
                    if src.actual_width is not None: break
                    time.sleep(0.05)
                if src.actual_width is None:
                    src.stop()
                    messagebox.showerror("Screen capture failed",
                                          "Could not read the selected display.")
                    self.screen_var.set(False); return
            except Exception as e:
                messagebox.showerror("Screen capture failed", str(e))
                self.screen_var.set(False); return
            self.preview_sources["screen"] = src
            self._add_tile("screen", src)
            self._relayout()
        else:
            self._remove_tile("screen")

    # ══════════════════════════════════════════════════════════════════════
    # RECORDING
    # ══════════════════════════════════════════════════════════════════════

    def _ensure_session(self):
        if self.session is None:
            nm = self.session_name.get().strip() or \
                 time.strftime("session_%Y%m%d_%H%M%S")
            self.session = RecordingSession(self.out_dir.get(), nm)
        return self.session

    def _get_tile_settings(self, name):
        t = self.tiles[name]
        # Parse free-text resolution: "1280x720", "1280×720", "1280 720", etc.
        rw, rh = 0, 0
        res_raw = t["res_var"].get().strip().replace("×", "x").replace(" ", "x")
        parts = [p for p in res_raw.split("x") if p.isdigit()]
        if len(parts) == 2:
            rw, rh = int(parts[0]), int(parts[1])

        fps_str  = t["fps_var"].get().strip()
        qual_key = t["qual_var"].get()
        qual     = QUAL_OPTIONS.get(qual_key, "balanced")
        audio    = (t["audio_dev"].get()
                    if t["audio_en"].get() and
                       t["audio_dev"].get() not in ("", "(select device)")
                    else None)
        return {
            "output_label":   t["label_var"].get().strip() or name,
            "out_width":      rw or None,
            "out_height":     rh or None,
            "fps_override":   int(fps_str) if fps_str.isdigit() else None,
            "quality_preset": qual,
            "use_hardware":   self.hw_var.get(),
            "audio_device":   audio,
        }

    def _toggle_record(self, name, shared_t0=None):
        sess = self._ensure_session()
        if sess.is_recording(name):
            self._cancel_limit(name)
            # Resume first so the writer's run-loop exits its pause spin-wait
            # immediately rather than waiting for the next 20 ms tick.
            if sess.is_paused(name):
                sess.resume_writer(name)
            # Update UI immediately — don't block on encoder shutdown
            self._tile_stopping(name)
            self._update_global_btns()
            # Encoder drain + file-close happens in a background thread
            threading.Thread(
                target=self._do_stop, args=(name, sess), daemon=True
            ).start()
        else:
            src = self.preview_sources.get(name)
            if src is None:
                return
            kwargs = self._get_tile_settings(name)
            if shared_t0 is not None:
                kwargs["shared_t0"] = shared_t0
            try:
                sess.start_writer(name, src, **kwargs)
            except Exception as e:
                messagebox.showerror("Could not start recording", str(e))
                return
            self._tile_recording(name)
            self._update_global_btns()
            self._arm_limit(name)

    def _do_stop(self, name, sess):
        """Runs in a background thread — blocks until ffmpeg finishes writing."""
        summary = sess.stop_writer(name)
        # Schedule UI update back on the main thread
        self.after(0, lambda: self._after_stop(name, summary))

    def _after_stop(self, name, summary):
        self._tile_idle(name)
        self._update_global_btns()
        if summary:
            self._show_summary(name, summary)

    def _arm_limit(self, name):
        tile = self.tiles.get(name)
        if not tile or not tile["limit_en"].get():
            return
        try:
            mins = float(tile["limit_mins"].get())
        except ValueError:
            return
        ms = int(mins * 60 * 1000)
        job = self.after(ms, lambda: self._auto_stop(name))
        tile["_limit_job"] = job

    def _auto_stop(self, name):
        if self.session and self.session.is_recording(name):
            self._toggle_record(name)

    def _cancel_limit(self, name):
        tile = self.tiles.get(name)
        if tile and tile["_limit_job"]:
            self.after_cancel(tile["_limit_job"])
            tile["_limit_job"] = None

    def _tile_stopping(self, name):
        tile = self.tiles.get(name)
        if tile:
            tile["rec_btn"].config(text="Saving…", bg="#7f8c8d", fg="white",
                                   state=tk.DISABLED)
            tile["pause_btn"].grid_remove()
            tile["status_lbl"].config(text="Finalising file…",
                                       foreground="#7f8c8d")

    def _tile_recording(self, name):
        tile = self.tiles.get(name)
        if tile:
            tile["rec_btn"].config(text="■ Stop", bg=REC_BG, fg=REC_FG,
                                   state=tk.NORMAL)
            tile["status_lbl"].config(text="● 00:00  /  0 frames",
                                       foreground=REC_BG)
            tile["pause_btn"].grid()

    def _tile_idle(self, name):
        tile = self.tiles.get(name)
        if tile:
            tile["rec_btn"].config(text="● Record", bg=IDLE_BG, fg=IDLE_FG,
                                   state=tk.NORMAL)
            tile["status_lbl"].config(text="Ready", foreground="#888")
            tile["pause_btn"].grid_remove()

    def _toggle_pause(self, name):
        if not self.session: return
        if self.session.is_paused(name):
            self.session.resume_writer(name)
            tile = self.tiles.get(name)
            if tile:
                tile["pause_btn"].config(text="⏸ Pause", bg="#e67e22")
                tile["status_lbl"].config(text="Recording…", foreground=REC_BG)
        else:
            self.session.pause_writer(name)
            tile = self.tiles.get(name)
            if tile:
                tile["pause_btn"].config(text="▶ Resume", bg="#2980b9")
                tile["status_lbl"].config(text="Paused", foreground="#2980b9")

    def _start_all(self):
        # Capture one shared timestamp so every source uses the same
        # frame-schedule reference clock — auto-alignable without fiddling.
        t0 = time.time()
        for n in list(self.tiles):
            if self.session is None or not self.session.is_recording(n):
                self._toggle_record(n, shared_t0=t0)

    def _stop_all(self):
        if self.session:
            for n in list(self.session.writers):
                self._toggle_record(n)

    def _update_global_btns(self):
        any_rec = self.session is not None and self.session.any_recording()
        self.stop_all_btn.config(
            state=tk.NORMAL if any_rec else tk.DISABLED)
        all_rec = (self.tiles and self.session is not None and
                   all(self.session.is_recording(n) for n in self.tiles))
        self.start_all_btn.config(
            state=tk.DISABLED if all_rec else tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════════
    # PREVIEW TICK
    # ══════════════════════════════════════════════════════════════════════

    def _tick_previews(self):
        now = time.time()
        rec_parts = []
        for name, src in list(self.preview_sources.items()):
            frame = src.get_preview_frame()
            if frame is not None:
                tile = self.tiles.get(name)
                if tile and (not tile["src_w"] or not tile["src_h"]):
                    w = getattr(src, "actual_width",  0) or 0
                    h = getattr(src, "actual_height", 0) or 0
                    if w and h:
                        tile["src_w"] = w; tile["src_h"] = h
                        tile["info_lbl"].config(text=_make_info(name, w, h))
                self._push_frame(name, frame)

            if self.session and self.session.is_recording(name):
                writer = self.session.writers.get(name)
                if writer and not writer.is_paused:
                    elapsed = now - writer.t0
                    mins, secs = divmod(int(elapsed), 60)

                    # Live FPS: frames written since last check (update every 0.75s)
                    trk = self._fps_track.get(name)
                    if trk is None:
                        self._fps_track[name] = {"t": now, "f": writer.frames_written, "fps": 0.0}
                        live_fps = 0.0
                    else:
                        dt = now - trk["t"]
                        if dt >= 0.75:
                            df = writer.frames_written - trk["f"]
                            live_fps = df / dt if dt > 0 else 0.0
                            self._fps_track[name] = {"t": now, "f": writer.frames_written, "fps": live_fps}
                        else:
                            live_fps = trk["fps"]

                    tile = self.tiles.get(name)
                    if tile:
                        tile["status_lbl"].config(
                            text=(f"● {mins:02d}:{secs:02d}"
                                  f"  |  {writer.frames_written} frames"
                                  f"  |  {live_fps:.1f} fps"),
                            foreground=REC_BG)
                    rec_parts.append(f"{name}: {mins:02d}:{secs:02d} @ {live_fps:.0f}fps")

        if rec_parts:
            self.status_var.set("Recording | " + "   ".join(rec_parts))
        self.after(PREVIEW_REFRESH_MS, self._tick_previews)

    def _push_frame(self, name, frame):
        tile = self.tiles.get(name)
        if not tile: return
        sw = tile["src_w"] or frame.shape[1]
        sh = tile["src_h"] or frame.shape[0]
        dw, dh = _fit(sw, sh, self._cell_w, self._cell_h)
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img   = Image.fromarray(rgb).resize((dw, dh), Image.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        lbl   = tile["img_lbl"]
        lbl.configure(image=photo, width=dw, height=dh)
        lbl.image = photo

    # ══════════════════════════════════════════════════════════════════════
    # RESIZE
    # ══════════════════════════════════════════════════════════════════════

    def _on_resize(self, event):
        if event.widget is not self: return
        if self._resize_job: self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._relayout)

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _browse(self):
        d = filedialog.askdirectory()
        if d: self.out_dir.set(d)

    def _show_summary(self, name, s):
        title = ("Saved — clean" if s["duplicate_frames"] == 0
                 else "Saved — some frame drops detected")
        audio_note = (f"\nAudio device: {s['audio_device']}"
                      if s.get("audio_device") and s["audio_device"] != "none"
                      else "")
        if s["duplicate_frames"] == 0:
            body = (f"Duration: {s['video_duration_seconds']}s  |  "
                    f"{s['frames_written']} frames  |  Codec: {s['codec_used']}"
                    f"{audio_note}")
        else:
            body = (f"{s['duplicate_pct']}% of frames repeated due to source stalls\n"
                    f"({s['total_dropped_seconds']}s total  |  "
                    f"worst freeze: {s['worst_single_freeze_seconds']}s)\n"
                    f"Duration still correct: {s['video_duration_seconds']}s"
                    f"{audio_note}")
        body += f"\n\nSaved to:\n{self.session.session_dir}"
        messagebox.showinfo(title, body)

    def _on_close(self):
        if self.session and self.session.any_recording():
            if not messagebox.askyesno("Recording in progress",
                                        "Stop all recordings and exit?"):
                return
            self.session.stop_all()
        for src in self.preview_sources.values():
            src.stop()
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════
# MODULE HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _make_info(name, w, h):
    display = _clean_label(name)
    if w and h:
        return f"{display}  |  {w}×{h}  |  {_aspect(w, h)}"
    return display

def _avg_ratio(tiles):
    rs = [t["src_w"] / t["src_h"]
          for t in tiles.values() if t["src_w"] and t["src_h"]]
    return (sum(rs) / len(rs)) if rs else 16/9


if __name__ == "__main__":
    App().mainloop()
