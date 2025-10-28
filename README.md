# 🐾 Pawsy Prints — Google Auto Reply Bot

This app automatically fetches new Google reviews for your business and posts warm, professional AI-generated replies using **OpenAI GPT** and the **Google Business Profile API**.

---

## 🚀 Features
- Fetches new Google Reviews hourly
- Generates natural replies using GPT
- Posts replies automatically via Google My Business API
- Sends email notifications of replies and errors
- Built with **Flask**, **Render**, and **OpenAI API**

---

## 🧠 Tech Stack
- Python 3.13
- Flask
- OpenAI GPT API
- Google My Business API
- Render (deployment)
- Gmail SMTP (notifications)

---

## ⚙️ Environment Variables

| Variable | Description |
|-----------|--------------|
| `OPENAI_KEY` | OpenAI API key |
| `GOOGLE_ACCESS_TOKEN` | Google OAuth Access Token |
| `GOOGLE_REFRESH_TOKEN` | Google OAuth Refresh Token |
| `GOOGLE_CLIENT_ID` | Your OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Your OAuth Client Secret |
| `ACCOUNT_ID` | Google Business Account ID |
| `LOCATION_ID` | Google Business Location ID |
| `PLACE_ID` | Google Maps Place ID |
| `GMAIL_USER` | Gmail address for notifications |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `NOTIFY_EMAIL_TO` | Optional: email to receive reports |

---

## 🧩 Endpoints
- `/` → Health check (`✅ Bot is live`)
- `/run-now` → Manual trigger for instant AI reply run

---

## 🕓 Automated Schedule
The bot runs hourly in a background thread on Render.

---

## 🔒 Security Notes
- Never commit `.env` or tokens to GitHub.
- Store all API keys securely in Render Environment Variables.

---

## 🧰 Deployment
Deployed automatically via **Render.com**  
➡️ [https://google-auto-reply-bot.onrender.com](https://google-auto-reply-bot.onrender.com)
