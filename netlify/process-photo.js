const https = require("https");
const http = require("http");

// ================================================================
// ТОХИРГОО — Netlify Environment Variables
// ================================================================
const REMOVE_BG_API_KEY  = process.env.REMOVE_BG_API_KEY  || "";
const GMAIL_USER         = process.env.GMAIL_USER         || "";
const GMAIL_APP_PASSWORD = process.env.GMAIL_APP_PASSWORD || "";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json",
};

// Паспортын хэмжээний тодорхойлолт
const PHOTO_SPECS = {
  passport_35x45: { width: 413,  height: 531,  label: "Гадаад паспорт (35×45мм)" },
  resume_3x4:     { width: 354,  height: 472,  label: "Анкет / Ажлын үнэмлэх (3×4см)" },
  student_4x6:    { width: 472,  height: 709,  label: "Оюутны үнэмлэх (4×6см)" },
};

exports.handler = async (event) => {
  // OPTIONS preflight
  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 200, headers: CORS_HEADERS, body: "" };
  }

  try {
    const body      = JSON.parse(event.body || "{}");
    const imageB64  = body.image     || "";
    const email     = body.email     || "";
    const photoType = body.photoType || "passport_35x45";

    if (!imageB64 || !email) {
      return errorResponse("image болон email заавал шаардлагатай.");
    }

    const spec = PHOTO_SPECS[photoType] || PHOTO_SPECS.passport_35x45;

    // 1. Remove.bg — дэвсгэр арилгах
    const removedB64 = await removeBackground(imageB64);

    // 2. Gmail — боловсруулсан зургийг илгээх
    await sendEmail(email, removedB64 || imageB64, spec);

    return {
      statusCode: 200,
      headers: CORS_HEADERS,
      body: JSON.stringify({
        success: true,
        message: "Зураг амжилттай боловсруулагдаж имэйлд илгээгдлээ.",
      }),
    };

  } catch (err) {
    console.error("Handler error:", err);
    return errorResponse("Серверийн алдаа: " + err.message);
  }
};

// ================================================================
// REMOVE.BG
// ================================================================
async function removeBackground(imageB64) {
  return new Promise((resolve) => {
    const imageBuffer = Buffer.from(imageB64, "base64");

    // Build multipart form data
    const boundary = "----FormBoundary" + Date.now();
    const parts = [];

    // image_file_b64 field
    parts.push(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="image_file_b64"\r\n\r\n` +
      imageB64 + "\r\n"
    );
    parts.push(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="size"\r\n\r\nauto\r\n`
    );
    parts.push(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="bg_color"\r\n\r\nffffff\r\n`
    );
    parts.push(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="format"\r\n\r\njpg\r\n`
    );
    parts.push(`--${boundary}--\r\n`);

    const bodyStr = parts.join("");
    const bodyBuf = Buffer.from(bodyStr, "utf-8");

    const options = {
      hostname: "api.remove.bg",
      path:     "/v1.0/removebg",
      method:   "POST",
      headers:  {
        "X-Api-Key":      REMOVE_BG_API_KEY,
        "Content-Type":   `multipart/form-data; boundary=${boundary}`,
        "Content-Length": bodyBuf.length,
      },
    };

    const req = https.request(options, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        if (res.statusCode === 200) {
          const result = Buffer.concat(chunks).toString("base64");
          resolve(result);
        } else {
          console.error("Remove.bg error:", res.statusCode, Buffer.concat(chunks).toString());
          resolve(null);
        }
      });
    });

    req.on("error", (e) => {
      console.error("Remove.bg request error:", e);
      resolve(null);
    });

    req.write(bodyBuf);
    req.end();
  });
}

// ================================================================
// GMAIL — SMTP илгээх
// ================================================================
async function sendEmail(toEmail, imageB64, spec) {
  // Base64 encoded credentials for SMTP AUTH LOGIN
  const userB64 = Buffer.from(GMAIL_USER).toString("base64");
  const passB64 = Buffer.from(GMAIL_APP_PASSWORD).toString("base64");

  const boundary = "----MailBoundary" + Date.now();
  const imageBuffer = Buffer.from(imageB64, "base64");

  const bodyText =
    `Сайн байна уу,\r\n\r\n` +
    `Таны захиалсан цээж зураг бэлэн боллоо!\r\n\r\n` +
    `📋 Захиалгын мэдээлэл:\r\n` +
    `   • Хэмжээ: ${spec.label}\r\n` +
    `   • Пиксел: ${spec.width} × ${spec.height} px\r\n` +
    `   • Нягтрал: 300 DPI (хэвлэхэд бэлэн)\r\n` +
    `   • Дэвсгэр: Цагаан (ICAO стандарт)\r\n\r\n` +
    `Зургаа хавсралтаас татаж аваад фото цехд хэвлүүлнэ үү.\r\n\r\n` +
    `Баярлалаа!\r\nIDzurag баг\r\nidzurag.mn\r\n`;

  const message =
    `From: IDzurag <${GMAIL_USER}>\r\n` +
    `To: ${toEmail}\r\n` +
    `Subject: =?UTF-8?B?${Buffer.from("Таны цээж зураг бэлэн боллоо ✅ — IDzurag").toString("base64")}?=\r\n` +
    `MIME-Version: 1.0\r\n` +
    `Content-Type: multipart/mixed; boundary="${boundary}"\r\n\r\n` +
    `--${boundary}\r\n` +
    `Content-Type: text/plain; charset="UTF-8"\r\n\r\n` +
    bodyText + `\r\n` +
    `--${boundary}\r\n` +
    `Content-Type: image/jpeg; name="ceej-zurag-idzurag.jpg"\r\n` +
    `Content-Transfer-Encoding: base64\r\n` +
    `Content-Disposition: attachment; filename="ceej-zurag-idzurag.jpg"\r\n\r\n` +
    imageB64 + `\r\n` +
    `--${boundary}--\r\n`;

  return new Promise((resolve, reject) => {
    const client = new SMTPClient("smtp.gmail.com", 465, true);
    client.send(GMAIL_USER, toEmail, message, userB64, passB64)
      .then(resolve)
      .catch(reject);
  });
}

// ================================================================
// Minimal SMTP client (TLS)
// ================================================================
class SMTPClient {
  constructor(host, port, secure) {
    this.host   = host;
    this.port   = port;
    this.secure = secure;
  }

  send(from, to, message, userB64, passB64) {
    return new Promise((resolve, reject) => {
      const tls  = require("tls");
      const lines = [];
      let step    = 0;

      const socket = tls.connect({ host: this.host, port: this.port }, () => {});

      socket.on("data", (data) => {
        const resp = data.toString();
        console.log("SMTP <<", resp.trim());

        if (step === 0 && resp.startsWith("220")) {
          socket.write(`EHLO idzurag.mn\r\n`); step = 1;
        } else if (step === 1 && resp.includes("250")) {
          socket.write(`AUTH LOGIN\r\n`); step = 2;
        } else if (step === 2 && resp.startsWith("334")) {
          socket.write(userB64 + "\r\n"); step = 3;
        } else if (step === 3 && resp.startsWith("334")) {
          socket.write(passB64 + "\r\n"); step = 4;
        } else if (step === 4 && resp.startsWith("235")) {
          socket.write(`MAIL FROM:<${from}>\r\n`); step = 5;
        } else if (step === 5 && resp.startsWith("250")) {
          socket.write(`RCPT TO:<${to}>\r\n`); step = 6;
        } else if (step === 6 && resp.startsWith("250")) {
          socket.write(`DATA\r\n`); step = 7;
        } else if (step === 7 && resp.startsWith("354")) {
          socket.write(message + "\r\n.\r\n"); step = 8;
        } else if (step === 8 && resp.startsWith("250")) {
          socket.write(`QUIT\r\n`); step = 9;
          resolve();
        } else if (resp.startsWith("5")) {
          reject(new Error("SMTP error: " + resp.trim()));
          socket.destroy();
        }
      });

      socket.on("error", reject);
    });
  }
}

function errorResponse(msg) {
  return {
    statusCode: 400,
    headers:    CORS_HEADERS,
    body:       JSON.stringify({ success: false, error: msg }),
  };
}
