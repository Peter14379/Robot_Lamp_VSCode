
"""Install Flask emotion API.

Run:
py -3.11 tools/install_emotion_api.py

Creates backup before modifying dashboard/app.py.
"""

from pathlib import Path
from datetime import datetime

import ast
import shutil


ROOT = Path(__file__).resolve().parent.parent
APP_FILE = ROOT / "dashboard" / "app.py"


API = '''

# =====================================
# EMOTION BRIDGE
# Camera/Voice -> Flask -> ESP32
# =====================================

@app.post("/api/emotion")
def api_emotion():
    global port

    data = request.get_json(silent=True) or {}
    emotion = data.get("emotion", "")

    allowed = {
        "IDLE",
        "HAPPY",
        "ANGRY",
        "SURPRISED",
        "SAD",
        "SLEEPY",
    }

    if type(emotion) is not str or emotion not in allowed:
        return jsonify(
            ok=False,
            error="Unknown emotion"
        ), 400

    with state_lock:
        if state["busy"]:
            return jsonify(
                ok=False,
                error="Servo movement in progress"
            ), 409

    try:
        with serial_lock:

            with state_lock:
                if state["busy"]:
                    return jsonify(
                        ok=False,
                        error="Servo movement in progress"
                    ), 409

            if port is None or not port.is_open:
                port = serial.Serial(
                    PORT,
                    BAUD,
                    timeout=0.12,
                    write_timeout=0.8
                )

                time.sleep(1.8)

            port.reset_input_buffer()

            port.write(
                (emotion + "\\\\n").encode("ascii")
            )

            port.flush()

            lines = []
            deadline = time.monotonic() + 3.0

            while time.monotonic() < deadline:

                raw = port.readline()

                if not raw:
                    continue

                line = raw.decode(
                    "utf-8",
                    errors="replace"
                ).strip()

                if not line:
                    continue

                lines.append(line)

                if line == f"[EMOTION] {emotion}":
                    return jsonify(
                        ok=True,
                        emotion=emotion,
                        acknowledged=True
                    )

                if line.startswith("[ERROR]"):
                    return jsonify(
                        ok=False,
                        error=line
                    ), 409

            raise TimeoutError(
                f"ESP32 did not acknowledge "
                f"{emotion}; {lines[-8:]}"
            )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 503


@app.get("/api/emotion/capabilities")
def api_emotion_capabilities():

    return jsonify(
        ok=True,
        emotions=[
            "IDLE",
            "HAPPY",
            "ANGRY",
            "SURPRISED",
            "SAD",
            "SLEEPY",
        ],
        protocol=(
            "ESP32 serial emotion commands, "
            "no auto ARM"
        )
    )

'''


def main():

    if not APP_FILE.is_file():
        raise SystemExit(
            f"Missing file: {APP_FILE}"
        )

    source = APP_FILE.read_text(
        encoding="utf-8-sig"
    )

    if (
        '@app.post("/api/emotion")' in source
        or "def api_emotion():" in source
    ):
        print(
            "Emotion API already exists. "
            "No change."
        )
        return

    required = (
        "serial_lock",
        "state_lock",
        'state["busy"]',
        "port = None",
        "app = Flask(",
        "def transact(",
    )

    missing = [
        item
        for item in required
        if item not in source
    ]

    if missing:
        raise SystemExit(
            "Unknown app.py layout. "
            "No files changed. "
            f"Missing: {missing}"
        )

    marker = (
        'if __name__ == "__main__":'
    )

    if marker not in source:
        raise SystemExit(
            "Cannot find Flask start marker."
        )

    new_source = source.replace(
        marker,
        API + marker,
        1
    )

    # Check Python syntax before replacing file.
    ast.parse(
        new_source,
        filename=str(APP_FILE)
    )

    backup = APP_FILE.with_name(
        APP_FILE.name
        + ".before_emotion_"
        + datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        + ".bak"
    )

    shutil.copy2(
        APP_FILE,
        backup
    )

    APP_FILE.write_text(
        new_source,
        encoding="utf-8"
    )

    print(
        "Installed /api/emotion "
        "and /api/emotion/capabilities"
    )

    print(
        "Backup created:",
        backup
    )

    print(
        "Restart Flask to enable the API."
    )


if __name__ == "__main__":
    main()
