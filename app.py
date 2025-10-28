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
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
PLACE_ID = os.getenv("PLACE_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

client = OpenAI(api_key=OPENAI_KEY)

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
    return "✅ Pawsy Prints Auto-Reply Bot is live — runs hourly and on-demand via /run-now."

@app.route("/run-now", methods=["GET"])
def run_now():
    Thread(target=auto_reply_once).start()
    return jsonify({"status": "started", "message": "Manual trigger started."})

# === Debug Endpoints ===
@app.route("/debug-status", methods=["GET"])
def debug_status():
    """Check if background thread is running"""
    import threading
    threads = []
    for thread in threading.enumerate():
        threads.append({
            "name": thread.name,
            "daemon": thread.daemon,
            "alive": thread.is_alive()
        })
    
    return jsonify({
        "status": "running",
        "threads": threads,
        "total_threads": threading.active_count(),
        "timestamp": datetime.now().isoformat()
    })

@app.route("/test-now", methods=["GET"])
def test_now():
    """Immediate test with detailed output"""
    try:
        print("🔄 Manual test triggered via /test-now")
        result = auto_reply_once()
        return jsonify({
            "status": "completed", 
            "message": "Test run finished",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        })

@app.route("/check-reviews", methods=["GET"])
def check_reviews():
    """Check reviews without replying"""
    try:
        reviews = get_reviews()
        review_data = []
        
        for rv in reviews:
            review_data.append({
                "review_id": rv.get("reviewId"),
                "stars": rv.get("starRating"),
                "comment_preview": (rv.get("comment", "")[:100] + "...") if rv.get("comment") else "No comment",
                "has_reply": bool(rv.get("reviewReply")),
                "reviewer": rv.get("reviewer", {}).get("displayName", "Unknown")
            })
        
        return jsonify({
            "total_reviews": len(reviews),
            "reviews_without_replies": len([r for r in reviews if not r.get("reviewReply")]),
            "reviews": review_data
        })
    except Exception as e:
        return jsonify({"error": str(e)})

# === Fetch Google Reviews ===
def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    print(f"🔍 Fetching reviews from: {url}")
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        error_msg = f"Status: {r.status_code}\n\n{r.text}"
        print(f"❌ Failed to fetch reviews: {error_msg}")
        send_email("❌ Fetch Reviews Failed", error_msg)
        return []
    print(f"✅ Successfully fetched {len(r.json().get('reviews', []))} reviews")
    return r.json().get("reviews", [])

# === Generate AI Reply ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, kind, and professional thank-you reply from Pawsy Prints under 60 words."
    )
    try:
        print(f"🤖 Generating AI reply for {stars}-star review...")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints' friendly assistant for public customer replies."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=150
        )
        reply = response.choices[0].message.content.strip()
        print(f"✅ AI reply generated: {reply[:50]}...")
        return reply
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""

# === Post Reply ===
def post_reply(review_id, reply):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    print(f"📤 Posting reply to review {review_id}...")
    r = requests.put(url, headers=headers, json={"comment": reply}, timeout=30)
    if r.status_code == 200:
        print(f"✅ Posted reply for {review_id}")
        return True
    else:
        print(f"❌ Failed to post reply ({r.status_code}): {r.text}")
        send_email("❌ Post Reply Failed", f"Review ID: {review_id}\n\n{r.text}")
        return False

# === Main Auto-Reply Job ===
def auto_reply_once():
    print(f"🔄 Starting auto-reply job at {datetime.now()}")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    reviews = get_reviews()
    if not reviews:
        print("ℹ️ No reviews found.")
        send_email("🤖 Pawsy Auto-Reply - No Reviews", f"No reviews found in the last 24 hours.\nCheck time: {now}")
        return

    successes, fails = [], []

    for rv in reviews:
        if rv.get("reviewReply"):
            print(f"⏩ Skipping review {rv.get('reviewId')} - already has reply")
            continue

        update_time = rv.get("updateTime") or rv.get("createTime")
        if update_time:
            try:
                t = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                if t < cutoff:
                    print(f"⏩ Skipping review {rv.get('reviewId')} - older than 24h")
                    continue
            except Exception:
                pass

        rid = rv.get("reviewId")
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        if not text:
            print(f"⏩ Skipping review {rid} - no comment text")
            continue

        print(f"📨 Processing {stars}-star review from {name}")
        reply = generate_reply(name, stars, text)
        if not reply:
            fails.append((rid, "GPT failed"))
            continue

        if post_reply(rid, reply):
            successes.append((name, stars, text, reply))
        else:
            fails.append((rid, "Post failed"))

        time.sleep(2)

    summary = [
        f"🕓 Run: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"✅ Replies sent: {len(successes)}",
        f"❌ Failures: {len(fails)}",
        "",
    ]
    for s in successes:
        name, stars, text, reply = s
        summary.append(f"— {name} ({stars}★)\n{text}\n→ {reply}\n")
    for f in fails:
        rid, reason = f
        summary.append(f"⚠️ {rid}: {reason}")

    email_sent = send_email(f"🐾 Pawsy Auto-Reply | {len(successes)} sent, {len(fails)} failed", "\n".join(summary))
    if email_sent:
        print("✅ Summary email sent.")
    else:
        print("❌ Failed to send summary email.")
    print(f"✅ Auto-reply job completed. Success: {len(successes)}, Failures: {len(fails)}")

def loop_hourly():
    print("🕒 Starting hourly review check loop...")
    while True:
        try:
            auto_reply_once()
        except Exception as e:
            print(f"❌ Error in hourly loop: {e}")
            send_email("❌ Auto-Reply Loop Error", str(e))
        
        print("🕒 Sleeping for 1 hour...")
        time.sleep(3600)

# Start background thread with error handling
def start_background_thread():
    try:
        thread = Thread(target=loop_hourly, daemon=True)
        thread.start()
        print(f"✅ Background thread started: {thread.name}")
        return True
    except Exception as e:
        print(f"❌ Failed to start background thread: {e}")
        return False

print("🚀 Starting Pawsy Prints Auto-Reply Bot...")
if start_background_thread():
    print("✅ Background thread started successfully")
else:
    print("❌ Failed to start background thread")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
