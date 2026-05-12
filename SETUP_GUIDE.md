# ConnectBahirDar — Setup Guide

## Step 1: Supabase
1. Go to https://supabase.com → New Project → name it `connectbahirdar`
2. Dashboard → SQL Editor → New Query → paste `database/schema.sql` → Run
3. Dashboard → Database → Replication → enable Realtime for: `bookings`, `rooms`
4. Dashboard → Storage → New Bucket → name: `payment-screenshots` → Private
5. Dashboard → Settings → API → copy:
   - Project URL → SUPABASE_URL
   - anon public key → SUPABASE_ANON_KEY
   - service_role secret → SUPABASE_SERVICE_KEY

## Step 2: Create Receptionist Account
In Supabase → Authentication → Users → Invite User
Enter the receptionist's email. They'll get a password setup email.

## Step 3: Backend
```bash
cd backend
cp ../.env.example .env
# Fill in all values in .env
pip install -r requirements.txt
python main.py
# Backend runs on https://semicommunicative-cyphellate-brittaney.ngrok-free.dev
```

## Step 4: Register Telegram Webhook
After deploying your backend to Railway/Fly.io, run:
```bash
curl -X POST "https://api.telegram.org/botYOUR_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://your-api.railway.app/webhook/telegram","secret_token":"YOUR_WEBHOOK_SECRET"}'
```

## Step 5: Dashboard
Open `frontend/dashboard.html` in a browser.
Edit the CONFIG block at the top of the file:
```js
const CONFIG = {
  SUPABASE_URL:     'https://xxx.supabase.co',
  SUPABASE_ANON_KEY:'eyJ...',
  API_URL:          'https://semicommunicative-cyphellate-brittaney.ngrok-free.dev',
  INTERNAL_SECRET:  'your-internal-secret',
};
```

## Step 6: Frontend (Customer Site)
Open `frontend/index.html` in a browser.
Replace the CONFIG block inside the script with your real Supabase values.

## Step 7: n8n Booking Expiry (Optional)
- Add HTTP Request node: POST `your-api/api/internal/expire-bookings`
- Header: `X-Internal-Key: your-internal-secret`
- Schedule: every 60 seconds
