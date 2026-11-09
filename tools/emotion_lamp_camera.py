
r"""Robot Lamp - Fast Multi-Face + Hand Landmarks + Emotion.

Preview:
py -3.11 tools\emotion_lamp_camera.py --camera 0 --preview-only --no-mic

ESP32 through Flask:
py -3.11 tools\emotion_lamp_camera.py --camera 0 --transport http

Features:
- MediaPipe verified multi-face detection
- Optical Flow face tracking
- Green hand landmark dots (21 points)
- No yellow face center dot
- Hand gesture emotion recognition
- Thai microphone commands
- Artificial Hormone / Emotion Brain
- Autonomous animal behavior
- Perception memory and CSV logging
- Optional previously calibrated exploration servo
"""

import argparse
import csv
import json
import math
import queue
import threading
import time
import urllib.request
import os

from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import serial
import sounddevice as sd
import speech_recognition as sr

from autonomous_behavior import AnimalAutonomy, LookBridge
from emotion_brain import EmotionBrain, EMOTIONS

# Four hand-controlled emotions.
# CURIO is separate autonomous behavior.
ACTIVE_EMOTIONS = (
    "HAPPY",
    "SURPRISED",
    "SAD",
    "SLEEPY",
)

from perception_memory import PerceptionMemory


# ==========================================
# CONFIGURATION
# ==========================================

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent

HAND_MODEL = TOOLS / "hand_landmarker.task"
FACE_MODEL = TOOLS / "blaze_face_short_range.tflite"

HAND_MODEL_URL = (
    "https://storage.googleapis.com/"
    "mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/"
    "hand_landmarker.task"
)

FACE_MODEL_URL = (
    "https://storage.googleapis.com/"
    "mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/"
    "blaze_face_short_range.tflite"
)


COLORS = {
    "IDLE": (255, 180, 50),
    "CURIO": (90, 180, 35),
    "HAPPY": (0, 220, 255),
    "SURPRISED": (255, 255, 255),
    "SAD": (255, 0, 0),
    "SLEEPY": (255, 0, 150),
}


VOICE_WORDS = [
    ("ดีใจ", "HAPPY"),
    ("ตกใจ", "SURPRISED"),
    ("เสียใจ", "SAD"),
    ("ง่วงนอน", "SLEEPY"),
    ("ง่วง", "SLEEPY"),
    ("ปกติ", "IDLE"),
]


# ==========================================
# DOWNLOAD MODELS
# ==========================================

def ensure_model(path, url):

    if (
        path.is_file()
        and path.stat().st_size > 10000
    ):
        return

    print("[MODEL] Downloading:", path.name)

    temporary = path.with_name(path.name + ".part")

    try:
        urllib.request.urlretrieve(url, temporary)

        if temporary.stat().st_size < 10000:
            raise RuntimeError("Invalid model file")

        temporary.replace(path)

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


# ==========================================
# HAND GESTURE
# ==========================================

def hand_emotion(points, threshold, was_near):

    def dist(a, b):
        return math.hypot(
            a.x - b.x,
            a.y - b.y
        )

    xs = [p.x for p in points]
    ys = [p.y for p in points]

    area = (
        (max(xs) - min(xs))
        * (max(ys) - min(ys))
    )

    near = area >= (
        threshold - (0.04 if was_near else 0)
    )

    fingers = []

    for mcp, pip, tip in (
        (5, 6, 8),
        (9, 10, 12),
        (13, 14, 16),
        (17, 18, 20)
    ):
        fingers.append(
            dist(points[tip], points[mcp])
            > 1.48 * dist(points[pip], points[mcp])
        )

    thumb = (
        dist(points[4], points[5])
        > 1.25 * dist(points[3], points[5])
    )

    # HAPPY - Thumbs up
    if area >= 0.035 and thumb and not any(fingers):
        if points[4].y < points[2].y - 0.04:
            return "HAPPY", area, near

        # SAD - Thumbs down
        if points[4].y > points[2].y + 0.04:
            return "SAD", area, near

    # SLEEPY - Closed fist
    if (
        area >= 0.010
        and not thumb
        and not any(fingers)
    ):
        return "SLEEPY", area, near

    # SURPRISED - Open palm near camera
    if near and all(fingers):
        return "SURPRISED", area, near

    return "IDLE", area, near


# ==========================================
# DRAW GREEN HAND LANDMARKS
# ==========================================

def draw_hand_points(frame, landmarks):
    """
    Display all 21 hand landmarks.

    The landmarks use normalized coordinates.
    No face-center dot is drawn.
    """

    if landmarks is None:
        return

    height, width = frame.shape[:2]

    for point in landmarks:

        px = int(point.x * width)
        py = int(point.y * height)

        if not (0 <= px < width and 0 <= py < height):
            continue

        # Dark outline improves visibility.
        cv2.circle(
            frame,
            (px, py),
            4,
            (0, 90, 0),
            -1
        )

        # Bright green hand point.
        cv2.circle(
            frame,
            (px, py),
            2,
            (0, 255, 60),
            -1
        )


# ==========================================
# MICROPHONE
# ==========================================

def get_microphone(index):

    if index is not None:
        return index, sd.query_devices(index, "input")

    devices = sd.query_devices()

    try:
        default = int(sd.default.device[0])

        if (
            0 <= default < len(devices)
            and devices[default]["max_input_channels"] > 0
        ):
            return default, devices[default]

    except (ValueError, IndexError, TypeError, sd.PortAudioError):
        pass

    return next(
        (
            (i, d)
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ),
        (None, None)
    )


def microphone_worker(index, rate, audio_queue, stop):

    blocks = queue.Queue(maxsize=100)

    def callback(data, frames, timing, status):

        if not stop.is_set():
            try:
                blocks.put_nowait(data.copy())
            except queue.Full:
                pass

    try:
        samples = []
        total = 0

        with sd.InputStream(
            device=index,
            samplerate=rate,
            channels=1,
            dtype="int16",
            callback=callback
        ):
            print("[MIC] Listening")

            while not stop.is_set():

                try:
                    block = blocks.get(timeout=0.3)
                except queue.Empty:
                    continue

                samples.append(block)
                total += len(block)

                if total < rate * 3:
                    continue

                signal = np.concatenate(samples).reshape(-1)
                samples = []
                total = 0

                rms = float(
                    np.sqrt(
                        np.mean(signal.astype(np.float32) ** 2)
                    ) / 32768
                )

                if rms < 0.008:
                    continue

                audio = sr.AudioData(
                    signal.tobytes(),
                    rate,
                    2
                )

                try:
                    audio_queue.put_nowait(audio)
                except queue.Full:
                    pass

    except Exception as exc:
        print("[MIC ERROR]", exc)


# ==========================================
# VOICE WORKER
# ==========================================

def voice_worker(audio_queue, voice_queue, stop):

    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 8

    while not stop.is_set():

        try:
            audio = audio_queue.get(timeout=0.3)
        except queue.Empty:
            continue

        try:
            spoken = recognizer.recognize_google(
                audio,
                language="th-TH"
            )

            matches = [
                (spoken.index(word), emotion)
                for word, emotion in VOICE_WORDS
                if word in spoken
            ]

            emotion = min(matches)[1] if matches else None

            print("[VOICE]", spoken, emotion)

            if emotion:
                voice_queue.put((emotion, spoken))

        except sr.UnknownValueError:
            pass

        except Exception as exc:
            print("[VOICE ERROR]", exc)


# ==========================================
# LATEST CAMERA FRAME
# HD 1280x720 / MJPG / 30 FPS
# ==========================================

class LatestCamera:

    def __init__(self, index):

        camera = cv2.VideoCapture(
            index,
            cv2.CAP_DSHOW
        )

        if not camera.isOpened():
            camera.release()
            camera = cv2.VideoCapture(index)

        if not camera.isOpened():
            raise RuntimeError(
                f"Cannot open camera {index}"
            )

        # ==================================
        # CAMERA QUALITY CONFIGURATION
        # ==================================

        camera.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc(*"MJPG")
        )

        camera.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            1280
        )

        camera.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            720
        )

        camera.set(
            cv2.CAP_PROP_FPS,
            30
        )

        camera.set(
            cv2.CAP_PROP_BUFFERSIZE,
            1
        )

        # ==================================
        # CHECK ACTUAL CAMERA SETTINGS
        # ==================================

        actual_width = int(
            camera.get(cv2.CAP_PROP_FRAME_WIDTH)
        )

        actual_height = int(
            camera.get(cv2.CAP_PROP_FRAME_HEIGHT)
        )

        actual_fps = camera.get(
            cv2.CAP_PROP_FPS
        )

        actual_fourcc = int(
            camera.get(cv2.CAP_PROP_FOURCC)
        )

        codec = "".join(
            chr(
                (actual_fourcc >> (8 * i)) & 0xFF
            )
            for i in range(4)
        )

        print(
            f"[CAMERA] Actual mode: "
            f"{actual_width}x{actual_height} "
            f"@ {actual_fps:.1f} FPS "
            f"| Codec: {codec}"
        )

        if (
            actual_width != 1280
            or actual_height != 720
        ):
            print(
                "[CAMERA WARNING] Webcam or driver "
                "did not accept 1280x720. "
                "Using reported resolution."
            )

        # ==================================
        # CAMERA THREAD STATE
        # ==================================

        self.camera = camera
        self.frame = None
        self.sequence = 0

        self.lock = threading.Lock()
        self.stop = threading.Event()

        self.thread = threading.Thread(
            target=self._reader,
            daemon=True
        )

        self.thread.start()

    # ======================================
    # CAMERA READER
    # ======================================

    def _reader(self):

        failures = 0

        while not self.stop.is_set():

            ok, frame = self.camera.read()

            if not ok:
                failures += 1

                if failures > 15:
                    break

                time.sleep(0.03)
                continue

            failures = 0

            with self.lock:
                self.frame = frame
                self.sequence += 1

        self.stop.set()

    # ======================================
    # READ LATEST FRAME
    # ======================================

    def read(
        self,
        previous_sequence,
        timeout=2.0
    ):

        deadline = (
            time.monotonic() + timeout
        )

        while (
            time.monotonic() < deadline
            and not self.stop.is_set()
        ):

            with self.lock:

                if (
                    self.frame is not None
                    and self.sequence != previous_sequence
                ):
                    return (
                        self.frame.copy(),
                        self.sequence
                    )

            time.sleep(0.005)

        return None, previous_sequence

    # ======================================
    # CLOSE CAMERA
    # ======================================

    def close(self):

        self.stop.set()

        self.thread.join(
            timeout=1.0
        )

        self.camera.release()


# ==========================================
# MEDIAPIPE VERIFIED FACE DETECTOR
# ==========================================

class VerifiedFaceDetector:

    def __init__(
        self,
        model,
        confidence=0.75,
        frontal_only=True
    ):

        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(model)
            ),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            min_detection_confidence=confidence,
            min_suppression_threshold=0.30
        )

        self.detector = (
            mp.tasks.vision.FaceDetector
            .create_from_options(options)
        )

        self.confidence = confidence
        self.frontal_only = frontal_only

        print("[FACE] MediaPipe ready")

    def scan(self, rgb, timestamp):

        height, width = rgb.shape[:2]

        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb
        )

        result = self.detector.detect_for_video(
            image,
            timestamp
        )

        boxes = []

        for detection in result.detections:

            if not detection.categories:
                continue

            score = detection.categories[0].score

            if score < self.confidence:
                continue

            box = detection.bounding_box

            x = max(0, int(box.origin_x))
            y = max(0, int(box.origin_y))

            right = min(
                width,
                int(box.origin_x + box.width)
            )

            bottom = min(
                height,
                int(box.origin_y + box.height)
            )

            w = right - x
            h = bottom - y

            if w < 32 or h < 32:
                continue

            keypoints = detection.keypoints

            if self.frontal_only and len(keypoints) >= 3:

                eye1 = keypoints[0]
                eye2 = keypoints[1]
                nose = keypoints[2]

                eye_distance = abs(
                    eye1.x - eye2.x
                ) * width

                eye_mid_x = (
                    eye1.x + eye2.x
                ) * width / 2

                nose_x = nose.x * width

                if eye_distance < 0.17 * w:
                    continue

                if abs(nose_x - eye_mid_x) > 0.33 * w:
                    continue

            boxes.append([
                float(x),
                float(y),
                float(w),
                float(h)
            ])

        return boxes

    def close(self):
        self.detector.close()


# ==========================================
# FACE BOX UTILITIES
# ==========================================

def box_center(box):

    x, y, w, h = box

    return (
        x + w / 2,
        y + h / 2
    )


def box_overlap(a, b):

    ax, ay, aw, ah = a
    bx, by, bw, bh = b

    iw = max(
        0,
        min(ax + aw, bx + bw) - max(ax, bx)
    )

    ih = max(
        0,
        min(ay + ah, by + bh) - max(ay, by)
    )

    intersection = iw * ih

    smaller = min(
        aw * ah,
        bw * bh
    )

    return intersection / max(1, smaller)


# ==========================================
# FAST MULTI-FACE TRACKER
# ==========================================

class FastFaceTracker:

    def __init__(self, hold_seconds=0.55, min_hits=2):

        self.hold = hold_seconds
        self.min_hits = min_hits

        self.tracks = {}
        self.next_id = 1

        self.primary_id = None
        self.previous_gray = None

        self.x = None
        self.y = None
        self.direction = "NONE"

    # ======================================
    # FACE FEATURE POINTS FOR OPTICAL FLOW
    # ======================================

    @staticmethod
    def features(gray, box):

        x, y, w, h = [
            int(round(value))
            for value in box
        ]

        height, width = gray.shape

        x0 = max(0, x + int(w * 0.12))
        x1 = min(width, x + int(w * 0.88))
        y0 = max(0, y + int(h * 0.12))
        y1 = min(height, y + int(h * 0.88))

        if x1 - x0 < 12 or y1 - y0 < 12:
            return None

        roi = gray[y0:y1, x0:x1]

        points = cv2.goodFeaturesToTrack(
            roi,
            maxCorners=45,
            qualityLevel=0.012,
            minDistance=4,
            blockSize=5
        )

        if points is not None:
            points[:, 0, 0] += x0
            points[:, 0, 1] += y0

        return points

    # ======================================
    # OPTICAL FLOW
    # ======================================

    def update_flow(self, gray):

        if self.previous_gray is None:
            return

        if self.previous_gray.shape != gray.shape:
            return

        height, width = gray.shape

        for track in self.tracks.values():

            points = track["points"]

            if points is None or len(points) < 6:
                continue

            try:
                moved, status, _ = (
                    cv2.calcOpticalFlowPyrLK(
                        self.previous_gray,
                        gray,
                        points,
                        None,
                        winSize=(31, 31),
                        maxLevel=3,
                        criteria=(
                            cv2.TERM_CRITERIA_EPS
                            | cv2.TERM_CRITERIA_COUNT,
                            20,
                            0.03
                        )
                    )
                )

            except cv2.error:
                track["points"] = None
                continue

            if moved is None or status is None:
                track["points"] = None
                continue

            valid = status.reshape(-1) == 1

            before = points.reshape(-1, 2)[valid]
            after = moved.reshape(-1, 2)[valid]

            if len(before) < 6:
                track["points"] = None
                continue

            displacement = after - before

            shift = np.median(
                displacement,
                axis=0
            )

            inliers = (
                np.linalg.norm(
                    displacement - shift,
                    axis=1
                ) < 12
            )

            if np.count_nonzero(inliers) < 6:
                track["points"] = None
                continue

            dx, dy = np.median(
                displacement[inliers],
                axis=0
            )

            if not np.isfinite([dx, dy]).all():
                continue

            if (
                abs(dx) > width * 0.20
                or abs(dy) > height * 0.20
            ):
                continue

            x, y, w, h = track["box"]

            new_x = max(
                0.0,
                min(width - w, x + float(dx))
            )

            new_y = max(
                0.0,
                min(height - h, y + float(dy))
            )

            track["box"] = [
                new_x,
                new_y,
                w,
                h
            ]

            track["points"] = (
                after[inliers]
                .reshape(-1, 1, 2)
                .astype(np.float32)
            )

            # Optical Flow cannot extend
            # the detection expiration time.

    # ======================================
    # MATCH DETECTED FACES
    # ======================================

    def correct(self, gray, boxes, now):

        candidates = []

        for track_id, track in self.tracks.items():

            tx, ty = box_center(track["box"])

            for index, box in enumerate(boxes):

                bx, by = box_center(box)

                distance = math.hypot(
                    bx - tx,
                    by - ty
                )

                gate = max(
                    45,
                    track["box"][2],
                    track["box"][3],
                    box[2],
                    box[3]
                )

                size_ratio = max(
                    box[2] / max(track["box"][2], 1),
                    track["box"][2] / max(box[2], 1)
                )

                if (
                    distance < gate
                    and size_ratio < 2.0
                ):
                    candidates.append(
                        (
                            distance / gate,
                            track_id,
                            index
                        )
                    )

        matched_tracks = set()
        matched_boxes = set()

        for _, track_id, index in sorted(candidates):

            if (
                track_id in matched_tracks
                or index in matched_boxes
            ):
                continue

            track = self.tracks[track_id]
            box = boxes[index]

            track["box"] = [
                0.15 * old + 0.85 * new
                for old, new in zip(
                    track["box"],
                    box
                )
            ]

            track["hits"] += 1

            track["confirmed"] = (
                track["confirmed"]
                or track["hits"] >= self.min_hits
            )

            track["last_detected"] = now

            track["points"] = self.features(
                gray,
                track["box"]
            )

            matched_tracks.add(track_id)
            matched_boxes.add(index)

        for index, box in enumerate(boxes):

            if index in matched_boxes:
                continue

            duplicate = any(
                box_overlap(
                    box,
                    track["box"]
                ) > 0.55
                for track in self.tracks.values()
            )

            if duplicate:
                continue

            track_id = self.next_id
            self.next_id += 1

            self.tracks[track_id] = {
                "box": box,
                "hits": 1,
                "confirmed": self.min_hits <= 1,
                "last_detected": now,
                "points": self.features(gray, box)
            }

    # ======================================
    # UPDATE TRACKER
    # ======================================

    def update(self, gray, boxes, fresh, now):

        self.update_flow(gray)

        if fresh:
            self.correct(gray, boxes, now)

        for track_id in list(self.tracks):

            track = self.tracks[track_id]

            age = now - track["last_detected"]

            timeout = (
                self.hold
                if track["confirmed"]
                else min(self.hold, 0.35)
            )

            if age > timeout:
                del self.tracks[track_id]

        self.previous_gray = gray

        confirmed = {
            track_id: track
            for track_id, track in self.tracks.items()
            if track["confirmed"]
        }

        # ==================================
        # SELECT PRIMARY FACE
        # ==================================

        if self.primary_id not in confirmed:

            self.primary_id = None

            if confirmed:

                self.primary_id = min(
                    confirmed,
                    key=lambda tid: math.hypot(
                        box_center(
                            confirmed[tid]["box"]
                        )[0] / gray.shape[1] - 0.5,
                        box_center(
                            confirmed[tid]["box"]
                        )[1] / gray.shape[0] - 0.5
                    )
                )

        # ==================================
        # CALCULATE X/Y
        # ==================================

        if self.primary_id is None:

            self.x = None
            self.y = None
            self.direction = "NONE"

        else:

            cx, cy = box_center(
                confirmed[self.primary_id]["box"]
            )

            self.x = max(
                0.0,
                min(1.0, cx / gray.shape[1])
            )

            self.y = max(
                0.0,
                min(1.0, cy / gray.shape[0])
            )

            if (
                self.direction == "LEFT"
                and self.x < 0.45
            ):
                self.direction = "LEFT"

            elif (
                self.direction == "RIGHT"
                and self.x > 0.55
            ):
                self.direction = "RIGHT"

            elif self.x < 0.38:
                self.direction = "LEFT"

            elif self.x > 0.62:
                self.direction = "RIGHT"

            else:
                self.direction = "CENTER"

        # ==================================
        # DISPLAYABLE TRACKS
        # ==================================

        visible = []

        ordered = sorted(
            confirmed.items(),
            key=lambda item: item[1]["box"][0]
        )

        for track_id, track in ordered:

            age = now - track["last_detected"]

            if age < 0.13:
                state = "DETECT"
            elif age < 0.40:
                state = "FLOW"
            else:
                state = "HOLD"

            visible.append(
                (
                    track_id,
                    track["box"],
                    state
                )
            )

        return (
            self.direction,
            self.x,
            self.y,
            visible
        )


# ==========================================
# ESP32 / FLASK TRANSPORT
# ==========================================

class EmotionTransport:

    def __init__(self, mode, server, port):
        self.mode = mode
        self.server = server.rstrip("/")

        self.device = None
        self.last_sent = None
        self.last_attempt = 0.0
        self.last_sent_time = 0.0

        self.last_hormone_attempt = 0.0
        self.last_hormone_ok = 0.0
        self.last_hormone_value = None

        if mode == "auto":

            try:
                with urllib.request.urlopen(
                    self.server + "/api/emotion/capabilities",
                    timeout=1
                ):
                    self.mode = "http"

            except Exception:
                self.mode = "preview"

        if self.mode == "serial":

            self.device = serial.Serial(
                port,
                115200,
                timeout=0.05,
                write_timeout=0.6
            )

            time.sleep(1.8)
            self.device.reset_input_buffer()

        print("[TRANSPORT]", self.mode)

    def send(self, emotion, source):

        if self.mode == "preview":
            return True

        now = time.monotonic()

        # CURIO can repeat after 18 seconds.
        # Other expressions only send on change.
        if emotion == self.last_sent:
            if (
                emotion != "CURIO"
                or now - self.last_sent_time < 18.0
            ):
                return True

        if now - self.last_attempt < 1.0:
            return False

        self.last_attempt = now

        try:

            if self.mode == "http":

                data = json.dumps({
                    "emotion": emotion,
                    "source": source,
                    "control_mode": "hormone"
                }).encode("utf-8")

                request = urllib.request.Request(
                    self.server + "/api/emotion",
                    data=data,
                    method="POST",
                    headers={
                        "Content-Type": "application/json"
                    }
                )

                with urllib.request.urlopen(
                    request,
                    timeout=4
                ) as response:
                    result = json.load(response)

                if not result.get("ok"):
                    raise RuntimeError(str(result))

            else:

                self.device.write(
                    (emotion + "\n").encode("ascii")
                )

                self.device.flush()

            self.last_sent = emotion
            self.last_sent_time = time.monotonic()

            print("[SENT]", emotion)
            return True

        except Exception as exc:
            print("[TRANSPORT ERROR]", exc)
            return False


    # RL_HORMONE_CPG_V1
    def send_hormone(self, value):
        if self.mode != "http":
            return False

        h = max(0.0, min(1.0, float(value)))
        now = time.monotonic()

        # At most one update every 0.8 s.
        if now - self.last_hormone_attempt < 0.8:
            return False

        # Suppress near-identical values, but send
        # a heartbeat so ESP32 knows data is fresh.
        if (
            self.last_hormone_value is not None
            and abs(h - self.last_hormone_value) < 0.025
            and now - self.last_hormone_ok < 2.0
        ):
            return True

        self.last_hormone_attempt = now

        try:
            body = json.dumps({
                "hormone": round(h, 4)
            }).encode("utf-8")

            req = urllib.request.Request(
                self.server + "/api/hormone/drive",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json"
                }
            )

            with urllib.request.urlopen(
                req,
                timeout=2.5
            ) as response:
                result = json.load(response)

            if not result.get("ok"):
                raise RuntimeError(str(result))

            self.last_hormone_value = h
            self.last_hormone_ok = time.monotonic()

            return True

        except Exception as exc:
            print("[HORMONE TRANSPORT ERROR]", exc)
            return False

    def close(self):

        if self.device is not None:
            self.device.close()


# ==========================================
# MAIN
# ==========================================


# ==========================================
# ROBOT_LAMP_SIGNAL_FILTER_V2
# OUTGOING EMOTION FILTER
# ==========================================

class FilteredEmotionTransport(EmotionTransport):

    STABLE_SECONDS = 0.8
    MIN_GAP_SECONDS = 3.0
    SETTLE_SECONDS = 2.0
    FALLBACK_MOTION_SECONDS = 8.0

    def __init__(self, mode, server, port):

        super().__init__(mode, server, port)

        self._filter_candidate = None
        self._filter_since = 0.0

        self._filter_last = None
        self._filter_last_at = 0.0
        self._filter_next_at = 0.0

    # ======================================
    # READ SAVED MOTION DURATION
    # ======================================

    def _expected_seconds(self, emotion):

        if emotion == "IDLE":
            return 0.0

        profile = (
            ROOT
            / "dashboard"
            / "emotion_motion.json"
        )

        try:
            data = json.loads(
                profile.read_text(
                    encoding="utf-8"
                )
            )

            frames = data[emotion]

            if (
                not isinstance(frames, list)
                or not 1 <= len(frames) <= 12
            ):
                raise ValueError(
                    "Invalid frame count"
                )

            total_ms = 0

            for frame in frames:

                if (
                    not isinstance(frame, list)
                    or len(frame) != 5
                ):
                    raise ValueError(
                        "Invalid frame"
                    )

                hold = frame[4]

                if (
                    type(hold) is not int
                    or not 300 <= hold <= 8000
                ):
                    raise ValueError(
                        "Invalid hold time"
                    )

                total_ms += hold

            return total_ms / 1000.0

        except (
            OSError,
            ValueError,
            KeyError,
            TypeError
        ):
            return self.FALLBACK_MOTION_SECONDS

    # ======================================
    # FILTER OUTGOING COMMANDS
    # ======================================

    def send(self, emotion, source):

        # Preview does not command the motors.
        if self.mode == "preview":
            return super().send(
                emotion,
                source
            )

        allowed = (
            "IDLE",
            "CURIO",
            "HAPPY",
            "SURPRISED",
            "SAD",
            "SLEEPY"
        )

        if emotion not in allowed:
            return False

        now = time.monotonic()

        # Step 1: emotion must be stable.
        if emotion != self._filter_candidate:

            self._filter_candidate = emotion
            self._filter_since = now

            return False

        if (
            now - self._filter_since
            < self.STABLE_SECONDS
        ):
            return False

        # Step 2: wait until the previous
        # motion's estimated duration ends.
        if now < self._filter_next_at:
            return False

        # Step 3: suppress duplicate emotions.
        if emotion == self._filter_last:

            if (
                emotion != "CURIO"
                or now - self._filter_last_at < 18.0
            ):
                return True

        # Step 4: use the existing transport.
        success = super().send(
            emotion,
            source
        )

        if not success:
            return False

        # Step 5: lock based on saved Hold ms.
        self._filter_last = emotion
        self._filter_last_at = time.monotonic()

        duration = self._expected_seconds(
            emotion
        )

        lock_for = max(
            self.MIN_GAP_SECONDS,
            duration + self.SETTLE_SECONDS
        )

        self._filter_next_at = (
            self._filter_last_at
            + lock_for
        )

        print(
            f"[FILTER] {emotion} accepted; "
            f"pause {lock_for:.1f}s"
        )

        return True


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--mic-index", type=int)

    parser.add_argument(
        "--no-mic",
        action="store_true"
    )

    parser.add_argument(
        "--preview-only",
        action="store_true"
    )

    parser.add_argument(
        "--transport",
        choices=("auto", "http", "serial", "preview"),
        default="auto"
    )

    parser.add_argument(
        "--server",
        default="http://127.0.0.1:5000"
    )

    parser.add_argument(
        "--look-servo",
        type=int,
        choices=(1, 2, 3, 4)
    )

    parser.add_argument(
        "--look-amplitude",
        type=int,
        choices=(1, 2, 3, 4, 5),
        default=3
    )

    parser.add_argument(
        "--no-face",
        action="store_true"
    )

    parser.add_argument(
        "--no-motion",
        action="store_true"
    )

    parser.add_argument(
        "--no-memory",
        action="store_true"
    )

    parser.add_argument(
        "--face-hold",
        type=float,
        default=0.55
    )

    parser.add_argument(
        "--face-confidence",
        type=float,
        default=0.75
    )

    parser.add_argument(
        "--face-every",
        type=int,
        default=2
    )

    parser.add_argument(
        "--hand-every",
        type=int,
        default=2
    )

    parser.add_argument(
        "--face-neighbors",
        type=int,
        default=6
    )

    parser.add_argument(
        "--allow-side-face",
        action="store_true"
    )

    parser.add_argument(
        "--log",
        default=str(ROOT / "emotion_trace.csv")
    )

    args = parser.parse_args()

    if not (
        0.2 <= args.face_hold <= 1.5
        and 0.5 <= args.face_confidence <= 0.99
    ):
        parser.error("Invalid face settings")

    if not (
        1 <= args.face_every <= 6
        and 1 <= args.hand_every <= 5
    ):
        parser.error("Invalid detection interval")

    ensure_model(HAND_MODEL, HAND_MODEL_URL)

    if not args.no_face:
        ensure_model(FACE_MODEL, FACE_MODEL_URL)

    # ======================================
    # EMOTION + AUTONOMOUS
    # ======================================

    brain = EmotionBrain(
        sleep_after=float("inf"),
        wake_idle=1.5,
        voice_hold=5.0
    )

    animal = AnimalAutonomy()

    tracker = FastFaceTracker(
        hold_seconds=args.face_hold,
        min_hits=2
    )

    memory = None

    if not args.no_memory:

        memory = PerceptionMemory(
            ROOT / "data" / "perception_events.csv",
            stable_frames=10
        )

    stop = threading.Event()

    audio_queue = queue.Queue(maxsize=2)
    voice_queue = queue.Queue()

    camera = None
    output = None
    look_bridge = None
    face_detector = None

    try:

        # ==================================
        # START MICROPHONE
        # ==================================

        if not args.no_mic:

            try:
                mic_index, device = get_microphone(
                    args.mic_index
                )

                if device is not None:

                    rate = int(
                        device["default_samplerate"]
                    )

                    threading.Thread(
                        target=microphone_worker,
                        args=(
                            mic_index,
                            rate,
                            audio_queue,
                            stop
                        ),
                        daemon=True
                    ).start()

                    threading.Thread(
                        target=voice_worker,
                        args=(
                            audio_queue,
                            voice_queue,
                            stop
                        ),
                        daemon=True
                    ).start()

            except Exception as exc:
                print("[MIC ERROR]", exc)

        # ==================================
        # CAMERA + ESP32 TRANSPORT
        # ==================================

        camera = LatestCamera(args.camera)

        output = FilteredEmotionTransport(
            "preview" if args.preview_only else args.transport,
            args.server,
            args.port
        )


        # RL_CAMERA_ASYNC_IO_V1
        # Network I/O runs outside the camera frame loop.

        io_lock = threading.Lock()

        latest_io = {
            "hormone": 0.0,
            "emotion": "IDLE",
            "source": "",
            "updated": 0.0,
        }

        def camera_network_worker():
            while not stop.is_set():
                with io_lock:
                    item = latest_io.copy()

                age = time.monotonic() - item["updated"]

                if item["updated"] > 0 and age < 2.0:
                    output.send_hormone(item["hormone"])
                    output.send(
                        item["emotion"],
                        item["source"]
                    )

                stop.wait(0.15)

        if output.mode == "http":
            threading.Thread(
                target=camera_network_worker,
                daemon=True
            ).start()

        if (
            args.look_servo is not None
            and output.mode == "http"
        ):

            look_bridge = LookBridge(
                args.look_servo,
                args.look_amplitude,
                args.server
            )

        # ==================================
        # FACE DETECTOR
        # ==================================

        if not args.no_face:

            face_detector = VerifiedFaceDetector(
                FACE_MODEL,
                confidence=args.face_confidence,
                frontal_only=not args.allow_side_face
            )

        # ==================================
        # HAND DETECTOR
        # ==================================

        hand_options = (
            mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=str(HAND_MODEL)
                ),
                running_mode=(
                    mp.tasks.vision.RunningMode.VIDEO
                ),
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_tracking_confidence=0.5
            )
        )

        # ==================================
        # CSV
        # ==================================

        fields = [
            "time_s",
            "raw",
            "stable",
            "hand_area",
            "face_count",
            "face_recent",
            "face_x",
            "face_y",
            "face_direction",
            "tracked_face",
            "motion_ratio",
            "voice",
            "expression",
            "brain_expression",
            "brain_source",
            "behavior_mode",
            "look",
            "curiosity",
            "energy",
            "arousal",
        ]

        fields += [
            "H_MOTOR",
            "MOTOR_SPEED_RAW",
            "STIMULUS_MAX",
        ]

        fields += [
            "H_" + e for e in ACTIVE_EMOTIONS
        ]

        fields += [
            "R_" + e for e in ACTIVE_EMOTIONS
        ]

        with open(
            args.log,
            "w",
            encoding="utf-8-sig",
            newline=""
        ) as logfile:

            writer = csv.DictWriter(
                logfile,
                fieldnames=fields
            )

            writer.writeheader()

            with (
                mp.tasks.vision.HandLandmarker
                .create_from_options(hand_options)
            ) as hands:

                print("[READY] Face + Hand Landmark Tracking")

                # Resizable camera window
                WINDOW_TITLE = (
                    "Robot Lamp - Face + Hand Landmarks"
                )

                cv2.namedWindow(
                    WINDOW_TITLE,
                    cv2.WINDOW_NORMAL
                )

                cv2.resizeWindow(
                    WINDOW_TITLE,
                    960,
                    600
                )

                previous_sequence = -1
                previous_motion = None

                frame_index = 0
                last_hand = None
                last_timestamp = -1

                last_log = 0.0
                last_loop = time.monotonic()

                fps = 0.0

                near_threshold = 0.22
                was_near = False

                raw_candidate = "IDLE"
                stable = "IDLE"
                stable_frames = 0

                last_mode = None
                last_look = "CENTER"
                last_direction = "NONE"
                last_face_seen = None

                # CURIO / Auto Sleep
                low_hormone_since = None
                sleeping = False
                curio_hold_until = 0.0
                HORMONE_SLEEP_THRESHOLD = 0.15
                HORMONE_SLEEP_SECONDS = 20.0

                while True:

                    # ======================
                    # READ CAMERA
                    # ======================

                    frame, previous_sequence = camera.read(
                        previous_sequence
                    )

                    if frame is None:
                        print("[CAMERA] No new frame")
                        break

                    now = time.monotonic()

                    dt = max(
                        now - last_loop,
                        0.001
                    )

                    last_loop = now

                    instant_fps = 1.0 / dt

                    fps = (
                        instant_fps
                        if fps == 0.0
                        else 0.85 * fps + 0.15 * instant_fps
                    )

                    frame = cv2.flip(frame, 1)

                    height, width = frame.shape[:2]
                    frame_index += 1

                    timestamp = max(
                        int(now * 1000),
                        last_timestamp + 1
                    )

                    last_timestamp = timestamp

                    rgb = cv2.cvtColor(
                        frame,
                        cv2.COLOR_BGR2RGB
                    )

                    image = mp.Image(
                        image_format=mp.ImageFormat.SRGB,
                        data=rgb
                    )

                    # ======================
                    # HAND DETECTION
                    # ======================

                    if (
                        (frame_index - 1)
                        % args.hand_every == 0
                    ):

                        hand_result = hands.detect_for_video(
                            image,
                            timestamp
                        )

                        last_hand = (
                            hand_result.hand_landmarks[0]
                            if hand_result.hand_landmarks
                            else None
                        )

                    hand_seen = last_hand is not None

                    if hand_seen:

                        raw, area, was_near = hand_emotion(
                            last_hand,
                            near_threshold,
                            was_near
                        )

                    else:
                        raw = "IDLE"
                        area = 0.0
                        was_near = False

                    if raw == raw_candidate:
                        stable_frames += 1
                    else:
                        raw_candidate = raw
                        stable_frames = 1

                    if stable_frames >= 8:
                        stable = raw_candidate

                    # ======================
                    # VERIFIED FACE DETECTION
                    # ======================

                    gray = cv2.cvtColor(
                        frame,
                        cv2.COLOR_BGR2GRAY
                    )

                    detect_now = (
                        face_detector is not None
                        and (
                            (frame_index - 1)
                            % args.face_every == 0
                        )
                    )

                    if detect_now:

                        boxes = face_detector.scan(
                            rgb,
                            timestamp
                        )

                    else:
                        boxes = []

                    (
                        direction,
                        face_x,
                        face_y,
                        faces
                    ) = tracker.update(
                        gray,
                        boxes,
                        detect_now,
                        now
                    )

                    if faces:
                        last_face_seen = now

                    face_recent = (
                        last_face_seen is not None
                        and now - last_face_seen <= 1.5
                    )

                    if direction != last_direction:

                        print(
                            f"[FACE TRACK] {direction} "
                            f"X={face_x} Y={face_y} "
                            f"COUNT={len(faces)}"
                        )

                        last_direction = direction

                    # ======================
                    # MOTION DETECTION
                    # ======================

                    motion_ratio = 0.0

                    if not args.no_motion:

                        small = cv2.resize(
                            gray,
                            None,
                            fx=0.5,
                            fy=0.5
                        )

                        blurred = cv2.GaussianBlur(
                            small,
                            (9, 9),
                            0
                        )

                        if previous_motion is not None:

                            difference = cv2.absdiff(
                                blurred,
                                previous_motion
                            )

                            motion_ratio = float(
                                np.mean(difference > 30)
                            )

                        previous_motion = blurred

                    motion_seen = motion_ratio > 0.05

                    # ======================
                    # PERCEPTION MEMORY
                    # ======================

                    events = []

                    if memory is not None:

                        events = memory.observe(
                            {
                                "FACE": face_recent,
                                "HAND": hand_seen,
                                "MOTION": motion_seen
                            },
                            {
                                "FACE": f"count={len(faces)}",
                                "MOTION": f"ratio={motion_ratio:.3f}"
                            }
                        )

                    if "FACE_APPEARED" in events:

                        animal.curiosity = min(
                            1.0,
                            animal.curiosity + 0.08
                        )

                    elif "MOTION_APPEARED" in events:

                        animal.curiosity = min(
                            1.0,
                            animal.curiosity + 0.03
                        )

                    # ======================
                    # VOICE
                    # ======================

                    voice_emotion = None
                    voice_text = ""

                    while True:

                        try:
                            (
                                voice_emotion,
                                voice_text
                            ) = voice_queue.get_nowait()

                        except queue.Empty:
                            break

                    # ======================
                    # EMOTION BRAIN
                    # ======================

                    decision = brain.update(
                        stable,
                        valid=True,
                        voice=voice_emotion,
                        now=now
                    )

                    voice_active = (
                        bool(voice_emotion)
                        or (
                            brain.voice_emotion is not None
                            and now < brain.voice_until
                        )
                        or brain.forced_sleep
                    )

                    visual_contact = (
                        hand_seen
                        or face_recent
                        or (
                            memory.currently_seen("FACE")
                            if memory is not None
                            else False
                        )
                    )

                    # ======================
                    # AUTONOMOUS BEHAVIOR
                    # ======================

                    behavior = animal.update(
                        now,
                        decision.emotion,
                        hand_present=visual_contact,
                        voice_event=voice_active
                    )

                    mode = behavior.mode

                    if (
                        face_recent
                        and not hand_seen
                        and decision.emotion == "IDLE"
                        and not voice_active
                    ):
                        mode = "ATTENTIVE"

                    # ==================================
                    # FINAL HAND / CURIO / SLEEPY
                    # ==================================

                    valid_hand = (
                        hand_seen
                        and stable in ACTIVE_EMOTIONS
                    )

                    if valid_hand:
                        sleeping = False
                        low_hormone_since = None
                        curio_hold_until = 0.0
                        expression = stable

                    elif voice_active:
                        # Preserve existing microphone path.
                        # Legacy ANGRY is not sent to ESP32.
                        expression = (
                            decision.emotion
                            if decision.emotion in ACTIVE_EMOTIONS
                            else "IDLE"
                        )
                        curio_hold_until = 0.0

                    else:
                        active_hormone_peak = max(
                            (
                                float(
                                    decision.hormone.get(e, 0.0)
                                )
                                for e in (
                                    "HAPPY",
                                    "SURPRISED",
                                    "SAD"
                                )
                            ),
                            default=0.0
                        )

                        if (
                            not hand_seen
                            and active_hormone_peak
                            <= HORMONE_SLEEP_THRESHOLD
                        ):
                            if low_hormone_since is None:
                                low_hormone_since = now

                            if (
                                now - low_hormone_since
                                >= HORMONE_SLEEP_SECONDS
                            ):
                                sleeping = True
                        else:
                            low_hormone_since = None

                        if sleeping:
                            mode = "SLEEP"
                            expression = "SLEEPY"
                            curio_hold_until = 0.0

                        elif hand_seen:
                            expression = "IDLE"
                            curio_hold_until = 0.0

                        elif (
                            mode == "EXPLORE"
                            or now < curio_hold_until
                        ):
                            if now >= curio_hold_until:
                                curio_hold_until = now + 12.0

                            mode = "CURIO"
                            expression = "CURIO"
                            last_look = "SCAN"

                        else:
                            expression = "IDLE"

                    # Continue selected emotion while its
                    # hormone remains above Brain threshold.
                    if (
                        not valid_hand
                        and not voice_active
                        and decision.emotion in EMOTIONS
                        and not sleeping
                    ):
                        expression = decision.emotion
                        mode = "HORMONE"
                        curio_hold_until = 0.0

                    # Prevent LookBridge from moving
                    # motors during ESP32 CURIO motion.
                    if look_bridge is not None:
                        look_bridge.set_paused(
                            expression == "CURIO"
                            or visual_contact
                            or voice_active
                            or decision.emotion != "IDLE"
                        )

                    if (
                        behavior.look is not None
                        and expression != "CURIO"
                    ):
                        last_look = behavior.look

                        if look_bridge is not None:
                            look_bridge.submit(last_look)

                    current = (mode, expression)

                    if current != last_mode:

                        print(
                            f"[BEHAVIOR] {mode} "
                            f"-> {expression}"
                        )

                        last_mode = current

                    # ======================
                    # ESP32 OUTPUT
                    # ======================

                    # RL_HORMONE_CPG_V1
                    # H is the actual Brain concentration.
                    # IDLE / CURIO / SLEEPY use a slow baseline.
                    motor_drive = (
                        max(decision.hormone.values(), default=0.0)
                        if expression in EMOTIONS
                        else 0.0
                    )

                    motor_drive = max(
                        0.0, min(1.0, motor_drive)
                    )

                    motor_speed_raw = round(
                        40 + 460 * motor_drive
                    )

                    stimulus_max = max(
                        decision.stimulus.values(),
                        default=0.0
                    )

                    if output.mode == "http":
                        with io_lock:
                            latest_io.update(
                                hormone=motor_drive,
                                emotion=expression,
                                source=(
                                    f"{decision.source} | AUTO:{mode}"
                                ),
                                updated=time.monotonic()
                            )
                    else:
                        output.send(
                            expression,
                            f"{decision.source} | AUTO:{mode}"
                        )

                    # ======================
                    # SAVE CSV
                    # ======================

                    if now - last_log >= 0.25:

                        row = {
                            "time_s": f"{now:.3f}",
                            "raw": raw,
                            "stable": stable,
                            "hand_area": f"{area:.4f}",
                            "face_count": len(faces),
                            "face_recent": int(face_recent),
                            "face_x": (
                                f"{face_x:.4f}"
                                if face_x is not None
                                else ""
                            ),
                            "face_y": (
                                f"{face_y:.4f}"
                                if face_y is not None
                                else ""
                            ),
                            "face_direction": direction,
                            "tracked_face": (
                                tracker.primary_id or ""
                            ),
                            "motion_ratio": (
                                f"{motion_ratio:.4f}"
                            ),
                            "voice": voice_text,
                            "expression": expression,
                            "brain_expression": decision.emotion,
                            "brain_source": decision.source,
                            "behavior_mode": mode,
                            "look": behavior.look or "",
                            "curiosity": (
                                f"{behavior.curiosity:.4f}"
                            ),
                            "energy": (
                                f"{behavior.energy:.4f}"
                            ),
                            "arousal": (
                                f"{behavior.arousal:.4f}"
                            )
                        }

                        row.update({
                            "H_" + e: (
                                f"{decision.hormone.get(e, 0.0):.4f}"
                            )
                            for e in ACTIVE_EMOTIONS
                        })

                        row.update({
                            "R_" + e: (
                                f"{decision.receptor.get(e, 0.0):.4f}"
                            )
                            for e in ACTIVE_EMOTIONS
                        })

                        row["H_MOTOR"] = f"{motor_drive:.4f}"
                        row["MOTOR_SPEED_RAW"] = motor_speed_raw
                        row["STIMULUS_MAX"] = (
                            f"{stimulus_max:.4f}"
                        )

                        writer.writerow(row)
                        logfile.flush()

                        last_log = now

                    # ======================
                    # DRAW FACE BOXES
                    # ======================

                    for track_id, box, state in faces:

                        x, y, w, h = [
                            int(round(value))
                            for value in box
                        ]

                        is_target = (
                            track_id == tracker.primary_id
                        )

                        color = (
                            (0, 155, 0)
                            if state == "HOLD"
                            else (0, 255, 0)
                        )

                        cv2.rectangle(
                            frame,
                            (x, y),
                            (x + w, y + h),
                            color,
                            3 if is_target else 2
                        )

                        label = f"FACE {track_id}"

                        if is_target:
                            label += " TRACK"

                        label += " " + state

                        cv2.putText(
                            frame,
                            label,
                            (x, max(18, y - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.48,
                            color,
                            2
                        )

                    # ======================
                    # GREEN HAND LANDMARKS
                    # ======================

                    if hand_seen:
                        draw_hand_points(
                            frame,
                            last_hand
                        )

                    # ======================
                    # GUI HEADER
                    # ======================

                    header = np.zeros(
                        (170, width, 3),
                        dtype=np.uint8
                    )

                    header[:] = COLORS.get(
                        expression,
                        COLORS["IDLE"]
                    )

                    ink = (
                        (0, 0, 0)
                        if expression in (
                            "HAPPY",
                            "SURPRISED"
                        )
                        else (255, 255, 255)
                    )

                    track_text = (
                        f"FACE TRACK: {direction} "
                        f"X:{face_x:.2f} Y:{face_y:.2f}"
                        if face_x is not None
                        else "FACE TRACK: NONE"
                    )

                    lines = [
                        (
                            f"EXPRESSION: {expression} "
                            f"| MODE: {mode}"
                        ),
                        (
                            f"Gesture: {stable} "
                            f"| Brain: {decision.emotion}"
                        ),
                        (
                            f"Verified faces: {len(faces)} "
                            f"| Hand: {int(hand_seen)} "
                            f"| Motion: {motion_ratio:.3f}"
                        ),
                        (
                            f"H_HAPPY: {decision.hormone.get('HAPPY', 0.0):.2f} "
                            f"| H_SURPRISED: {decision.hormone.get('SURPRISED', 0.0):.2f} "
                            f"| H_SAD: {decision.hormone.get('SAD', 0.0):.2f}"
                        ),
                        (
                            f"LOOK: {last_look} "
                            f"| Transport: {output.mode} "
                            f"| Process FPS: {fps:.1f}"
                        ),
                        track_text,
                        (
                            f"H_MOTOR: {motor_drive:.2f} "
                            f"| Stimulus: {stimulus_max:.2f} "
                            f"| Speed: {motor_speed_raw} RAW"
                        )
                    ]

                    for i, line in enumerate(lines):

                        cv2.putText(
                            header,
                            line,
                            (10, 21 + 23 * i),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.50,
                            ink,
                            1,
                            cv2.LINE_AA
                        )

                    display = np.vstack(
                        (header, frame)
                    )

                    cv2.imshow(
                        WINDOW_TITLE,
                        display
                    )

                    # ======================
                    # KEYBOARD
                    # ======================

                    key = cv2.waitKey(1) & 0xFF

                    if key in (
                        ord("q"),
                        ord("Q"),
                        27
                    ):
                        break

                    if key in (
                        ord("+"),
                        ord("=")
                    ):

                        near_threshold = min(
                            0.65,
                            near_threshold + 0.02
                        )

                    elif key in (
                        ord("-"),
                        ord("_")
                    ):

                        near_threshold = max(
                            0.04,
                            near_threshold - 0.02
                        )

    except KeyboardInterrupt:
        print("[STOP] Interrupted")

    finally:

        stop.set()

        if look_bridge is not None:
            look_bridge.close()

        if output is not None:
            output.close()

        if face_detector is not None:
            face_detector.close()

        if camera is not None:
            camera.close()

        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
