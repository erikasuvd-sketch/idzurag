import json
import base64
import os
import io
import requests
import numpy as np
from PIL import Image

# ================================================================
# ТОХИРГОО — Environment variables (Netlify dashboard-д оруулна)
# ================================================================
REMOVE_BG_API_KEY = os.environ.get("REMOVE_BG_API_KEY", "")
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD= os.environ.get("GMAIL_APP_PASSWORD", "")

# Паспортын хэмжээний тодорхойлолт (300 DPI)
PHOTO_SPECS = {
    "passport_35x45": {"width": 413, "height": 531, "face_min": 0.70, "face_max": 0.80, "top_margin": 60},
    "resume_3x4":     {"width": 354, "height": 472, "face_min": 0.65, "face_max": 0.75, "top_margin": 50},
    "student_4x6":    {"width": 472, "height": 709, "face_min": 0.60, "face_max": 0.70, "top_margin": 60},
}

# ================================================================
# CORS headers
# ================================================================
CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
}


def handler(event, context):
    # OPTIONS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        body      = json.loads(event.get("body", "{}"))
        image_b64 = body.get("image", "")
        email     = body.get("email", "")
        photo_type= body.get("photoType", "passport_35x45")

        if not image_b64 or not email:
            return _error("image болон email заавал шаардлагатай.")

        spec = PHOTO_SPECS.get(photo_type, PHOTO_SPECS["passport_35x45"])

        # 1. Base64 → PIL Image
        img_bytes = base64.b64decode(image_b64)
        original  = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

        # 2. Remove.bg — дэвсгэр арилгах
        removed = _remove_background(img_bytes)
        if removed is None:
            # Remove.bg амжилтгүй бол original ашиглана
            removed = original

        # 3. Цагаан дэвсгэр нэмэх
        white_bg = Image.new("RGBA", removed.size, (255, 255, 255, 255))
        white_bg.paste(removed, mask=removed.split()[3])
        img_rgb = white_bg.convert("RGB")

        # 4. Нүүр илрүүлж crop хийх
        result = _crop_to_passport_standard(img_rgb, spec)

        # 5. PIL → Base64
        out_buf = io.BytesIO()
        result.save(out_buf, format="JPEG", quality=95, dpi=(300, 300))
        result_b64 = base64.b64encode(out_buf.getvalue()).decode("utf-8")

        # 6. Gmail-ээр илгээх
        _send_email(email, result_b64, photo_type, spec)

        return {
            "statusCode": 200,
            "headers":    CORS_HEADERS,
            "body": json.dumps({
                "success":            True,
                "message":            "Зураг амжилттай боловсруулагдаж имэйлд илгээгдлээ.",
                "processedImageB64":  result_b64,
                "width":              spec["width"],
                "height":             spec["height"],
            }),
        }

    except Exception as e:
        print(f"ERROR: {e}")
        return _error(f"Серверийн алдаа: {str(e)}")


# ================================================================
# REMOVE.BG — дэвсгэр арилгах
# ================================================================
def _remove_background(img_bytes):
    try:
        resp = requests.post(
            "https://api.remove.bg/v1.0/removebg",
            files={"image_file": ("photo.jpg", img_bytes, "image/jpeg")},
            data={"size": "auto", "bg_color": "ffffff", "format": "png"},
            headers={"X-Api-Key": REMOVE_BG_API_KEY},
            timeout=30,
        )
        if resp.status_code == 200:
            return Image.open(io.BytesIO(resp.content)).convert("RGBA")
        print(f"Remove.bg error: {resp.status_code} {resp.text}")
        return None
    except Exception as e:
        print(f"Remove.bg exception: {e}")
        return None


# ================================================================
# ПАСПОРТЫН СТАНДАРТ CROP
#
# Алгоритм:
#   1. OpenCV Haar cascade — нүүр илрүүлнэ
#   2. Нүүрний байрлалыг үндэслэн crop window тооцно
#   3. Нүүр нийт зургийн face_min~face_max хувийг эзэлнэ
#   4. Зургийн дээд талд top_margin px зай үлдээнэ
#   5. Яг target хэмжээнд resize хийнэ
# ================================================================
def _crop_to_passport_standard(img_rgb, spec):
    import cv2

    target_w = spec["width"]
    target_h = spec["height"]
    face_min  = spec["face_min"]
    face_max  = spec["face_max"]
    top_margin= spec["top_margin"]

    # PIL → OpenCV (numpy)
    img_np = np.array(img_rgb)
    gray   = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    # Haar cascade нүүр илрүүлэгч
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )

    h_orig, w_orig = img_np.shape[:2]

    if len(faces) == 0:
        # Нүүр олдохгүй бол голоос crop хийнэ
        return _center_crop_resize(img_rgb, target_w, target_h)

    # Хамгийн том нүүрийг сонгоно
    face_x, face_y, face_w, face_h = max(faces, key=lambda f: f[2] * f[3])

    # Нүүрний голын x цэг
    face_cx = face_x + face_w // 2

    # Зорилтот crop хэмжээ тооцох
    # Нүүр нийт зургийн 75% эзэлнэ гэж тооцно (face_min/face_max дундаж)
    face_ratio  = (face_min + face_max) / 2
    crop_h      = int(face_h / face_ratio)
    crop_w      = int(crop_h * target_w / target_h)

    # Crop-ын дээд талын y — нүүрнээс дээш top_margin үлдээнэ
    crop_y = face_y - top_margin
    crop_x = face_cx - crop_w // 2

    # Хил хязгаар тохируулах
    crop_x = max(0, min(crop_x, w_orig - crop_w))
    crop_y = max(0, min(crop_y, h_orig - crop_h))

    # Crop хэмжээ зургаас гарахгүй байх
    crop_w = min(crop_w, w_orig - crop_x)
    crop_h = min(crop_h, h_orig - crop_y)

    # Crop
    cropped = img_rgb.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))

    # Target хэмжээнд resize
    return cropped.resize((target_w, target_h), Image.LANCZOS)


def _center_crop_resize(img, target_w, target_h):
    """Нүүр олдохгүй үед голоос crop хийнэ."""
    w, h   = img.size
    ratio  = target_w / target_h
    if w / h > ratio:
        new_w = int(h * ratio)
        left  = (w - new_w) // 2
        img   = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / ratio)
        top   = (h - new_h) // 4  # дээш хуруу
        img   = img.crop((0, top, w, top + new_h))
    return img.resize((target_w, target_h), Image.LANCZOS)


# ================================================================
# GMAIL — имэйл илгээх
# ================================================================
def _send_email(to_email, img_b64, photo_type, spec):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from email.mime.base      import MIMEBase
    from email                import encoders

    labels = {
        "passport_35x45": "Гадаад паспорт (35×45мм)",
        "resume_3x4":     "Анкет / Ажлын үнэмлэх (3×4см)",
        "student_4x6":    "Оюутны үнэмлэх (4×6см)",
    }

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_USER
    msg["To"]      = to_email
    msg["Subject"] = "Таны цээж зураг бэлэн боллоо ✅ — IDzurag"

    body = f"""Сайн байна уу,

Таны захиалсан цээж зураг бэлэн боллоо!

📋 Захиалгын мэдээлэл:
   • Хэмжээ: {labels.get(photo_type, photo_type)}
   • Пиксел: {spec['width']} × {spec['height']} px
   • Нягтрал: 300 DPI (хэвлэхэд бэлэн)
   • Дэвсгэр: Цагаан (ICAO стандарт)

Зургаа хавсралтаас татаж аваад фото цехд хэвлүүлнэ үү.

Баярлалаа!
IDzurag баг
idzurag.mn"""

    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Зургийг хавсаргах
    img_bytes = base64.b64decode(img_b64)
    part = MIMEBase("image", "jpeg")
    part.set_payload(img_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", 'attachment; filename="ceej-zurag-idzurag.jpg"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())


# ================================================================
# Helper
# ================================================================
def _error(msg):
    return {
        "statusCode": 400,
        "headers":    CORS_HEADERS,
        "body":       json.dumps({"success": False, "error": msg}),
    }
