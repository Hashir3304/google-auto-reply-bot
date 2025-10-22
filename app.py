import os
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from threading import Thread
import requests
from flask import Flask
from openai import OpenAI

# === Flask app (for Render) ===
app = Flask(__name__)

# === Environment variables ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_USER)

# === OpenAI client ===
client = OpenAI(api_key=OPENAI_KEY)

# === Email utility ===
def send_email(subject: str, body: str):
    if not (GMAIL_USER and GMAIL_APP_PASSWORD and NOTIFY_EMAIL_TO):
        print("⚠️ Gmail not configured — skipping email.")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = GMAIL_USER
        msg["To"] = NOTIFY_EMAIL_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"📧 Email sent → {NOTIFY_EMAIL_TO}: {subject}")
    except Exception as e:
        print(f"❌ Email send failed: {e}")

# === Health check ===
@app.route("/")
def home():
    return "✅ Pawsy Prints Auto-Reply Bot is live — hourly replies & Gmail summaries active!"

# === Fetch Google reviews ===
def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        send_email("❌ Auto-Reply: Fetch failed", f"Status: {r.status_code}\n{r.text}")
        print(f"❌ Fetch failed: {r.text}")
        return []
    return r.json().get("reviews", [])

# === Generate AI reply ===
def generate_reply(text, stars, name):
    prompt = (
        f"{name} left a {stars}-star Google review:\n"
        f"\"{text}\"\n\n"
        "Write a short, kind, professional thank-you reply for Pawsy Prints. "
        "Keep it under 60 words, warm but concise."
    )
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints’ friendly, professional AI assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        send_email("❌ Auto-Reply: GPT error", str(e))
        print(f"❌ GPT error: {e}")
        return ""

# === Post reply to Google ===
def post_reply(review_id, reply_text):
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews/{review_id}/reply"
    headers = {
        "Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.put(url, headers=headers, json={"comment": reply_text}, timeout=30)
    if r.status_code == 200:
        print(f"✅ Posted reply for {review_id}")
        return True
    else:
        send_email("❌ Auto-Reply: Post failed", f"Review ID: {review_id}\n{r.text}")
        print(f"❌ Post failed for {review_id}: {r.text}")
        return False

# === Main job ===
def auto_reply_once():
    start = datetime.utcnow()
    print(f"⏰ Run started {start.isoformat()}Z")

    reviews = get_reviews()
    if not reviews:
        print("ℹ️ No reviews found.")
        return

    successes, fails = [], []
    for rv in reviews:
        if rv.get("reviewReply"):
            continue
        review_id = rv.get("reviewId")
        name = rv.get("reviewer", {}).get("displayName", "Customer")
        stars = rv.get("starRating", "5")
        text = rv.get("comment", "")
        if not text:
            continue

        reply = generate_reply(text, stars, name)
        if not reply:
            fails.append((review_id, "GPT failed"))
            continue

        if post_reply(review_id, reply):
            successes.append((review_id, name, stars, text, reply))
        else:
            fails.append((review_id, "Post failed"))
        time.sleep(2)

    # === Build and send summary ===
    end = datetime.utcnow()
    duration = (end - start).total_seconds()
    subject = f"🐾 Auto-Reply Summary | {len(successes)} OK, {len(fails)} failed | {end.strftime('%Y-%m-%d %H:%M UTC')}"
    lines = [
        f"🕓 Run time: {start.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"⌛ Duration: {round(duration)}s",
        f"📊 Total reviews checked: {len(reviews)}",
        f"✅ Successful replies: {len(successes)}",
        f"❌ Failed: {len(fails)}",
        ""
    ]
    for s in successes:
        rid, nm, st, tx, rp = s
        lines.append(f"— {nm} ({st}★)\nReview: {tx}\nReply: {rp}\n")
    for f in fails:
        rid, reason = f
        lines.append(f"⚠️ {rid}: {reason}")

    send_email(subject, "\n".join(lines)[:9000])
    print("✅ Summary email sent.\n")

# === Hourly loop ===
def loop_hourly():
    while True:
        auto_reply_once()
        print("🛌 Sleeping 1 hour...\n")
        time.sleep(3600)

Thread(target=loop_hourly, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
