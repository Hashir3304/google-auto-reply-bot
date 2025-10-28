import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from threading import Thread
import requests
from flask import Flask, jsonify
from openai import OpenAI

app = Flask(__name__)

# === Environment Variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
LOCATION_ID = os.getenv("LOCATION_ID")
PLACE_ID = os.getenv("PLACE_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

client = OpenAI(api_key=OPENAI_KEY)

class GoogleAuthManager:
    def __init__(self):
        self.access_token = None
        self.token_expiry = None
    
    def refresh_access_token(self):
        """Refresh Google OAuth token using refresh token"""
        try:
            url = "https://oauth2.googleapis.com/token"
            data = {
                'client_id': GOOGLE_CLIENT_ID,
                'client_secret': GOOGLE_CLIENT_SECRET,
                'refresh_token': GOOGLE_REFRESH_TOKEN,
                'grant_type': 'refresh_token'
            }
            
            response = requests.post(url, data=data, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            # Tokens typically expire in 1 hour
            self.token_expiry = datetime.now() + timedelta(seconds=token_data.get('expires_in', 3600))
            
            print("✅ Google access token refreshed successfully")
            return True
            
        except Exception as e:
            print(f"❌ Token refresh failed: {e}")
            send_email("❌ Google Token Refresh Failed", str(e))
            return False
    
    def get_valid_token(self):
        """Get valid access token, refresh if expired"""
        if not self.access_token or not self.token_expiry or datetime.now() >= self.token_expiry:
            print("🔄 Access token expired or missing, refreshing...")
            if not self.refresh_access_token():
                raise Exception("Failed to refresh Google access token")
        return self.access_token

# Initialize auth manager
google_auth = GoogleAuthManager()

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
        return True
    except Exception as e:
        print(f"❌ Email send failed: {e}")
        return False

@app.route("/")
def home():
    return jsonify({
        "status": "✅ Pawsy Prints Auto-Reply Bot is live",
        "endpoints": {
            "/": "Health check",
            "/run-now": "Manual trigger for immediate review processing",
            "/status": "System status check"
        },
        "schedule": "Runs automatically every hour"
    })

@app.route("/status")
def status():
    """System status endpoint"""
    try:
        # Test OpenAI connection
        client.models.list(limit=1)
        openai_status = "✅ Connected"
    except Exception as e:
        openai_status = f"❌ Error: {str(e)}"
    
    try:
        # Test Google Auth
        token = google_auth.get_valid_token()
        google_status = "✅ Connected"
    except Exception as e:
        google_status = f"❌ Error: {str(e)}"
    
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "openai": openai_status,
            "google_auth": google_status,
            "email": "✅ Configured" if GMAIL_USER and GMAIL_APP_PASSWORD else "❌ Not configured"
        }
    })

@app.route("/run-now", methods=["GET"])
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({
        "status": "started", 
        "message": "Manual review processing triggered",
        "timestamp": datetime.now().isoformat()
    })

# === Fetch Google Reviews ===
def get_reviews():
    """Fetch reviews from Google My Business API"""
    try:
        url = f"https://mybusiness.googleapis.com/v4/accounts/{ACCOUNT_ID}/locations/{LOCATION_ID}/reviews"
        headers = {
            "Authorization": f"Bearer {google_auth.get_valid_token()}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 401:
            # Token might be expired, refresh and retry once
            google_auth.refresh_access_token()
            headers["Authorization"] = f"Bearer {google_auth.get_valid_token()}"
            response = requests.get(url, headers=headers, timeout=30)
        
        response.raise_for_status()
        data = response.json()
        reviews = data.get("reviews", [])
        print(f"📝 Found {len(reviews)} reviews")
        return reviews
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to fetch reviews: {e}"
        print(f"❌ {error_msg}")
        send_email("❌ Fetch Reviews Failed", error_msg)
        return []
    except Exception as e:
        error_msg = f"Unexpected error fetching reviews: {e}"
        print(f"❌ {error_msg}")
        send_email("❌ Fetch Reviews Failed", error_msg)
        return []

# === Generate AI Reply ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, kind, and professional thank-you reply from Pawsy Prints under 60 words. "
        "Sound genuine and appreciative. For lower ratings, be understanding and offer to make things right."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints' friendly assistant for public customer replies. Be warm, professional, and appreciative."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=150
        )
        reply = response.choices[0].message.content.strip()
        print(f"🤖 Generated reply for {stars}-star review")
        return reply
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""

# === Post Reply ===
def post_reply(review_id, reply):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{ACCOUNT_ID}/locations/{LOCATION_ID}/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {google_auth.get_valid_token()}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.put(url, headers=headers, json={"comment": reply}, timeout=30)
        if response.status_code == 200:
            print(f"✅ Posted reply for review {review_id}")
            return True
        else:
            print(f"❌ Failed to post reply ({response.status_code}): {response.text}")
            send_email("❌ Post Reply Failed", f"Review ID: {review_id}\nStatus: {response.status_code}\n\n{response.text}")
            return False
    except Exception as e:
        print(f"❌ Error posting reply: {e}")
        send_email("❌ Post Reply Failed", f"Review ID: {review_id}\nError: {str(e)}")
        return False

# === Main Auto-Reply Job ===
def auto_reply_once():
    print(f"🔄 Starting auto-reply job at {datetime.now()}")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    reviews = get_reviews()
    
    if not reviews:
        print("ℹ️ No reviews found or unable to fetch reviews.")
        send_email("🤖 Pawsy Auto-Reply - No Reviews", f"No reviews found in the last 24 hours.\nCheck time: {now}")
        return

    successes, fails = [], []

    for rv in reviews:
        # Skip if already replied
        if rv.get("reviewReply"):
            continue

        # Check review time
        update_time = rv.get("updateTime") or rv.get("createTime")
        if update_time:
            try:
                review_time = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                if review_time < cutoff:
                    continue
            except Exception as e:
                print(f"⚠️ Could not parse time for review {rv.get('reviewId')}: {e}")

        rid = rv.get("reviewId")
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        
        if not text.strip():
            continue

        print(f"📨 Processing {stars}-star review from {name}")
        reply = generate_reply(name, stars, text)
        
        if not reply:
            fails.append((rid, "GPT failed to generate reply"))
            continue

        if post_reply(rid, reply):
            successes.append((name, stars, text, reply))
        else:
            fails.append((rid, "Failed to post reply"))

        # Small delay to be respectful to APIs
        time.sleep(2)

    # Send summary email
    summary = [
        f"🕓 Run: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"✅ Replies sent: {len(successes)}",
        f"❌ Failures: {len(fails)}",
        f"📝 Total reviews processed: {len(reviews)}",
        "",
    ]
    
    for name, stars, text, reply in successes:
        summary.append(f"--- {name} ({stars}★) ---")
        summary.append(f"Review: {text}")
        summary.append(f"Reply: {reply}")
        summary.append("")
    
    for rid, reason in fails:
        summary.append(f"⚠️ {rid}: {reason}")

    email_subject = f"🐾 Pawsy Auto-Reply | {len(successes)} sent, {len(fails)} failed"
    send_email(email_subject, "\n".join(summary))
    print(f"✅ Auto-reply job completed. {len(successes)} successful, {len(fails)} failed")

def loop_hourly():
    """Background thread for hourly execution"""
    print("🕒 Starting hourly review check loop...")
    while True:
        try:
            auto_reply_once()
        except Exception as e:
            print(f"❌ Error in hourly loop: {e}")
            send_email("❌ Auto-Reply Loop Error", str(e))
        
        print("🕒 Sleeping for 1 hour...")
        time.sleep(3600)  # 1 hour

# Start background thread
Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Pawsy Prints Auto-Reply Bot on port {port}")
    app.run(host="0.0.0.0", port=port)
