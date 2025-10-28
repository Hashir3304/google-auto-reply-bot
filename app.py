import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from threading import Thread
import requests
from flask import Flask, jsonify
import openai

# ========= Logging =========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("pawsy-bot")

app = Flask(__name__)

# === Environment Variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")  # optional seed
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
LOCATION_ID = os.getenv("LOCATION_ID")

openai.api_key = OPENAI_KEY


# ====== Gmail Helper ======
def send_email(subject: str, body: str):
    """Send email notifications"""
    if not (GMAIL_USER and GMAIL_APP_PASSWORD and NOTIFY_EMAIL_TO):
        log.warning("Email not configured; skipping email send.")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = GMAIL_USER
        msg["To"] = NOTIFY_EMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        log.info(f"📧 Email sent: {subject}")
    except Exception as e:
        log.error(f"❌ Email send failed: {e}")


# ====== Token Manager ======
class GoogleAuth:
    def __init__(self):
        self.access_token = GOOGLE_ACCESS_TOKEN
        self.expiry = datetime.now() + timedelta(seconds=60)

    def refresh_token(self):
        log.info("🔄 Refreshing Google access token...")
        url = "https://oauth2.googleapis.com/token"
        data = {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        }
        try:
            r = requests.post(url, data=data, timeout=20)
            r.raise_for_status()
            j = r.json()
            self.access_token = j["access_token"]
            self.expiry = datetime.now() + timedelta(seconds=j.get("expires_in", 3600))
            log.info("✅ Access token refreshed successfully.")
        except Exception as e:
            log.error(f"❌ Failed to refresh token: {e}")
            send_email("❌ Google Token Refresh Failed", str(e))
            raise

    def get_token(self):
        if (not self.access_token) or datetime.now() >= self.expiry:
            self.refresh_token()
        return self.access_token


google_auth = GoogleAuth()


# ====== Google API Helper ======
def google_request(method, url, **kwargs):
    """Unified Google API request with retry/backoff"""
    for attempt in range(5):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {google_auth.get_token()}"
        try:
            r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            if r.status_code == 401:
                log.warning("⚠️ 401 Unauthorized, refreshing token...")
                google_auth.refresh_token()
                continue
            if r.status_code in (429, 500, 502, 503):
                sleep_time = 2 ** attempt
                log.warning(f"⚠️ {r.status_code} error, retrying in {sleep_time}s...")
                time.sleep(sleep_time)
                continue
            return r
        except Exception as e:
            log.error(f"Request failed: {e}")
            time.sleep(2)
    raise Exception("Max retries reached for Google API.")


# ====== Fetch Reviews ======
def get_reviews(account_id, location_id):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews"
    r = google_request("GET", url)
    if r.status_code != 200:
        log.error(f"❌ Failed to fetch reviews: {r.text}")
        send_email("❌ Fetch Reviews Failed", r.text)
        return []
    data = r.json()
    reviews = data.get("reviews", [])
    log.info(f"📝 Found {len(reviews)} reviews.")
    return reviews


# ====== Generate Reply ======
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, concise, and kind reply (under 60 words) from Pawsy Prints. "
        "If the rating is low, be understanding and professional."
    )
    try:
        res = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints' customer care assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        reply = res["choices"][0]["message"]["content"].strip()
        return reply
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""


# ====== Post Reply ======
def post_reply(account_id, location_id, review_id, reply):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply"
    r = google_request("PUT", url, json={"comment": reply})
    if r.status_code == 200:
        log.info(f"✅ Posted reply for review {review_id}")
        return True
    log.error(f"❌ Failed to post reply: {r.text}")
    return False


# ====== Main Logic ======
def auto_reply_once():
    log.info(f"🔄 Auto-reply started at {datetime.now(timezone.utc).isoformat()}")
    if not (ACCOUNT_ID and LOCATION_ID):
        err = "❌ ACCOUNT_ID or LOCATION_ID missing."
        log.error(err)
        send_email("❌ Setup Error", err)
        return

    reviews = get_reviews(ACCOUNT_ID, LOCATION_ID)
    successes, fails = [], []

    for rv in reviews:
        if rv.get("reviewReply"):
            continue
        rid = rv["reviewId"]
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        if not text.strip():
            continue
        reply = generate_reply(name, stars, text)
        if reply and post_reply(ACCOUNT_ID, LOCATION_ID, rid, reply):
            successes.append(name)
        else:
            fails.append(rid)
        time.sleep(2)

    summary = f"✅ {len(successes)} replies sent, ❌ {len(fails)} failed."
    log.info(summary)
    send_email("🐾 Pawsy Auto-Reply Summary", summary)


# ====== Flask Endpoints ======
@app.route("/")
def home():
    return jsonify({
        "status": "✅ Pawsy Prints Auto-Reply Bot is live",
        "manual_trigger": "/run-now",
        "health_check": "/healthz",
        "schedule": "Runs hourly in background"
    })


@app.route("/run-now")
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({"message": "Manual trigger started.", "status": "started"})


@app.route("/healthz")
def healthz():
    """Health endpoint for monitoring"""
    try:
        token_expiry = google_auth.expiry.isoformat()
        return jsonify({
            "status": "healthy",
            "access_token_expiry": token_expiry,
            "account_id": ACCOUNT_ID,
            "location_id": LOCATION_ID,
            "uptime": str(datetime.now(timezone.utc))
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


# ====== Background Loop ======
def loop_hourly():
    log.info("🕒 Starting hourly auto-reply loop...")
    while True:
        auto_reply_once()
        time.sleep(3600)


Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    log.info(f"🚀 Starting Pawsy Prints Auto-Reply Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
