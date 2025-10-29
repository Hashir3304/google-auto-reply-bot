import os, time, smtplib, requests
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask, jsonify

app = Flask(__name__)

# === Environment Variables ===
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GMAIL_USER           = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO      = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

# === Gmail Helper ===
def send_email(subject, body):
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, GMAIL_USER, NOTIFY_EMAIL_TO
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        print(f"📧 Email sent: {subject}")
    except Exception as e:
        print(f"❌ Email send failed: {e}")

# === Google OAuth Token Manager ===
class GoogleAuth:
    def __init__(self):
        self.access_token, self.expiry = None, None

    def refresh_token(self):
        print("🔄 Refreshing Google access token...")
        data = {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        }
        try:
            r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
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

# === Google Business API ===
def get_account_and_location():
    token = google_auth.get_token()
    headers = {"Authorization": f"Bearer {token}"}
    acc = requests.get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", headers=headers)
    acc.raise_for_status()
    account_id = acc.json()["accounts"][0]["name"].split("/")[-1]

    loc_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{account_id}/locations?readMask=name,title,websiteUri"
    loc = requests.get(loc_url, headers=headers)
    loc.raise_for_status()
    location_id = loc.json()["locations"][0]["name"].split("/")[-1]
    return account_id, location_id

def get_reviews(account_id, location_id):
    token = google_auth.get_token()
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(
        f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews",
        headers=headers, timeout=20)
    if r.status_code != 200:
        send_email("❌ Fetch Reviews Failed", r.text)
        print(f"❌ Failed to fetch reviews: {r.text}")
        return []
    return r.json().get("reviews", [])

# === Gemini Reply Generator ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, concise, kind reply (under 60 words) from Pawsy Prints. "
        "If the rating is low, be professional and understanding."
    )
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
        res = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [
                    {"role": "user", "parts": [{"text": prompt}]}
                ]
            },
            timeout=20
        )
        res.raise_for_status()
        data = res.json()
        reply = (
            data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
        )
        print(f"🤖 Generated reply: {reply[:60]}...")
        return reply
    except Exception as e:
        print(f"❌ Gemini API error: {e}")
        send_email("❌ Gemini API Error", str(e))
        return ""

# === Post Reply to Google ===
def post_reply(account_id, location_id, review_id, reply):
    token = google_auth.get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.put(
        f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply",
        headers=headers, json={"comment": reply})
    if r.status_code == 200:
        print(f"✅ Posted reply for review {review_id}")
        return True
    print(f"❌ Failed to post reply: {r.text}")
    return False

# === Main Logic ===
def auto_reply_once():
    print(f"🔄 Auto-reply job started at {datetime.now(timezone.utc)}")
    try:
        account_id, location_id = get_account_and_location()
        reviews = get_reviews(account_id, location_id)
    except Exception as e:
        send_email("❌ Setup Failed", str(e))
        print(f"❌ Setup failed: {e}")
        return

    successes, fails = [], []
    for rv in reviews:
        if rv.get("reviewReply"): 
            continue
        rid = rv["reviewId"]
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text  = rv.get("comment", "")
        if not text.strip(): 
            continue
        reply = generate_reply(name, stars, text)
        if reply and post_reply(account_id, location_id, rid, reply):
            successes.append(name)
        else:
            fails.append(rid)
        time.sleep(2)

    summary = f"✅ {len(successes)} replies sent, ❌ {len(fails)} failed."
    print(summary)
    send_email("🐾 Pawsy Auto-Reply Summary", summary)

# === Hourly Loop ===
def loop_hourly():
    print("🕒 Starting hourly auto-reply loop...")
    while True:
        auto_reply_once()
        time.sleep(3600)

# === Flask Routes ===
@app.route("/")
def home():
    return jsonify({
        "status": "✅ Pawsy Prints Gemini Auto-Reply Bot is live",
        "manual_trigger": "/run-now",
        "health": "/healthz",
        "schedule": "Runs hourly in background"
    })

@app.route("/run-now")
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({"message": "Manual trigger started.", "status": "started"})

@app.route("/healthz")
def healthz():
    try:
        gemini_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
        ping = requests.post(
            gemini_url,
            headers={"Content-Type": "application/json"},
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [
                    {"role": "user", "parts": [{"text": "ping"}]}
                ]
            },
            timeout=10
        )
        gemini_status = ping.status_code
        google_token_expiry = google_auth.expiry.isoformat() if google_auth.expiry else "unknown"
        return jsonify({
            "status": "healthy" if gemini_status == 200 else "Gemini issue",
            "gemini_status": gemini_status,
            "google_token_expiry": google_token_expiry,
            "uptime": datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500

Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Pawsy Prints Gemini Auto-Reply Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
