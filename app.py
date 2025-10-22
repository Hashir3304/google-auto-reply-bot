import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from threading import Thread
import requests
from flask import Flask
from openai import OpenAI

app = Flask(__name__)

# === Environment ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
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
        print(f"📧 Sent email: {subject}")
    except Exception as e:
        print(f"❌ Email failed: {e}")

# === Health ===
@app.route("/")
def home():
    return "✅ Pawsy Prints Auto-Reply Bot — hourly, new & unreplied reviews only."

# === Google Reviews ===
def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        send_email("❌ Fetch Reviews Failed", f"{r.status_code}\n{r.text}")
        return []
    return r.json().get("reviews", [])

def generate_reply(name, stars, text):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a short, warm, polite professional thank-you reply as Pawsy Prints."
        "Keep it under 60 words and sound human and caring."
    )
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints’ friendly assistant writing public owner replies."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ GPT error: {e}")
        return ""

def post_reply(review_id, reply):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.put(url, headers=headers, json={"comment": reply}, timeout=30)
    return r.status_code == 200

# === Main Worker ===
def auto_reply_once():
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)
    reviews = get_reviews()
    if not reviews:
        print("ℹ️ No reviews.")
        return

    new_replies = []
    for rv in reviews:
        if rv.get("reviewReply"):  # skip already replied
            continue

        update_time = rv.get("updateTime") or rv.get("createTime")
        if update_time:
            try:
                t = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                if t < cutoff:
                    continue  # older than 24 h
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
            continue

        if post_reply(rid, reply):
            new_replies.append(f"{name} ({stars}★)\n{text}\n→ {reply}\n")

        time.sleep(2)

    if new_replies:
        body = f"🕓 {now.strftime('%Y-%m-%d %H:%M UTC')}\nReplied to {len(new_replies)} new review(s):\n\n" + "\n".join(new_replies)
        send_email(f"🐾 Auto-Replies Sent ({len(new_replies)})", body)
    else:
        print("ℹ️ No new reviews needing reply.")

# === Hourly Loop ===
def loop_hourly():
    while True:
        auto_reply_once()
        print("🕒 Sleeping 1 hour…")
        time.sleep(3600)

Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
