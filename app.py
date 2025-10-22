import os
import time
import requests
from flask import Flask, request
from openai import OpenAI

# Initialize Flask
app = Flask(__name__)

# Environment variables
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_KEY)

@app.route("/")
def home():
    return "✅ Pawsy Prints Auto-Reply Bot is running!"

@app.route("/test")
def test():
    text = request.args.get("text", "This place is awesome!")
    stars = request.args.get("stars", "5")
    prompt = f"Write a short, kind professional reply to a {stars}-star Google review: '{text}'"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    reply = response.choices[0].message.content.strip()
    return f"<b>Customer said:</b> {text}<br><b>AI Reply:</b> {reply}"

def get_reviews():
    url = f"https://mybusiness.googleapis.com/v4/accounts/accountId/locations/{PLACE_ID}/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json().get("reviews", [])
    return []

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
