
r"""Robot Lamp: built-in camera + Thai voice + RGB + auto sleep + wake transition.
Run: py -3.11 tools\emotion_lamp_camera.py --camera 0 --port COM3
"""

from pathlib import Path
import argparse
import math
import queue
import threading
import time
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
import serial
import sounddevice as sd
import speech_recognition as sr

# ================= USER SETTINGS =================
CAMERA_INDEX = 0
ESP32_PORT = "COM3"
BAUD_RATE = 115200
NEAR_THRESHOLD = 0.22
STABLE_FRAMES = 8
MIN_GESTURE_AREA = 0.035
SLEEP_TIMEOUT_SECONDS = 30.0   # Inactivity before SLEEPY, seconds
WAKE_IDLE_SECONDS = 1.5        # IDLE time after waking, before HAPPY/SAD/etc.
VOICE_HOLD_SECONDS = 5.0
RECORD_CHUNK_SECONDS = 3.0
MIC_RMS_THRESHOLD = 0.008

MODEL_PATH = Path(__file__).with_name("hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
COLOR_BGR = {
    "IDLE": (255, 180, 50),
    "SURPRISED": (255, 255, 255),
    "ANGRY": (0, 0, 255),
    "HAPPY": (0, 220, 255),
    "SAD": (255, 0, 0),
    "SLEEPY": (255, 0, 150),
}
VOICE_WORDS = (
    ("ดีใจ", "HAPPY"), ("โกรธ", "ANGRY"), ("ตกใจ", "SURPRISED"),
    ("เสียใจ", "SAD"), ("ง่วงนอน", "SLEEPY"), ("ง่วง", "SLEEPY"),
    ("ปกติ", "IDLE"),
)


def recognize_voice(text):
    matches = [(text.find(word), emotion)
               for word, emotion in VOICE_WORDS if word in text]
    return min(matches)[1] if matches else None


def find_microphone(requested_index=None):
    devices = sd.query_devices()
    if requested_index is not None:
        return requested_index, sd.query_devices(requested_index, "input")
    try:
        default_index = int(sd.default.device[0])
        if default_index >= 0 and devices[default_index]["max_input_channels"] > 0:
            return default_index, devices[default_index]
    except (ValueError, IndexError, TypeError, sd.PortAudioError):
        pass
    for index, info in enumerate(devices):
        if info["max_input_channels"] > 0:
            return index, info
    return None, None


def microphone_capture(mic_index, sample_rate, segments, stop_event):
    blocks = queue.Queue(maxsize=100)
    target_frames = int(sample_rate * RECORD_CHUNK_SECONDS)
    frames = []
    collected = 0

    def callback(indata, frame_count, time_info, status):
        if not stop_event.is_set():
            try:
                blocks.put_nowait(indata.copy())
            except queue.Full:
                pass

    try:
        with sd.InputStream(device=mic_index, samplerate=sample_rate,
                            channels=1, dtype="int16", callback=callback):
            print("[MIC] Listening continuously...")
            print("[MIC] Commands: ดีใจ โกรธ ตกใจ เสียใจ ง่วงนอน ปกติ")
            while not stop_event.is_set():
                try:
                    part = blocks.get(timeout=0.3)
                except queue.Empty:
                    continue
                frames.append(part)
                collected += len(part)
                if collected < target_frames:
                    continue
                samples = np.concatenate(frames, axis=0)
                frames.clear()
                collected = 0
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)) / 32768.0)
                if rms < MIC_RMS_THRESHOLD:
                    continue
                audio = sr.AudioData(samples.tobytes(), sample_rate, 2)
                try:
                    segments.put_nowait(audio)
                except queue.Full:
                    try:
                        segments.get_nowait()
                    except queue.Empty:
                        pass
                    segments.put_nowait(audio)
    except Exception as error:
        print(f"[MIC] Capture error: {error}")


def speech_recognition_worker(segments, commands, stop_event):
    recognizer = sr.Recognizer()
    recognizer.operation_timeout = 8
    while not stop_event.is_set():
        try:
            audio = segments.get(timeout=0.3)
        except queue.Empty:
            continue
        try:
            spoken = recognizer.recognize_google(audio, language="th-TH")
            emotion = recognize_voice(spoken)
            print(f"[VOICE] {spoken} -> {emotion or 'no command'}")
            if emotion:
                commands.put((emotion, spoken))
        except sr.UnknownValueError:
            pass
        except sr.RequestError as error:
            print(f"[VOICE] Recognition / internet error: {error}")
        except Exception as error:
            print(f"[VOICE] Error: {error}")


def dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def extended(points, mcp, pip, tip):
    return dist(points[tip], points[mcp]) > 1.48 * dist(points[pip], points[mcp])


def recognize_hand(points, threshold):
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    area = max(0.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))
    index_up = extended(points, 5, 6, 8)
    middle_up = extended(points, 9, 10, 12)
    ring_up = extended(points, 13, 14, 16)
    pinky_up = extended(points, 17, 18, 20)

    if area >= MIN_GESTURE_AREA:
        if middle_up and not index_up and not ring_up and not pinky_up:
            return "ANGRY", area

        folded = not (index_up or middle_up or ring_up or pinky_up)
        thumb_extended = dist(points[4], points[5]) > 1.25 * dist(points[3], points[5])
        if folded and thumb_extended:
            if points[4].y < points[2].y - 0.04:
                return "HAPPY", area
            if points[4].y > points[2].y + 0.04:
                return "SAD", area

    if area >= threshold:
        return "SURPRISED", area
    return "IDLE", area


def open_camera(index):
    camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        camera = cv2.VideoCapture(index)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(f"Cannot open camera {index}; try --camera 0 or --camera 1")
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print(f"[CAMERA] Opened camera index {index}")
    return camera


def open_serial(port):
    if not port:
        print("[ESP32] Preview mode only")
        return None
    try:
        device = serial.Serial(port, BAUD_RATE, timeout=0.02)
        time.sleep(2)
        device.reset_input_buffer()
        device.write(b"IDLE\n")
        print(f"[ESP32] Connected on {port}")
        return device
    except (serial.SerialException, OSError) as error:
        print(f"[ESP32] Connection failed: {error}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Robot Lamp: Camera + Voice + Sleep/Wake")
    parser.add_argument("--camera", type=int, default=CAMERA_INDEX)
    parser.add_argument("--port", default=ESP32_PORT)
    parser.add_argument("--mic-index", type=int, default=None)
    parser.add_argument("--no-mic", action="store_true")
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--sleep-after", type=float, default=SLEEP_TIMEOUT_SECONDS)
    parser.add_argument("--wake-idle", type=float, default=WAKE_IDLE_SECONDS)
    args = parser.parse_args()

    sleep_after = max(5.0, args.sleep_after)
    wake_idle = max(0.0, args.wake_idle)
    near_threshold = NEAR_THRESHOLD

    if not MODEL_PATH.is_file():
        print("[MODEL] Downloading MediaPipe hand model...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)

    stop_event = threading.Event()
    audio_segments = queue.Queue(maxsize=2)
    voice_commands = queue.Queue()
    camera = None
    device = None

    try:
        if not args.no_mic:
            try:
                mic_index, mic_info = find_microphone(args.mic_index)
                if mic_info is None:
                    print("[MIC] No microphone found; camera can still work")
                else:
                    rate = int(mic_info["default_samplerate"])
                    print(f"[MIC] Device #{mic_index}: {mic_info['name']} ({rate} Hz)")
                    threading.Thread(target=microphone_capture,
                                     args=(mic_index, rate, audio_segments, stop_event),
                                     daemon=True).start()
                    threading.Thread(target=speech_recognition_worker,
                                     args=(audio_segments, voice_commands, stop_event),
                                     daemon=True).start()
            except Exception as error:
                print(f"[MIC] Not available: {error}")

        camera = open_camera(args.camera)
        device = open_serial(None if args.preview_only else args.port)
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )

        candidate = "IDLE"
        current_hand = "IDLE"
        stable = 0
        effective = "IDLE"
        voice_emotion = None
        voice_until = 0.0
        forced_sleep = False
        last_activity_at = time.monotonic()
        previous_timestamp = -1

        # Wake transition: SLEEPY -> IDLE (wait) -> pending command
        wake_pending_emotion = None
        wake_until = 0.0

        print(f"[READY] Auto SLEEPY after {sleep_after:g}s")
        print(f"[READY] Wake IDLE time {wake_idle:g}s")
        print("[KEYS] Q=quit, +/-=near, [ / ]=sleep time")

        with mp.tasks.vision.HandLandmarker.create_from_options(options) as detector:
            while True:
                ok, frame = camera.read()
                if not ok:
                    print("[CAMERA] Frame unavailable")
                    break
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_frame = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                timestamp = max(int(time.monotonic() * 1000), previous_timestamp + 1)
                previous_timestamp = timestamp
                detected = detector.detect_for_video(mp_frame, timestamp)

                hand_emotion = "IDLE"
                area = 0.0
                if detected.hand_landmarks:
                    points = detected.hand_landmarks[0]
                    hand_emotion, area = recognize_hand(points, near_threshold)
                    h, w = frame.shape[:2]
                    for p in points:
                        cv2.circle(frame, (int(p.x * w), int(p.y * h)),
                                   3, (0, 255, 0), -1)

                if hand_emotion == candidate:
                    stable += 1
                else:
                    candidate = hand_emotion
                    stable = 1

                now = time.monotonic()
                hand_changed = False
                if stable >= STABLE_FRAMES and candidate != current_hand:
                    current_hand = candidate
                    hand_changed = True

                if hand_changed and current_hand != "IDLE":
                    forced_sleep = False
                    last_activity_at = now
                    if voice_emotion == "SLEEPY":
                        voice_emotion = None
                        voice_until = 0.0

                # A held recognized hand pose counts as activity (except during
                # explicit voice-forced sleep, until a new gesture is seen).
                if current_hand != "IDLE" and not forced_sleep:
                    last_activity_at = now

                voice_event = False
                while True:
                    try:
                        voice_emotion, spoken = voice_commands.get_nowait()
                    except queue.Empty:
                        break
                    voice_event = True
                    print(f"[COMMAND] Voice: {spoken} -> {voice_emotion}")
                    if voice_emotion == "SLEEPY":
                        forced_sleep = True
                        voice_until = 0.0
                    else:
                        forced_sleep = False
                        last_activity_at = now
                        voice_until = now + VOICE_HOLD_SECONDS

                voice_active = voice_emotion is not None and now < voice_until
                inactive_for = now - last_activity_at

                if forced_sleep:
                    new_emotion, source = "SLEEPY", "VOICE SLEEP"
                elif voice_active:
                    new_emotion, source = voice_emotion, "VOICE"
                elif current_hand != "IDLE":
                    new_emotion, source = current_hand, "CAMERA"
                elif inactive_for >= sleep_after:
                    new_emotion, source = "SLEEPY", "AUTO SLEEP"
                else:
                    new_emotion, source = "IDLE", "WAITING"

                # Transition back into the active state. A wake request is
                # latched so that a brief gesture is not lost during IDLE.
                if wake_pending_emotion is not None:
                    if forced_sleep:
                        wake_pending_emotion = None
                    elif now < wake_until:
                        if new_emotion not in ("IDLE", "SLEEPY"):
                            wake_pending_emotion = new_emotion
                        new_emotion, source = "IDLE", "WAKING UP"
                    else:
                        if new_emotion not in ("IDLE", "SLEEPY"):
                            wake_pending_emotion = new_emotion
                        new_emotion, source = wake_pending_emotion, "WAKE COMMAND"
                        wake_pending_emotion = None

                elif effective == "SLEEPY" and (
                    new_emotion not in ("IDLE", "SLEEPY")
                    or (voice_event and new_emotion == "IDLE")
                ):
                    if new_emotion == "IDLE":
                        # Spoken 'ปกติ' wakes directly to IDLE.
                        new_emotion, source = "IDLE", "VOICE WAKE"
                    else:
                        wake_pending_emotion = new_emotion
                        wake_until = now + wake_idle
                        new_emotion, source = "IDLE", "WAKING UP"
                        print(f"[WAKE] IDLE for {wake_idle:g}s, then {wake_pending_emotion}")

                if new_emotion != effective:
                    effective = new_emotion
                    print(f"[EMOTION] {effective} <- {source}")
                    if device is not None:
                        try:
                            device.write((effective + "\n").encode("ascii"))
                            print(f"[SENT] {effective}")
                        except (serial.SerialException, OSError) as error:
                            print(f"[ESP32] Write failed: {error}")
                            device.close()
                            device = None

                if device is not None:
                    try:
                        for _ in range(5):
                            if not device.in_waiting:
                                break
                            line = device.readline().decode("utf-8", errors="replace").strip()
                            if line:
                                print(f"[ESP32] {line}")
                    except (serial.SerialException, OSError) as error:
                        print(f"[ESP32] Read failed: {error}")
                        device.close()
                        device = None

                color = COLOR_BGR[effective]
                cv2.rectangle(frame, (0, 0), (frame.shape[1], 72), color, -1)
                text_color = (0, 0, 0) if effective in ("SURPRISED", "HAPPY") else (255, 255, 255)
                cv2.putText(frame, f"EMOTION: {effective} ({source})", (10, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.67, text_color, 2)

                if wake_pending_emotion is not None:
                    remaining = max(0.0, wake_until - now)
                    status = f"WAKING UP | IDLE {remaining:.1f}s -> {wake_pending_emotion}"
                elif effective == "SLEEPY":
                    status = "SLEEP MODE | Gesture / voice to wake"
                elif current_hand != "IDLE":
                    status = f"Gesture active | Sleep={sleep_after:g}s"
                else:
                    remaining = max(0.0, sleep_after - inactive_for)
                    status = f"Sleep in {remaining:.0f}s | Timeout={sleep_after:g}s"
                cv2.putText(frame, status, (10, 66), cv2.FONT_HERSHEY_SIMPLEX,
                            0.51, text_color, 1)
                cv2.putText(frame, f"Hand area={area:.3f} Near={near_threshold:.2f}",
                            (10, frame.shape[0] - 36), cv2.FONT_HERSHEY_SIMPLEX,
                            0.53, (255, 255, 255), 2)
                cv2.putText(frame, "Q=quit  +/-=near  [ / ]=sleep time",
                            (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX,
                            0.49, (255, 255, 255), 1)
                cv2.imshow("Robot Lamp - Camera + Voice + Auto Sleep", frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), ord("Q")):
                    break
                if key in (ord("+"), ord("=")):
                    near_threshold = min(0.65, near_threshold + 0.02)
                elif key in (ord("-"), ord("_")):
                    near_threshold = max(0.02, near_threshold - 0.02)
                elif key == ord("["):
                    sleep_after = max(5.0, sleep_after - 5.0)
                    print(f"[SETTING] Auto sleep after {sleep_after:g}s")
                elif key == ord("]"):
                    sleep_after = min(3600.0, sleep_after + 5.0)
                    print(f"[SETTING] Auto sleep after {sleep_after:g}s")

    finally:
        stop_event.set()
        if camera is not None:
            camera.release()
        if device is not None:
            device.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
