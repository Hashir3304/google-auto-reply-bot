import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from threading import Thread
import requests
import openai
from flask import Flask

app = Flask(__name__)

# === Environment Variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

# === Configure OpenAI (stable version) ===
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

# === Health Check Route ===
@app.route("/")
def home():
    return "✅ Pawsy Prints Auto-Reply Bot — running hourly with Gmail summaries."

# === Fetch Google Reviews ===
def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        send_email("❌ Fetch Reviews Failed", f"Status: {r.status_code}\n\n{r.text}")
        print(f"❌ Failed to fetch reviews: {r.text}")
        return []
    return r.json().get("reviews", [])

# === Generate AI Reply ===
def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a warm, kind, professional thank-you reply from Pawsy Prints under 60 words."
    )
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints’ friendly support assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        send_email("❌ GPT Error", str(e))
        return ""

# === Post Reply to Google ===
def post_reply(review_id, reply):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
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
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)
    reviews = get_reviews()
    if not reviews:
        print("ℹ️ No reviews found.")
        return

    successes, fails = [], []

    for rv in reviews:
        if rv.get("reviewReply"):  # Skip already replied reviews
            continue

        # Only handle reviews from the last 24 hours
        update_time = rv.get("updateTime") or rv.get("createTime")
        if update_time:
            try:
                t = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                if t < cutoff:
                    continue
            except Exception:
                pass

        rid = rv.get("reviewId")
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        if not text:
            continue

        reply = generate_reply(name, stars, text)
        if not reply:
            fails.append((rid, "No GPT reply"))
            continue

        if post_reply(rid, reply):
            successes.append((name, stars, text, reply))
        else:
            fails.append((rid, "Post failed"))

        time.sleep(2)

    # === Summary Email ===
    total = len(reviews)
    body = [
        f"🕓 Run: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"📊 Total reviews fetched: {total}",
        f"✅ Replies sent: {len(successes)}",
        f"❌ Failures: {len(fails)}",
        "",
    ]
    for s in successes:
        name, stars, text, reply = s
        body.append(f"— {name} ({stars}★)\n{text}\n→ {reply}\n")
    for f in fails:
        rid, reason = f
        body.append(f"⚠️ {rid}: {reason}")

    send_email(f"🐾 Pawsy Auto-Reply | {len(successes)} sent, {len(fails)} failed", "\n".join(body))
    print("✅ Summary email sent.\n")

# === Background Hourly Loop ===
def loop_hourly():
    while True:
        auto_reply_once()
        print("🕒 Sleeping 1 hour...\n")
        time.sleep(3600)

Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
