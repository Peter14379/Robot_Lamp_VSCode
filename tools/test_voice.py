
import sounddevice as sd
import speech_recognition as sr

# USB Webcam microphone
MIC_INDEX = 1
RECORD_SECONDS = 5

device_info = sd.query_devices(MIC_INDEX, "input")
sample_rate = int(device_info["default_samplerate"])

print("=== ROBOT LAMP VOICE TEST ===")
print("Microphone:", device_info["name"])
print("Sample rate:", sample_rate)

print("\nกำลังบันทึกเสียง 5 วินาที...")
print("พูดว่า ดีใจ หรือ โกรธ ได้เลยครับ")

audio = sd.rec(
    int(RECORD_SECONDS * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="int16",
    device=MIC_INDEX
)

sd.wait()
print("บันทึกเสียงเรียบร้อย")
print("กำลังแปลงเสียงเป็นข้อความภาษาไทย...")

recognizer = sr.Recognizer()
audio_data = sr.AudioData(
    audio.tobytes(),
    sample_rate,
    2
)

try:
    text = recognizer.recognize_google(
        audio_data,
        language="th-TH"
    )
    print("\nตรวจจับคำพูด:", text)

    if "ดีใจ" in text:
        print("EMOTION -> HAPPY")
    elif "โกรธ" in text:
        print("EMOTION -> ANGRY")
    elif "ตกใจ" in text:
        print("EMOTION -> SURPRISED")
    elif "เสียใจ" in text:
        print("EMOTION -> SAD")
    elif "ง่วง" in text:
        print("EMOTION -> SLEEPY")
    elif "ปกติ" in text:
        print("EMOTION -> IDLE")
    else:
        print("ไม่พบคำสั่งอารมณ์")

except sr.UnknownValueError:
    print("ไม่สามารถแยกคำพูดได้ ลองพูดให้ชัดขึ้น")

except sr.RequestError as error:
    print("ไม่สามารถเชื่อมต่อบริการรู้จำเสียง:", error)
