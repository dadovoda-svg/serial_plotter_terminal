#!/usr/bin/env python3
import argparse
import json
import os
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

import serial  # pip install pyserial

import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


NUM_RE = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


def is_number(s: str) -> bool:
    return bool(NUM_RE.match(s.strip()))


def parse_line(line: str):
    """
    Robust parser for:
      - "T:90.00 M:89.91"
      - "T=90.00,M=89.91"
      - numeric lists: "1,2,3" or "1 2 3"
    Returns (labels, values) or None.
    """
    line = line.strip()
    if not line:
        return None

    pairs = re.findall(
        r'([A-Za-z_]\w*)\s*[:=]\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)',
        line
    )
    if pairs:
        labels = [k for k, _ in pairs]
        values = [float(v) for _, v in pairs]
        return labels, values

    norm = line.replace(",", " ").replace("\t", " ").replace(";", " ")
    tokens = [t for t in norm.split() if t]
    vals = [float(t) for t in tokens if is_number(t)]
    if vals:
        labels = [f"v{i}" for i in range(len(vals))]
        return labels, vals

    return None


class QuickStore:
    """
    Simple persistence for the 5 quick commands.
    Stored as JSON.
    """
    def __init__(self, path: Path, slots: int = 5):
        self.path = path
        self.slots = slots

    def load(self):
        if not self.path.exists():
            return [""] * self.slots
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "quick" in data and isinstance(data["quick"], list):
                lst = [str(x) for x in data["quick"][: self.slots]]
            elif isinstance(data, list):
                lst = [str(x) for x in data[: self.slots]]
            else:
                lst = []
            lst += [""] * (self.slots - len(lst))
            return lst
        except Exception:
            return [""] * self.slots

    def save(self, values):
        values = list(values)[: self.slots]
        values += [""] * (self.slots - len(values))
        payload = {"quick": values}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class ReconnectingSerial(threading.Thread):
    """
    Serial thread that keeps trying to connect.
    It never crashes the app:
      - starts offline if port is missing
      - reconnects when the port appears
      - goes offline if disconnected, then retries

    Events sent to out_queue:
      ("__STATUS__", {"online": bool, "msg": str})
      ("__LINE__", "...")
      ("__ERROR__", "...")
    """
    def __init__(self, port: str, baud: int, out_queue: "queue.Queue", stop_event: threading.Event,
                 retry_s: float = 1.0, read_timeout_s: float = 0.2):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.retry_s = max(0.2, float(retry_s))
        self.read_timeout_s = float(read_timeout_s)

        self._ser = None
        self._lock = threading.Lock()
        self._online = False

    def is_online(self) -> bool:
        return self._online

    def _set_status(self, online: bool, msg: str):
        self._online = online
        self._put(("__STATUS__", {"online": online, "msg": msg}))

    def _put(self, item):
        try:
            self.out_queue.put_nowait(item)
        except queue.Full:
            pass

    def _close_serial(self):
        with self._lock:
            try:
                if self._ser:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _try_open(self) -> bool:
        try:
            ser = serial.Serial(self.port, self.baud, timeout=self.read_timeout_s)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            with self._lock:
                self._ser = ser
            self._set_status(True, f"ONLINE: {self.port} @ {self.baud}")
            return True
        except Exception as e:
            self._set_status(False, f"OFFLINE: {self.port} ({e})")
            return False

    def run(self):
        # Start offline; attempt open in loop
        self._set_status(False, f"OFFLINE: {self.port} (not connected yet)")

        while not self.stop_event.is_set():
            # Ensure we have an open port
            if self._ser is None:
                ok = self._try_open()
                if not ok:
                    time.sleep(self.retry_s)
                    continue

            # Read loop when connected
            try:
                with self._lock:
                    ser = self._ser
                if ser is None:
                    continue

                raw = ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._put(("__LINE__", line))

            except Exception as e:
                # Disconnected or read error => go offline and retry
                self._put(("__ERROR__", f"Serial error: {e}"))
                self._close_serial()
                self._set_status(False, f"OFFLINE: {self.port} (disconnected)")
                time.sleep(self.retry_s)

        # Shutdown
        self._close_serial()
        self._set_status(False, "OFFLINE: stopped")

    def write_line(self, s: str) -> bool:
        """
        Returns True if sent, False if offline/failure.
        """
        s = s.rstrip("\r\n")
        if not s.endswith("\n"):
            s += "\n"
        data = s.encode("utf-8", errors="replace")

        with self._lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write(data)
                return True
            except Exception as e:
                self._put(("__ERROR__", f"Write error: {e}"))
                # force offline
                try:
                    ser.close()
                except Exception:
                    pass
                self._ser = None
                self._set_status(False, f"OFFLINE: {self.port} (write failed)")
                return False


class App:
    def __init__(self, root, sio: ReconnectingSerial, rx_queue: "queue.Queue", prefix: str,
                 window_s: float, max_series: int, ylim_auto: bool, store: QuickStore):
        self.root = root
        self.sio = sio
        self.rx_queue = rx_queue
        self.prefix = prefix
        self.window_s = window_s
        self.max_series = max_series
        self.ylim_auto = ylim_auto
        self.store = store

        # Plot control
        self.plot_running = True
        self.t0 = time.time()
        self.paused_total = 0.0
        self.paused_started_at = None

        # Data
        self.series = {}    # name -> (deque_t, deque_y)
        self.lines = {}     # name -> matplotlib line2D

        # UI
        self.quick_entries = []
        self._save_after_id = None
        self._build_ui()
        self._load_quick_commands()

        # Start offline indicator until status event arrives
        self._set_online_ui(False, "OFFLINE")

        # periodic processing
        self.root.after(20, self._tick)

        # Save on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.root.title("Serial Plotter + Terminal")

        # Top: toolbar
        top = ttk.Frame(self.root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)

        self.btn_start = ttk.Button(top, text="Start", command=self._on_start)
        self.btn_stop = ttk.Button(top, text="Stop", command=self._on_stop)
        self.btn_clear = ttk.Button(top, text="Clear", command=self._on_clear)

        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_clear.pack(side=tk.LEFT, padx=(0, 12))

        # Status text + semaphore
        self.status = ttk.Label(top, text="OFFLINE")
        self.status.pack(side=tk.LEFT)

        self.sema = tk.Canvas(top, width=18, height=18, highlightthickness=0)
        self.sema.pack(side=tk.LEFT, padx=(8, 0))
        self._sema_circle = self.sema.create_oval(3, 3, 15, 15, outline="", fill="red")

        # Middle: plot
        mid = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.fig = Figure(figsize=(8, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Value")
        self.ax.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, master=mid)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Bottom: terminal + input + quick buttons
        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(side=tk.BOTTOM, fill=tk.BOTH)

        # Terminal
        term_frame = ttk.Frame(bottom)
        term_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.term = tk.Text(term_frame, height=10, wrap="none")
        self.term.configure(state="disabled")
        self.term.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(term_frame, orient="vertical", command=self.term.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.term.configure(yscrollcommand=yscroll.set)

        # Single-line input
        input_frame = ttk.Frame(bottom)
        input_frame.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))

        ttk.Label(input_frame, text="> ").pack(side=tk.LEFT)
        self.entry = ttk.Entry(input_frame)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_enter)

        # Quick-send rows (5)
        quick_frame = ttk.LabelFrame(bottom, text="Quick Send", padding=6)
        quick_frame.pack(side=tk.TOP, fill=tk.X, pady=(6, 0))
        quick_frame.columnconfigure(0, weight=1)

        for i in range(5):
            row = ttk.Frame(quick_frame)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.columnconfigure(0, weight=1)

            e = ttk.Entry(row)
            e.grid(row=0, column=0, sticky="ew")
            e.bind("<KeyRelease>", self._on_quick_modified)

            b = ttk.Button(row, text=f"Send {i+1}", command=lambda idx=i: self._on_quick_send(idx))
            b.grid(row=0, column=1, padx=(6, 0))

            e.bind("<Control-Return>", lambda _evt, idx=i: self._on_quick_send(idx))
            self.quick_entries.append(e)

        self.entry.focus_set()

    def _set_online_ui(self, online: bool, msg: str):
        self.status.configure(text=msg)
        self.sema.itemconfigure(self._sema_circle, fill=("green" if online else "red"))

    def _append_terminal(self, line: str):
        self.term.configure(state="normal")
        self.term.insert("end", line + "\n")
        self.term.see("end")
        self.term.configure(state="disabled")

    def _send_to_serial(self, s: str, echo: bool = True):
        s = s.rstrip("\r\n")
        if not s.strip():
            return

        # local echo first (you asked to keep it)
        if echo:
            self._append_terminal(f"> {s}")

        ok = self.sio.write_line(s)
        if not ok:
            self._append_terminal("[offline] not sent (serial not connected)")

    # --- Quick commands persistence ---
    def _load_quick_commands(self):
        values = self.store.load()
        for i, e in enumerate(self.quick_entries):
            e.delete(0, "end")
            e.insert(0, values[i] if i < len(values) else "")

    def _save_quick_commands(self):
        values = [e.get() for e in self.quick_entries]
        self.store.save(values)

    def _on_quick_modified(self, _evt=None):
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.root.after(600, self._save_quick_commands)

    def _on_close(self):
        try:
            self._save_quick_commands()
        except Exception:
            pass
        self.root.destroy()

    # --- Plot timebase ---
    def _now_rel(self):
        return time.time() - self.t0 - self.paused_total

    def _ensure_series(self, name: str):
        if name in self.series:
            return
        if len(self.series) >= self.max_series:
            return

        self.series[name] = (deque(), deque())
        (ln,) = self.ax.plot([], [], label=name)
        self.lines[name] = ln
        self.ax.legend(loc="upper left")

    def _prune_old(self, rel_t: float):
        cutoff = rel_t - self.window_s
        for dq_t, dq_y in self.series.values():
            while dq_t and dq_t[0] < cutoff:
                dq_t.popleft()
                dq_y.popleft()

    def _autoscale_y(self):
        ymin = None
        ymax = None
        for _, dq_y in self.series.values():
            if not dq_y:
                continue
            y0 = min(dq_y)
            y1 = max(dq_y)
            ymin = y0 if ymin is None else min(ymin, y0)
            ymax = y1 if ymax is None else max(ymax, y1)

        if ymin is None or ymax is None:
            return
        if ymin == ymax:
            pad = 1.0 if ymin == 0 else abs(ymin) * 0.1
        else:
            pad = (ymax - ymin) * 0.1
        self.ax.set_ylim(ymin - pad, ymax + pad)

    def _redraw_plot(self):
        for name, (dq_t, dq_y) in self.series.items():
            self.lines[name].set_data(list(dq_t), list(dq_y))

        rel_t = self._now_rel()
        self.ax.set_xlim(max(0.0, rel_t - self.window_s), max(self.window_s, rel_t))
        if self.ylim_auto:
            self._autoscale_y()
        self.canvas.draw_idle()

    # --- Buttons ---
    def _on_stop(self):
        if not self.plot_running:
            return
        self.plot_running = False
        self.paused_started_at = time.time()

    def _on_start(self):
        if self.plot_running:
            return
        if self.paused_started_at is not None:
            self.paused_total += (time.time() - self.paused_started_at)
        self.paused_started_at = None
        self.plot_running = True

    def _on_clear(self):
        for dq_t, dq_y in self.series.values():
            dq_t.clear()
            dq_y.clear()
        self.t0 = time.time()
        self.paused_total = 0.0
        self.paused_started_at = None
        self._redraw_plot()

    # --- Main entry send ---
    def _on_enter(self, _evt):
        s = self.entry.get()
        self._send_to_serial(s, echo=True)
        self.entry.delete(0, tk.END)

    # --- Quick sends ---
    def _on_quick_send(self, idx: int):
        if idx < 0 or idx >= len(self.quick_entries):
            return
        s = self.quick_entries[idx].get()
        self._send_to_serial(s, echo=True)

    def _tick(self):
        """
        - Terminal shows ONLY non-plot lines (i.e., lines NOT starting with prefix)
        - Plot uses ONLY lines starting with prefix (after stripping it)
        - If plot is stopped, plot samples are discarded
        - Handles online/offline status events
        """
        changed_plot = False

        while True:
            try:
                kind, payload = self.rx_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "__STATUS__":
                online = bool(payload.get("online", False))
                msg = str(payload.get("msg", ""))
                self._set_online_ui(online, msg)
                continue

            if kind == "__ERROR__":
                # Show errors in terminal (non-plot channel)
                self._append_terminal(payload)
                continue

            if kind == "__LINE__":
                line = payload

                if line.startswith(self.prefix):
                    plot_line = line[len(self.prefix):].strip()
                    parsed = parse_line(plot_line)
                    if not parsed:
                        continue

                    if not self.plot_running:
                        continue

                    rel_t = self._now_rel()
                    labels, values = parsed
                    for name, val in zip(labels, values):
                        self._ensure_series(name)
                        if name not in self.series:
                            continue
                        dq_t, dq_y = self.series[name]
                        dq_t.append(rel_t)
                        dq_y.append(val)
                        changed_plot = True
                else:
                    self._append_terminal(line)

        if changed_plot and self.plot_running:
            self._prune_old(self._now_rel())
            self._redraw_plot()

        self.root.after(20, self._tick)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Serial Plotter (prefix-filtered) + Serial Terminal + Quick Send + Auto-reconnect."
    )
    ap.add_argument("--port", required=True, help="Serial port (e.g., /dev/ttyACM0 or COM5)")
    ap.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    ap.add_argument("--window", type=float, default=10.0, help="Plot window seconds (default: 10)")
    ap.add_argument("--max-series", type=int, default=16, help="Max series (default: 16)")
    ap.add_argument("--prefix", default="@", help='Plot only lines starting with this prefix (default: "@")')
    ap.add_argument(
        "--ylim-auto",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-scale Y continuously (default: on). Use --no-ylim-auto to disable.",
    )
    ap.add_argument(
        "--quick-file",
        default="serial_plotter_terminal.quick.json",
        help="JSON file used to store quick commands (default: serial_plotter_terminal.quick.json)",
    )
    ap.add_argument(
        "--retry",
        type=float,
        default=1.0,
        help="Reconnect retry interval in seconds (default: 1.0)",
    )
    return ap.parse_args()


if __name__ == "__main__":
    args = parse_args()

    rxq = queue.Queue(maxsize=5000)
    stop_event = threading.Event()

    sio = ReconnectingSerial(
        port=args.port,
        baud=args.baud,
        out_queue=rxq,
        stop_event=stop_event,
        retry_s=args.retry,
        read_timeout_s=0.2,
    )
    sio.start()

    store = QuickStore(Path(args.quick_file), slots=5)

    root = tk.Tk()
    try:
        app = App(
            root=root,
            sio=sio,
            rx_queue=rxq,
            prefix=args.prefix,
            window_s=args.window,
            max_series=args.max_series,
            ylim_auto=args.ylim_auto,
            store=store,
        )
        root.mainloop()
    finally:
        stop_event.set()
        time.sleep(0.2)
