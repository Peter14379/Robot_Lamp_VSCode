
"""
Robot Lamp FK/IK Dashboard Safety Fix

- Backup existing dashboard files
- Improve Serial error reporting
- Add diagnostic API
- Disable unsafe REAL IK movement
- Keep servo_zeros.json unchanged
"""

from pathlib import Path
from datetime import datetime
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent

APP = ROOT / "dashboard" / "app.py"
UI = ROOT / "dashboard" / "templates" / "index.html"


def change(src, old, new, label):
    if old not in src:
        if new in src:
            print(f"[ALREADY] {label}")
            return src, True

        print(
            f"[NOT MATCHED] {label} - "
            "source differs; do not overwrite"
        )

        return src, False

    return src.replace(old, new, 1), True


def app_changes(original):
    src = original
    ok = True

    src, r = change(
        src,

        '''    started = False
    lines = []

    while time.monotonic() < deadline:''',

        '''    started = False
    lines = []
    observed = []

    while time.monotonic() < deadline:''',

        "Capture raw ESP32 response"
    )

    ok &= r

    src, r = change(
        src,

        '''        if begin in line:
            started = True''',

        '''        if line:
            observed.append(line)
            observed = observed[-40:]

        if begin in line:
            started = True''',

        "Capture console tail"
    )

    ok &= r

    src, r = change(
        src,

        '''    raise TimeoutError(
        f"ESP32 did not complete {command}"
    )''',

        '''    raise TimeoutError(
        f"ESP32 did not complete {command}; "
        f"received={observed[-12:]}"
    )''',

        "Include observed serial lines in timeout"
    )

    ok &= r

    src, r = change(
        src,

        '''    if len(positions) != 4:
        raise RuntimeError(
            "READPOS did not return all four servos"
        )''',

        '''    if len(positions) != 4:
        missing = sorted(set(IDS) - set(positions))
        raise RuntimeError(
            f"READPOS missing servo IDs {missing}; "
            f"ESP32 replies={lines[-12:]}"
        )''',

        "Show missing READPOS IDs"
    )

    ok &= r

    src, r = change(
        src,

        '''    if len(values) != 4:
        raise RuntimeError(
            "READINFO did not return all four servos"
        )''',

        '''    if len(values) != 4:
        missing = sorted(set(IDS) - set(values))
        raise RuntimeError(
            f"READINFO missing servo IDs {missing}; "
            f"ESP32 replies={lines[-12:]}"
        )''',

        "Show missing READINFO IDs"
    )

    ok &= r

    src, r = change(
        src,

        '''    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc),
        ), 503''',

        '''    except Exception as exc:
        with lock:
            state["error"] = str(exc)
        return jsonify(
            ok=False,
            error=str(exc),
        ), 503''',

        "Persist refresh error to status API"
    )

    ok &= r

    insert_before = '''@app.post("/api/refresh")'''

    diagnostic = '''@app.get("/api/diagnostics")
def api_diagnostics():
    with lock:
        now = time.monotonic()
        return jsonify(
            ok=True,
            port=PORT,
            baud=BAUD,
            serial_open=bool(
                device is not None and device.is_open
            ),
            connected=state["connected"],
            info_ready=state["info_ready"],
            last_error=state["error"],
            position_age_s=(
                round(now - state["pos_at"], 1)
                if state["pos_at"] else None
            ),
            info_age_s=(
                round(now - state["info_at"], 1)
                if state["info_at"] else None
            ),
            servo_online={
                str(i): servos[i]["online"]
                for i in IDS
            },
        )


@app.post("/api/refresh")'''

    if '@app.get("/api/diagnostics")' in src:
        print(
            "[ALREADY] Add read-only /api/diagnostics"
        )

    else:
        src, r = change(
            src,
            insert_before,
            diagnostic,
            "Add read-only /api/diagnostics"
        )

        ok &= r

    return src, ok


def html_changes(original):
    src = original

    guard_old = '''async function moveIK() {
    if (moving) return;

    const t = previewIK();'''

    guard_new = '''async function moveIK() {
    if (moving) return;

    // Real IK is blocked until coordinated
    // hardware motion and geometry calibration
    // have been verified.
    if (source === "real") {
        message(
            "ik-result",
            "REAL IK MOVE is disabled: hardware path "
            + "may collide. Preview IK and FK simulation "
            + "remain available.",
            true
        );

        log(
            "REAL IK MOVE blocked: "
            + "no synchronized motion planner"
        );

        return;
    }

    const t = previewIK();'''

    src, ok = change(
        src,
        guard_old,
        guard_new,
        "Block unsafe REAL IK movement"
    )

    return src, ok


def main():
    if not APP.is_file() or not UI.is_file():
        print(
            "Missing dashboard/app.py or "
            "dashboard/templates/index.html"
        )

        return 2

    a = APP.read_text(encoding="utf-8-sig")
    h = UI.read_text(encoding="utf-8-sig")

    if (
        "def transact(" not in a
        or "async function moveIK()" not in h
    ):
        print(
            "Project source does not match "
            "the chat version; no files changed."
        )

        return 2

    new_a, a_ok = app_changes(a)
    new_h, h_ok = html_changes(h)

    if not (a_ok and h_ok):
        print(
            "Patch aborted: at least one expected "
            "block differs. No files changed."
        )

        return 1

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    for path in (APP, UI):
        backup = path.with_name(
            path.name
            + ".before_safetyfix_"
            + stamp
            + ".bak"
        )

        shutil.copy2(path, backup)

        print("Backup:", backup)

    APP.write_text(
        new_a,
        encoding="utf-8"
    )

    UI.write_text(
        new_h,
        encoding="utf-8"
    )

    print(
        "Patched app.py and index.html. "
        "servo_zeros.json and src/main.cpp "
        "were NOT changed."
    )

    print(
        "Run: py -3.11 -m py_compile dashboard/app.py"
    )

    print(
        "Restart Flask, then open "
        "http://127.0.0.1:5000/api/diagnostics"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
