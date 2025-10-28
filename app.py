import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from threading import Thread
import requests
from flask import Flask, jsonify
import openai

# === Flask App ===
app = Flask(__name__)

# === Environment Variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
LOCATION_ID = os.getenv("LOCATION_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

openai.api_key = OPENAI_KEY


# === Gmail Helper ===
def send_email(subject, body):
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = GMAIL_USER
        msg["To"] = NOTIFY_EMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        print(f"📧 Email sent: {subject}")
    except Exception as e:
        print(f"❌ Email send failed: {e}")


# === Google OAuth Manager ===
class GoogleAuthManager:
    def __init__(self):
        self.access_token = None
        self.token_expiry = datetime.now(timezone.utc)

    def refresh_access_token(self):
        """Refresh Google OAuth2 access token using refresh token"""
        try:
            print("🔄 Refreshing Google access token...")
            url = "https://oauth2.googleapis.com/token"
            data = {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            }
            r = requests.post(url, data=data, timeout=30)
            r.raise_for_status()
            token_data = r.json()
            self.access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            print("✅ Google token refreshed successfully")
        except Exception as e:
            print(f"❌ Token refresh failed: {e}")
            send_email("❌ Google Token Refresh Failed", str(e))

    def get_valid_token(self):
        """Return valid token (refresh if expired)"""
        if not self.access_token or datetime.now(timezone.utc) >= self.token_expiry:
            self.refresh_access_token()
        return self.access_token


google_auth = GoogleAuthManager()


# === OpenAI Reply Generator ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a short, friendly, and professional public reply from Pawsy Prints (under 60 words). "
        "Be warm and appreciative. If low rating, express understanding and offer help."
    )
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You write professional customer replies for Pawsy Prints."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""


# === Google Review Fetcher ===
def get_reviews():
    """Fetch reviews from Google My Business API"""
    try:
        url = f"https://mybusiness.googleapis.com/v4/accounts/{ACCOUNT_ID}/locations/{LOCATION_ID}/reviews"
        headers = {"Authorization": f"Bearer {google_auth.get_valid_token()}"}
        r = requests.get(url, headers=headers, timeout=30)

        if r.status_code == 401:
            google_auth.refresh_access_token()
            headers["Authorization"] = f"Bearer {google_auth.get_valid_token()}"
            r = requests.get(url, headers=headers, timeout=30)

        if r.status_code != 200:
            send_email("❌ Fetch Reviews Failed", f"Status: {r.status_code}\n\n{r.text}")
            print(f"❌ Failed to fetch reviews: {r.text}")
            return []
        data = r.json()
        reviews = data.get("reviews", [])
        print(f"📝 Found {len(reviews)} reviews")
        return reviews
    except Exception as e:
        print(f"❌ Error fetching reviews: {e}")
        send_email("❌ Fetch Reviews Failed", str(e))
        return []


# === Post Reply to Review ===
def post_reply(review_id, reply):
    try:
        url = f"https://mybusiness.googleapis.com/v4/accounts/{ACCOUNT_ID}/locations/{LOCATION_ID}/reviews/{review_id}/reply"
        headers = {
            "Authorization": f"Bearer {google_auth.get_valid_token()}",
            "Content-Type": "application/json",
        }
        r = requests.put(url, headers=headers, json={"comment": reply}, timeout=30)
        if r.status_code == 200:
            print(f"✅ Posted reply for review {review_id}")
            return True
        else:
            send_email("❌ Post Reply Failed", f"Review ID: {review_id}\n\n{r.text}")
            print(f"❌ Failed to post reply ({r.status_code}): {r.text}")
            return False
    except Exception as e:
        send_email("❌ Post Reply Failed", f"Error posting reply: {str(e)}")
        return False


# === Auto Reply Core Logic ===
def auto_reply_once():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    reviews = get_reviews()
    successes, fails = [], []

    for rv in reviews:
        if rv.get("reviewReply"):
            continue  # skip if already replied

        review_time = rv.get("updateTime") or rv.get("createTime")
        if review_time:
            try:
                t = datetime.fromisoformat(review_time.replace("Z", "+00:00"))
                if t < cutoff:
                    continue
            except:
                pass

        rid = rv.get("reviewId")
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        if not text.strip():
            continue

        reply = generate_reply(name, stars, text)
        if not reply:
            fails.append((rid, "No reply generated"))
            continue

        if post_reply(rid, reply):
            successes.append((name, stars, text, reply))
        else:
            fails.append((rid, "Failed to post"))

        time.sleep(2)

    # Summary email
    summary = [
        f"🕓 Run: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"✅ Replies sent: {len(successes)}",
        f"❌ Failures: {len(fails)}",
        f"📝 Total reviews fetched: {len(reviews)}",
        "",
    ]
    for name, stars, text, reply in successes:
        summary.append(f"{name} ({stars}★)\n{text}\n→ {reply}\n")
    for rid, reason in fails:
        summary.append(f"⚠️ {rid}: {reason}")

    send_email(f"🐾 Pawsy Auto-Reply Summary ({len(successes)} success, {len(fails)} failed)", "\n".join(summary))
    print("✅ Auto-reply cycle complete.")


# === Background Hourly Loop ===
def loop_hourly():
    print("🕒 Starting hourly auto-reply loop...")
    while True:
        try:
            auto_reply_once()
        except Exception as e:
            print(f"❌ Hourly loop error: {e}")
            send_email("❌ Hourly Loop Error", str(e))
        print("💤 Sleeping 1 hour...\n")
        time.sleep(3600)


# === Flask Routes ===
@app.route("/")
def home():
    return jsonify({
        "status": "✅ Pawsy Prints Auto-Reply Bot is live",
        "manual_trigger": "/run-now",
        "schedule": "Runs hourly in background",
    })


@app.route("/run-now", methods=["GET"])
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({"status": "started", "message": "Manual trigger started."})


# === Run Application ===
Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Pawsy Prints Auto-Reply Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
