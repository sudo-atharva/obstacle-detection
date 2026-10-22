"""Finds obstacles in the vehicle's path in a camera frame.

Only the "path zone", a band in the middle of the image, counts. An object
that takes up more of the frame height is treated as closer:
  height >= warning_size   -> Level.WARNING
  height >= critical_size  -> Level.CRITICAL

Backends:
  YOLO  : (pip install ultralytics) detects real objects such as people, cars and animals.
  EDGES : needs no model. It treats any large, strongly outlined shape in the
          path zone as an obstacle. Rough, but fine for testing.
A single camera can only guess distance. On a real vehicle, add a distance
sensor (ultrasonic, LiDAR or radar) and feed its reading into the same Level logic.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from controller import Level


@dataclass
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    level: Level


class ObstacleDetector:
    def __init__(self, backend="auto", path_width=0.4, warning_size=0.25, critical_size=0.55):
        self.path_width = path_width        # fraction of frame width treated as "our path"
        self.warning_size = warning_size    # fractions of frame height
        self.critical_size = critical_size
        self.model = None
        if backend in ("auto", "yolo"):
            try:
                from ultralytics import YOLO
                self.model = YOLO("yolov8n.pt")
            except ImportError:
                if backend == "yolo":
                    raise
        self.name = "YOLO" if self.model else "Edges"

    def path_zone(self, frame):
        h, w = frame.shape[:2]
        half = int(w * self.path_width / 2)
        return w // 2 - half, 0, w // 2 + half, h

    def detect(self, frame) -> tuple[Level, list[Box]]:
        candidates = self._yolo(frame) if self.model else self._edges(frame)
        zx1, _, zx2, _ = self.path_zone(frame)
        h = frame.shape[0]
        boxes = []
        worst = Level.CLEAR
        for x1, y1, x2, y2, label in candidates:
            if x2 < zx1 or x1 > zx2:
                continue  # not in our path
            size = (y2 - y1) / h
            if size >= self.critical_size:
                level = Level.CRITICAL
            elif size >= self.warning_size:
                level = Level.WARNING
            else:
                continue  # far away, ignore for now
            boxes.append(Box(x1, y1, x2, y2, label, level))
            worst = max(worst, level, key=lambda lv: lv.value)
        return worst, boxes

    def _yolo(self, frame):
        result = self.model(frame, conf=0.4, verbose=False)[0]
        names = result.names
        return [
            (*map(int, xyxy), names[int(cls)])
            for xyxy, cls in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist())
        ]

    def _edges(self, frame):
        gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.dilate(edges, np.ones((9, 9), np.uint8), iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = frame.shape[0] * frame.shape[1] * 0.03
        out = []
        for c in contours:
            if cv2.contourArea(c) < min_area:
                continue
            x, y, w, h = cv2.boundingRect(c)
            out.append((x, y, x + w, y + h, "object"))
        return out


COLORS = {Level.WARNING: (0, 190, 255), Level.CRITICAL: (0, 0, 255)}


def draw_overlay(frame, detector, boxes):
    zx1, zy1, zx2, zy2 = detector.path_zone(frame)
    overlay = frame.copy()
    cv2.rectangle(overlay, (zx1, zy1), (zx2, zy2), (255, 200, 0), -1)
    cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
    cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (255, 200, 0), 1)
    for b in boxes:
        color = COLORS[b.level]
        cv2.rectangle(frame, (b.x1, b.y1), (b.x2, b.y2), color, 3)
        cv2.putText(frame, f"{b.label} ({b.level.name})", (b.x1, max(18, b.y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return frame
