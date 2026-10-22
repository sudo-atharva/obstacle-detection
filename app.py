"""Obstacle Detection: slows down for obstacles, asks the pilot, stops if there is no answer.

Run:  python app.py              (webcam 0, simulated vehicle)
      python app.py --sim        (no camera: set obstacles with the GUI buttons)
Keys: Y = move forward / resume, N or Space = stop
"""
import argparse
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from controller import Config, Level, ObstacleController, State
from detector import ObstacleDetector, draw_overlay
from vehicle import SimulatedVehicle

VIDEO_W, VIDEO_H = 760, 570
CAMERA_LOST_AFTER = 1.0  # seconds without a frame before stopping for safety

STATE_COLORS = {
    State.CRUISING: "#1a7f37",
    State.AWAITING_PILOT: "#d4a017",
    State.PROCEEDING: "#e07b00",
    State.STOPPED: "#c62828",
}


class CameraWorker(threading.Thread):
    def __init__(self, source, detector, out_queue):
        super().__init__(daemon=True)
        self.source, self.detector, self.out_queue = source, detector, out_queue
        self.running = True

    def run(self):
        cap = cv2.VideoCapture(self.source)
        # Video files would otherwise play as fast as they decode; webcams pace themselves.
        frame_time = 1 / (cap.get(cv2.CAP_PROP_FPS) or 30) if isinstance(self.source, str) else 0
        while self.running and cap.isOpened():
            started = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                break
            level, boxes = self.detector.detect(frame)
            draw_overlay(frame, self.detector, boxes)
            try:
                self.out_queue.get_nowait()
            except queue.Empty:
                pass
            self.out_queue.put((frame, level, time.monotonic()))
            time.sleep(max(0, frame_time - (time.monotonic() - started)))
        cap.release()


class ObstacleApp:
    def __init__(self, root, source, sim, backend, config):
        self.root = root
        root.title("Obstacle Detection - Pilot Assist")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.controller = ObstacleController(config)
        self.vehicle = SimulatedVehicle()
        self.frames = queue.Queue(maxsize=1)
        self.camera_level = Level.CLEAR
        self.last_frame_time = time.monotonic()
        self.prev_state = None
        self.distance = 0.0

        self.mode = tk.StringVar(value="sim" if sim else "camera")
        self.sim_level = tk.StringVar(value="CLEAR")
        self.timeout_var = tk.DoubleVar(value=config.pilot_timeout)

        self._build_ui()
        self.worker = None
        if not sim:
            self.detector = ObstacleDetector(backend)
            self.worker = CameraWorker(source, self.detector, self.frames)
            self.worker.start()
            self.log(f"Camera started ({self.detector.name} detector)")
        else:
            self.log("Simulation mode: use the buttons to place obstacles")

        root.bind("<y>", lambda e: self.forward())
        root.bind("<Y>", lambda e: self.forward())
        root.bind("<n>", lambda e: self.stop())
        root.bind("<N>", lambda e: self.stop())
        root.bind("<space>", lambda e: self.stop())
        self.tick()

    # ---- layout --------------------------------------------------------
    def _build_ui(self):
        self.video = tk.Label(self.root, bg="black")
        self.video.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        self.show(self._placeholder("Starting camera…"))

        side = ttk.Frame(self.root, padding=8, width=330)
        side.grid(row=0, column=1, sticky="nsew")

        self.banner = tk.Label(side, text="", font=("Helvetica", 18, "bold"), fg="white",
                               width=22, height=2, wraplength=300)
        self.banner.pack(fill="x")
        self.reason = ttk.Label(side, text="", foreground="#c62828", font=("Helvetica", 11))
        self.reason.pack(pady=(4, 0))

        ttk.Label(side, text="Speed").pack(anchor="w", pady=(10, 0))
        self.speed_bar = ttk.Progressbar(side, maximum=100, length=300)
        self.speed_bar.pack(fill="x")
        self.speed_label = ttk.Label(side, text="0 %", font=("Helvetica", 12))
        self.speed_label.pack(anchor="e")

        # Question for the pilot (only active while waiting for an answer).
        self.prompt = tk.LabelFrame(side, text=" Pilot ", font=("Helvetica", 12, "bold"), padx=8, pady=8)
        self.prompt.pack(fill="x", pady=10)
        self.question = tk.Label(self.prompt, text="No action needed", font=("Helvetica", 13), wraplength=290)
        self.question.pack()
        self.countdown = ttk.Progressbar(self.prompt, maximum=100)
        self.countdown.pack(fill="x", pady=6)
        buttons = tk.Frame(self.prompt)
        buttons.pack(fill="x")
        self.yes_btn = tk.Button(buttons, text="▲ Move forward (Y)", bg="#1a7f37", fg="white",
                                 font=("Helvetica", 12, "bold"), command=self.forward, height=2)
        self.yes_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.no_btn = tk.Button(buttons, text="■ Stop (N)", bg="#c62828", fg="white",
                                font=("Helvetica", 12, "bold"), command=self.stop, height=2)
        self.no_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        source = ttk.LabelFrame(side, text=" Obstacle input ", padding=6)
        source.pack(fill="x")
        ttk.Radiobutton(source, text="Camera", variable=self.mode, value="camera").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(source, text="Simulated:", variable=self.mode, value="sim").grid(row=1, column=0, sticky="w")
        for i, name in enumerate(["CLEAR", "WARNING", "CRITICAL"]):
            ttk.Radiobutton(source, text=name.title(), variable=self.sim_level, value=name).grid(
                row=1, column=i + 1, sticky="w")

        settings = ttk.Frame(side)
        settings.pack(fill="x", pady=8)
        ttk.Label(settings, text="Pilot answer timeout (s):").pack(side="left")
        ttk.Spinbox(settings, from_=1, to=30, increment=1, width=5, textvariable=self.timeout_var).pack(side="left")

        ttk.Label(side, text="Event log").pack(anchor="w")
        self.log_box = tk.Listbox(side, height=8, font=("Courier", 9))
        self.log_box.pack(fill="both", expand=True)

    # ---- pilot actions -------------------------------------------------
    def forward(self):
        before = self.controller.state
        self.controller.pilot_forward()
        if self.controller.state != before:
            self.log("Pilot: move forward")
        elif before == State.STOPPED:
            self.log("Resume refused: obstacle too close")

    def stop(self):
        self.controller.pilot_stop()

    # ---- main loop (runs ~20 times per second) -------------------------
    def tick(self):
        try:
            self.controller.config.pilot_timeout = float(self.timeout_var.get())
        except (tk.TclError, ValueError):
            pass

        try:
            frame, self.camera_level, self.last_frame_time = self.frames.get_nowait()
            if self.mode.get() == "camera":
                self.show(frame)
        except queue.Empty:
            pass

        if self.mode.get() == "sim":
            level = Level[self.sim_level.get()]
            self.show(self._sim_view(level))
        else:
            level = self.camera_level
            if time.monotonic() - self.last_frame_time > CAMERA_LOST_AFTER:
                self.controller.emergency_stop("Camera feed lost")
                self.show(self._placeholder("NO CAMERA SIGNAL"))

        speed = self.controller.update(level)
        self.vehicle.set_speed(speed)
        self.distance += speed * 0.05
        self.refresh_panel()
        self.root.after(50, self.tick)

    def refresh_panel(self):
        c = self.controller
        if c.state != self.prev_state:
            self.log(c.state.value + (f": {c.stop_reason}" if c.stop_reason else ""))
            if c.state == State.AWAITING_PILOT:
                self.root.bell()  # audible alert for the pilot
            self.prev_state = c.state

        self.banner.config(text=c.state.value.upper(), bg=STATE_COLORS[c.state])
        self.reason.config(text=c.stop_reason)
        self.speed_bar["value"] = c.speed * 100
        self.speed_label.config(text=f"{c.speed * 100:.0f} %")

        if c.state == State.AWAITING_PILOT:
            left = c.seconds_left()
            self.question.config(text=f"⚠ Obstacle ahead! Move forward?\nStopping in {left:.1f} s",
                                 fg="#b26a00")
            self.countdown["value"] = 100 * left / c.config.pilot_timeout
            self.yes_btn.config(state="normal", text="▲ Move forward (Y)")
            self.no_btn.config(state="normal")
        elif c.state == State.STOPPED:
            self.question.config(text="Vehicle stopped. Resume when safe.", fg="#c62828")
            self.countdown["value"] = 0
            self.yes_btn.config(state="normal", text="▶ Resume (Y)")
            self.no_btn.config(state="disabled")
        else:
            self.question.config(text="No action needed", fg="black")
            self.countdown["value"] = 0
            self.yes_btn.config(state="disabled", text="▲ Move forward (Y)")
            self.no_btn.config(state="normal")  # pilot can always stop

    # ---- drawing helpers -----------------------------------------------
    def _placeholder(self, text):
        img = np.zeros((VIDEO_H, VIDEO_W, 3), np.uint8)
        cv2.putText(img, text, (40, VIDEO_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (200, 200, 200), 2)
        return img

    def _sim_view(self, level):
        """A simple road scene so simulation mode has something to look at."""
        img = np.full((VIDEO_H, VIDEO_W, 3), (60, 60, 60), np.uint8)
        img[: VIDEO_H // 2] = (235, 206, 135)  # sky
        road = np.array([[VIDEO_W // 2 - 40, VIDEO_H // 2], [VIDEO_W // 2 + 40, VIDEO_H // 2],
                         [VIDEO_W - 60, VIDEO_H], [60, VIDEO_H]])
        cv2.fillPoly(img, [road], (90, 90, 90))
        offset = int(self.distance * 60) % 80
        for y in range(VIDEO_H // 2 + offset % 40, VIDEO_H, 80):
            cv2.line(img, (VIDEO_W // 2, y), (VIDEO_W // 2, y + 30), (255, 255, 255), 3)
        if level != Level.CLEAR:
            size = 70 if level == Level.WARNING else 220
            color = (0, 190, 255) if level == Level.WARNING else (0, 0, 255)
            cx, by = VIDEO_W // 2, VIDEO_H // 2 + size
            cv2.rectangle(img, (cx - size // 2, by - size), (cx + size // 2, by), (40, 70, 120), -1)
            cv2.rectangle(img, (cx - size // 2, by - size), (cx + size // 2, by), color, 4)
            cv2.putText(img, level.name, (cx - size // 2, by - size - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(img, "SIMULATION", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        return img

    def show(self, frame):
        h, w = frame.shape[:2]
        scale = min(VIDEO_W / w, VIDEO_H / h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        image = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        self.video.configure(image=image)
        self.video.image = image

    def log(self, text):
        self.log_box.insert(0, f"{datetime.now():%H:%M:%S}  {text}")

    def on_close(self):
        self.vehicle.stop()
        if self.worker:
            self.worker.running = False
            self.worker.join(timeout=2)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Obstacle detection with pilot confirmation.")
    parser.add_argument("--source", default="0", help="camera index or video file")
    parser.add_argument("--sim", action="store_true", help="no camera; place obstacles from the GUI")
    parser.add_argument("--detector", choices=["auto", "yolo", "edges"], default="auto")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds the pilot has to answer")
    args = parser.parse_args()

    source = int(args.source) if args.source.isdigit() else args.source
    pid_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))  # lets stop.bat find this process
    root = tk.Tk()
    ObstacleApp(root, source, args.sim, args.detector, Config(pilot_timeout=args.timeout))
    try:
        root.mainloop()
    finally:
        if os.path.exists(pid_file):
            os.remove(pid_file)


if __name__ == "__main__":
    main()
