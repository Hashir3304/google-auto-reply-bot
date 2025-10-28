import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from threading import Thread
import requests
from flask import Flask, jsonify
import openai

app = Flask(__name__)

# === Environment Variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
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


# === Token Manager ===
class GoogleAuth:
    def __init__(self):
        self.access_token = None
        self.expiry = None

    def refresh_token(self):
        """Refresh Google OAuth2 access token using refresh_token"""
        print("🔄 Refreshing Google access token...")
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
            print("✅ Access token refreshed successfully.")
        except Exception as e:
            print(f"❌ Failed to refresh token: {e}")
            send_email("❌ Google Token Refresh Failed", str(e))

    def get_token(self):
        if not self.access_token or datetime.now() >= self.expiry:
            self.refresh_token()
        return self.access_token


google_auth = GoogleAuth()


# === Google API Helpers ===
def get_account_and_location():
    """Detect account + location automatically"""
    token = google_auth.get_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Get account
    acc_url = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
    acc_resp = requests.get(acc_url, headers=headers)
    if acc_resp.status_code != 200:
        raise Exception(f"Failed to get account: {acc_resp.text}")
    account_id = acc_resp.json()["accounts"][0]["name"].split("/")[-1]

    # Get location
    loc_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{account_id}/locations?readMask=name,title,websiteUri"
    loc_resp = requests.get(loc_url, headers=headers)
    if loc_resp.status_code != 200:
        raise Exception(f"Failed to get location: {loc_resp.text}")
    location_id = loc_resp.json()["locations"][0]["name"].split("/")[-1]
    return account_id, location_id


# === Review Fetch ===
def get_reviews(account_id, location_id):
    token = google_auth.get_token()
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print(f"❌ Failed to fetch reviews: {r.text}")
        send_email("❌ Fetch Reviews Failed", r.text)
        return []
    return r.json().get("reviews", [])


# === AI Reply Generator ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, concise, and kind reply (under 60 words) from Pawsy Prints."
        "If the rating is low, be understanding and professional."
    )
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints' customer care assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""


# === Post Reply ===
def post_reply(account_id, location_id, review_id, reply):
    token = google_auth.get_token()
    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.put(url, headers=headers, json={"comment": reply})
    if r.status_code == 200:
        print(f"✅ Posted reply for review {review_id}")
        return True
    else:
        print(f"❌ Failed to post reply: {r.text}")
        return False


# === Main Logic ===
def auto_reply_once():
    print(f"🔄 Auto-reply job started at {datetime.now(timezone.utc)}")
    try:
        account_id, location_id = get_account_and_location()
        reviews = get_reviews(account_id, location_id)
    except Exception as e:
        print(f"❌ Setup failed: {e}")
        send_email("❌ Setup Failed", str(e))
        return

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
        if reply and post_reply(account_id, location_id, rid, reply):
            successes.append((name, stars, text, reply))
        else:
            fails.append((rid, "Failed"))
        time.sleep(2)

    summary = f"✅ {len(successes)} replies sent, ❌ {len(fails)} failed."
    send_email("🐾 Pawsy Auto-Reply Summary", summary)
    print(summary)


# === Background Loop ===
def loop_hourly():
    print("🕒 Starting hourly auto-reply loop...")
    while True:
        auto_reply_once()
        time.sleep(3600)


# === Flask Endpoints ===
@app.route("/")
def home():
    return jsonify({
        "status": "✅ Pawsy Prints Auto-Reply Bot is live",
        "manual_trigger": "/run-now",
        "schedule": "Runs hourly in background"
    })


@app.route("/run-now")
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({"message": "Manual trigger started.", "status": "started"})


Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Pawsy Prints Auto-Reply Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
