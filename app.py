from flask import Flask, jsonify
import requests
import os
from openai import OpenAI

app = Flask(__name__)

# Load secrets
OPENAI_KEY = os.getenv("OPENAI_KEY")
GOOGLE_ACCESS_TOKEN = os.getenv("GOOGLE_ACCESS_TOKEN")
PLACE_ID = os.getenv("PLACE_ID")

client = OpenAI(api_key=OPENAI_KEY)

@app.route('/')
def home():
    return "✅ Pawsy Prints Auto-Reply Bot connected to Google Reviews!"

@app.route('/reviews', methods=['GET'])
def get_reviews():
    """Fetch recent Google reviews for your Place ID"""
    url = f"https://mybusiness.googleapis.com/v4/accounts/{PLACE_ID}/locations/reviews"
    headers = {"Authorization": f"Bearer {GOOGLE_ACCESS_TOKEN}"}

    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        return jsonify({"error": "Failed to fetch reviews", "details": response.text}), 400

    data = response.json()
    reviews = data.get("reviews", [])
    replies = []

    for review in reviews:
        text = review.get("comment", "")
        stars = review.get("starRating", "5")
        name = review.get("reviewer", {}).get("displayName", "Customer")

        # AI-generated reply
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Pawsy Prints’ friendly assistant."},
                {"role": "user", "content": f"{name} left a {stars}-star review: '{text}'. Write a short, polite thank-you reply."}
            ]
        )

        reply_text = completion.choices[0].message.content.strip()
        replies.append({"reviewer": name, "review": text, "ai_reply": reply_text})

    return jsonify(replies)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
