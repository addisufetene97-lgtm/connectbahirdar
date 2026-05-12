import os
import re
import calendar
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Supabase (service key — bypasses RLS) ──────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
supabase: Client     = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ── Telegram ───────────────────────────────────────────────
BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET   = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TG_API           = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── App config ─────────────────────────────────────────────
INTERNAL_SECRET  = os.getenv("INTERNAL_SECRET", "changeme")
CBE_ACCOUNT      = os.getenv("CBE_ACCOUNT", "1000039338789")
CBE_ACCOUNT_NAME = os.getenv("CBE_ACCOUNT_NAME", "ConnectBahirDar Services")


# ══════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════
app = FastAPI(title="ConnectBahirDar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════
def require_internal(x_internal_key: str = Header(None)):
    if x_internal_key != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

async def safe_query(label: str, operation):
    try:
        result = operation.execute()
        return result.data
    except Exception as e:
        logger.error(f"Supabase [{label}]: {e}")
        raise HTTPException(status_code=500, detail="Database error")

async def log_audit(action: str, entity_type: str, entity_id: str, details: dict = {}):
    try:
        supabase.table("audit_log").insert({
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details,
        }).execute()
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")

async def send_telegram_message(chat_id: int, text: str, reply_markup: dict = None):
    if not BOT_TOKEN or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TG_API}/sendMessage", json=payload)
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


# ══════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════
class RejectBody(BaseModel):
    reason: str


# ══════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════
@app.get("/")
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ConnectBahirDar API"}


# ══════════════════════════════════════════════════════════════
# HOTELS
# ══════════════════════════════════════════════════════════════
@app.get("/api/hotels")
async def list_hotels():
    data = await safe_query("list_hotels",
        supabase.table("hotels")
            .select("id, name, location, description, contact_phone")
            .eq("is_active", True)
    )
    return {"hotels": data}


@app.get("/api/hotels/{hotel_id}/rooms")
async def hotel_rooms(hotel_id: str, status: str = "available"):
    data = await safe_query("hotel_rooms",
        supabase.table("rooms")
            .select("id, room_number, room_type, price_per_night, description, amenities, status, image_url")
            .eq("hotel_id", hotel_id)
            .eq("status", status)
    )
    return {"rooms": data}


# ══════════════════════════════════════════════════════════════
# ROOMS
# ══════════════════════════════════════════════════════════════
@app.get("/api/rooms")
async def list_rooms(status: Optional[str] = None, hotel_id: Optional[str] = None):
    q = supabase.table("rooms").select(
        "id, hotel_id, room_number, room_type, price_per_night, description, amenities, status, image_url, hotels(name, location)"
    )
    if status:
        q = q.eq("status", status)
    if hotel_id:
        q = q.eq("hotel_id", hotel_id)
    data = await safe_query("list_rooms", q)
    return {"rooms": data}


@app.get("/api/rooms/{room_id}")
async def get_room(room_id: str):
    data = await safe_query("get_room",
        supabase.table("rooms")
            .select("id, hotel_id, room_number, room_type, price_per_night, description, amenities, status, image_url, hotels(name, location)")
            .eq("id", room_id)
            .single()
    )
    if not data:
        raise HTTPException(404, "Room not found")
    return data


# ══════════════════════════════════════════════════════════════
# BOOKINGS — CREATE
# ══════════════════════════════════════════════════════════════
@app.post("/api/bookings")
async def create_booking(
    guest_name:           str      = Form(...),
    guest_phone:          str      = Form(...),
    guest_email:          str      = Form(None),
    room_id:              str      = Form(...),
    hotel_id:             Optional[str] = Form(None),
    check_in_date:        str      = Form(...),
    check_out_date:       str      = Form(...),
    payment_method:       str      = Form(...),
    transaction_reference: str     = Form(...),
    screenshot:           UploadFile = File(...),
):
    # 1. Validate dates
    try:
        ci = date.fromisoformat(check_in_date)
        co = date.fromisoformat(check_out_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    if co <= ci:
        raise HTTPException(400, "Check-out must be after check-in")
    if ci < date.today():
        raise HTTPException(400, "Check-in cannot be in the past")
    nights = (co - ci).days

    # 2. Check room is available
    room_data = await safe_query("check_room",
        supabase.table("rooms").select("id, status, price_per_night, hotel_id").eq("id", room_id).single()
    )
    if not room_data:
        raise HTTPException(404, "Room not found")
    # Always get hotel_id from DB, not from frontend
    hotel_id = room_data["hotel_id"]

    if room_data["status"] != "available":
        raise HTTPException(409, "Room is no longer available")

    # 3. Check occupancy conflict
    check_dates = [(ci + timedelta(days=i)).isoformat() for i in range(nights)]
    occupancy = await safe_query("check_occupancy",
        supabase.table("room_occupancy")
            .select("id")
            .eq("room_id", room_id)
            .in_("occupancy_date", check_dates)
    )
    if occupancy:
        raise HTTPException(409, "Room is already booked for selected dates")

    # 4. Upload screenshot to Supabase Storage
    booking_uuid = str(uuid.uuid4())
    ext = screenshot.filename.rsplit(".", 1)[-1] if "." in screenshot.filename else "jpg"
    storage_path = f"bookings/{booking_uuid}/{uuid.uuid4()}.{ext}"
    file_bytes = await screenshot.read()
    try:
        supabase.storage.from_("payment-screenshots").upload(
            storage_path, file_bytes, {"content-type": screenshot.content_type or "image/jpeg"}
        )
        screenshot_url = f"{SUPABASE_URL}/storage/v1/object/public/payment-screenshots/{storage_path}"
    except Exception as e:
        logger.error(f"Screenshot upload failed: {e}")
        raise HTTPException(500, "Failed to upload screenshot")

    # 5. Calculate total
    price_per_night = float(room_data["price_per_night"])
    subtotal = price_per_night * nights
    service_fee = round(subtotal * 0.05)
    total = subtotal + service_fee

    # 6. Insert booking
    booking = await safe_query("insert_booking",
        supabase.table("bookings").insert({
            "id": booking_uuid,
            "room_id": room_id,
            "hotel_id": hotel_id,
            "guest_name": guest_name,
            "guest_phone": guest_phone,
            "guest_email": guest_email,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "total_amount": total,
            "payment_method": payment_method,
            "transaction_reference": transaction_reference,
            "payment_screenshot_url": screenshot_url,
            "status": "pending",
            "pending_expires_at": (datetime.utcnow() + timedelta(minutes=20)).isoformat(),
        })
    )

    # 7. Mark room as pending
    await safe_query("room_pending",
        supabase.table("rooms").update({"status": "pending"}).eq("id", room_id)
    )

    # 8. Audit
    await log_audit("booking_created", "booking", booking_uuid, {"guest": guest_name, "room": room_id})

    ref = "CBD-" + booking_uuid.replace("-", "").upper()[:8]
    return {"success": True, "booking_id": booking_uuid, "reference": ref, "booking": booking[0] if booking else {}}


# ══════════════════════════════════════════════════════════════
# BOOKINGS — READ
# ══════════════════════════════════════════════════════════════
@app.get("/api/bookings")
async def get_bookings_by_phone(phone: str):
    data = await safe_query("bookings_by_phone",
        supabase.table("bookings")
            .select("id, guest_name, check_in_date, check_out_date, total_amount, status, created_at, rooms(room_number, room_type, hotels(name))")
            .eq("guest_phone", phone)
            .order("created_at", desc=True)
            .limit(10)
    )
    return {"bookings": data}


@app.get("/api/bookings/{booking_id}")
async def get_booking(booking_id: str):
    data = await safe_query("get_booking",
        supabase.table("bookings")
            .select("*, rooms(room_number, room_type, price_per_night, hotels(name, location))")
            .eq("id", booking_id)
            .single()
    )
    if not data:
        raise HTTPException(404, "Booking not found")
    return data


# ══════════════════════════════════════════════════════════════
# PENDING BOOKINGS (for dashboard)
# ══════════════════════════════════════════════════════════════
@app.get("/api/bookings/pending/all")
async def get_pending_bookings(hotel_id: Optional[str] = None, x_internal_key: str = Header(None)):
    if x_internal_key != INTERNAL_SECRET:
        raise HTTPException(403, "Forbidden")
    q = supabase.table("bookings") \
        .select("*, rooms(room_number, room_type, hotels(name))") \
        .eq("status", "pending") \
        .order("created_at", desc=True)
    if hotel_id:
        q = q.eq("hotel_id", hotel_id)
    data = await safe_query("pending_bookings", q)
    return {"bookings": data}


# ══════════════════════════════════════════════════════════════
# APPROVE
# ══════════════════════════════════════════════════════════════
@app.post("/api/bookings/{booking_id}/approve")
async def approve_booking(booking_id: str, x_internal_key: str = Header(None)):
    if x_internal_key != INTERNAL_SECRET:
        raise HTTPException(403, "Forbidden")

    # Load booking
    booking = await safe_query("load_booking",
        supabase.table("bookings").select("*").eq("id", booking_id).single()
    )
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] != "pending":
        raise HTTPException(409, f"Booking is already {booking['status']}")

    # Update booking
    await safe_query("confirm_booking",
        supabase.table("bookings").update({
            "status": "confirmed",
            "confirmed_at": datetime.utcnow().isoformat(),
        }).eq("id", booking_id)
    )

    # Mark room occupied
    await safe_query("room_occupied",
        supabase.table("rooms").update({"status": "occupied"}).eq("id", booking["room_id"])
    )

    # Insert occupancy rows
    ci = date.fromisoformat(booking["check_in_date"])
    co = date.fromisoformat(booking["check_out_date"])
    nights = (co - ci).days
    occupancy_rows = [
        {"room_id": booking["room_id"], "booking_id": booking_id, "occupancy_date": (ci + timedelta(days=i)).isoformat()}
        for i in range(nights)
    ]
    await safe_query("insert_occupancy",
        supabase.table("room_occupancy").insert(occupancy_rows)
    )

    # Audit
    await log_audit("booking_approved", "booking", booking_id)

    # Notify guest via Telegram
    ref = "CBD-" + booking_id.replace("-", "").upper()[:8]
    if booking.get("telegram_chat_id"):
        await send_telegram_message(
            booking["telegram_chat_id"],
            f"✅ <b>Booking Confirmed!</b>\n\n"
            f"Reference: <code>{ref}</code>\n"
            f"Check-in: <b>{booking['check_in_date']}</b>\n"
            f"Check-out: <b>{booking['check_out_date']}</b>\n\n"
            f"We look forward to welcoming you! 🏨"
        )

    return {"success": True, "reference": ref}


# ══════════════════════════════════════════════════════════════
# REJECT
# ══════════════════════════════════════════════════════════════
@app.post("/api/bookings/{booking_id}/reject")
async def reject_booking(booking_id: str, body: RejectBody, x_internal_key: str = Header(None)):
    if x_internal_key != INTERNAL_SECRET:
        raise HTTPException(403, "Forbidden")

    booking = await safe_query("load_booking",
        supabase.table("bookings").select("*").eq("id", booking_id).single()
    )
    if not booking:
        raise HTTPException(404, "Booking not found")
    if booking["status"] != "pending":
        raise HTTPException(409, f"Booking is already {booking['status']}")

    await safe_query("reject_booking",
        supabase.table("bookings").update({
            "status": "rejected",
            "rejection_reason": body.reason,
        }).eq("id", booking_id)
    )

    await safe_query("room_available",
        supabase.table("rooms").update({"status": "available"}).eq("id", booking["room_id"])
    )

    await log_audit("booking_rejected", "booking", booking_id, {"reason": body.reason})

    ref = "CBD-" + booking_id.replace("-", "").upper()[:8]
    if booking.get("telegram_chat_id"):
        await send_telegram_message(
            booking["telegram_chat_id"],
            f"❌ <b>Booking Not Confirmed</b>\n\n"
            f"Reference: <code>{ref}</code>\n"
            f"Reason: {body.reason}\n\n"
            f"Please try booking another room or contact us for help."
        )

    return {"success": True}


# ══════════════════════════════════════════════════════════════
# INTERNAL — EXPIRE PENDING BOOKINGS (called by n8n every 60s)
# ══════════════════════════════════════════════════════════════
@app.post("/api/internal/expire-bookings")
async def expire_bookings(x_internal_key: str = Header(None)):
    if x_internal_key != INTERNAL_SECRET:
        raise HTTPException(403, "Forbidden")
    now = datetime.utcnow().isoformat()
    expired = await safe_query("find_expired",
        supabase.table("bookings")
            .select("id, room_id")
            .eq("status", "pending")
            .lt("pending_expires_at", now)
    )
    count = 0
    for b in expired:
        await safe_query("expire",
            supabase.table("bookings").update({"status": "expired"}).eq("id", b["id"])
        )
        await safe_query("room_available_after_expire",
            supabase.table("rooms").update({"status": "available"}).eq("id", b["room_id"])
        )
        count += 1
    return {"expired": count}


# ══════════════════════════════════════════════════════════════
# TELEGRAM BOT HELPERS
# ══════════════════════════════════════════════════════════════
async def tg_post(method: str, payload: dict):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{TG_API}/{method}", json=payload)
        return r.json()

async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        p["reply_markup"] = reply_markup
    await tg_post("sendMessage", p)

async def send_inline_keyboard(chat_id: int, text: str, buttons: list):
    await send_message(chat_id, text, {
        "inline_keyboard": buttons
    })

async def get_session(chat_id: int) -> dict:
    try:
        result = supabase.table("bot_sessions").select("state, data") \
            .eq("telegram_chat_id", chat_id).execute()
        if result.data:
            return result.data[0]
    except Exception:
        pass
    return {"state": "idle", "data": {}}

async def set_session(chat_id: int, state: str, data: dict = {}):
    try:
        supabase.table("bot_sessions").upsert({
            "telegram_chat_id": chat_id,
            "state": state,
            "data": data,
            "updated_at": datetime.utcnow().isoformat(),
        }, on_conflict="telegram_chat_id").execute()
    except Exception as e:
        logger.warning(f"Session save failed: {e}")

async def get_user(chat_id: int) -> Optional[dict]:
    try:
        result = supabase.table("users").select("id, full_name, phone_number") \
            .eq("telegram_chat_id", chat_id).execute()
        return result.data[0] if result.data else None
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════
# TELEGRAM BOT HANDLERS
# ══════════════════════════════════════════════════════════════
async def handle_start(chat_id: int, first_name: str):
    await set_session(chat_id, "awaiting_phone")
    await send_message(
        chat_id,
        f"👋 Welcome to <b>ConnectBahirDar</b>, {first_name}!\n\n"
        f"🏨 Book hotel rooms in Bahir Dar easily.\n\n"
        f"Please share your phone number to continue:",
        {
            "keyboard": [[{"text": "📱 Share My Phone Number", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
    )

async def handle_contact(chat_id: int, contact: dict):
    phone = contact.get("phone_number", "")
    # Normalize: +251XXXXXXXXX → 09XXXXXXXX
    if phone.startswith("+251"):
        phone = "0" + phone[4:]
    elif phone.startswith("251"):
        phone = "0" + phone[3:]
    first_name = contact.get("first_name", "")
    last_name  = contact.get("last_name", "")
    full_name  = f"{first_name} {last_name}".strip()

    try:
        supabase.table("users").upsert({
            "telegram_chat_id": chat_id,
            "phone_number": phone,
            "full_name": full_name,
            "role": "customer",
        }, on_conflict="telegram_chat_id").execute()
    except Exception as e:
        logger.error(f"Upsert user failed: {e}")

    await set_session(chat_id, "browsing")
    await send_message(
        chat_id,
        f"✅ Welcome, <b>{first_name}</b>! You're all set.\n\n"
        f"Use the commands below:\n"
        f"🏨 /hotels — Browse & book rooms\n"
        f"📋 /mybookings — View your bookings\n"
        f"❓ /help — Show help",
        {"remove_keyboard": True}
    )

async def handle_hotels(chat_id: int):
    user = await get_user(chat_id)
    if not user or not user.get("phone_number"):
        await send_message(chat_id,
            "Please share your phone number first.\nSend /start to begin.")
        return
    try:
        result = supabase.table("hotels").select("id, name, location").eq("is_active", True).execute()
        hotels = result.data or []
    except Exception:
        hotels = []

    if not hotels:
        await send_message(chat_id, "No hotels available at the moment. Try again later.")
        return

    buttons = [[{"text": f"🏨 {h['name']}", "callback_data": f"hotel:{h['id']}"}] for h in hotels]
    await send_inline_keyboard(chat_id, "Select a hotel to browse rooms:", buttons)

async def handle_mybookings(chat_id: int):
    user = await get_user(chat_id)
    if not user or not user.get("phone_number"):
        await send_message(chat_id, "Please /start first to register.")
        return
    try:
        result = supabase.table("bookings") \
            .select("id, check_in_date, check_out_date, total_amount, status, rooms(room_number, room_type)") \
            .eq("guest_phone", user["phone_number"]) \
            .order("created_at", desc=True).limit(5).execute()
        bookings = result.data or []
    except Exception:
        bookings = []

    if not bookings:
        await send_message(chat_id, "You have no bookings yet. Use /hotels to book a room.")
        return

    status_emoji = {"pending": "⏳", "confirmed": "✅", "rejected": "❌", "expired": "🕐"}
    lines = ["<b>Your Recent Bookings:</b>\n"]
    for b in bookings:
        ref = "CBD-" + b["id"].replace("-", "").upper()[:8]
        emoji = status_emoji.get(b["status"], "•")
        room = b.get("rooms", {})
        lines.append(
            f"{emoji} <b>{ref}</b>\n"
            f"   Room: {room.get('room_type','')} {room.get('room_number','')}\n"
            f"   {b['check_in_date']} → {b['check_out_date']}\n"
            f"   ETB {int(float(b['total_amount'])):,} — {b['status'].upper()}\n"
        )
    await send_message(chat_id, "\n".join(lines))

async def handle_callback(chat_id: int, data: str):
    session = await get_session(chat_id)

    if data.startswith("hotel:"):
        hotel_id = data.split(":")[1]
        try:
            result = supabase.table("rooms") \
                .select("id, room_number, room_type, price_per_night, status") \
                .eq("hotel_id", hotel_id).eq("status", "available").execute()
            rooms = result.data or []
        except Exception:
            rooms = []

        if not rooms:
            await send_message(chat_id, "No available rooms at this hotel right now. Try later.")
            return

        buttons = [[{
            "text": f"🛏 {r['room_type']} #{r['room_number']} — ETB {int(float(r['price_per_night'])):,}/night",
            "callback_data": f"room:{r['id']}"
        }] for r in rooms]
        await send_inline_keyboard(chat_id, "Available rooms — tap to see details:", buttons)

    elif data.startswith("room:"):
        room_id = data.split(":")[1]
        try:
            result = supabase.table("rooms") \
                .select("id, room_number, room_type, price_per_night, description, amenities, hotels(name)") \
                .eq("id", room_id).single().execute()
            room = result.data
        except Exception:
            room = None

        if not room:
            await send_message(chat_id, "Room not found.")
            return

        amenities = ", ".join(room.get("amenities") or [])
        hotel_name = (room.get("hotels") or {}).get("name", "")
        text = (
            f"🏨 <b>{hotel_name}</b>\n"
            f"🛏 <b>{room['room_type']} Room #{room['room_number']}</b>\n\n"
            f"💰 <b>ETB {int(float(room['price_per_night'])):,}/night</b>\n"
            f"✨ {room.get('description','')}\n"
            f"🎁 Amenities: {amenities}\n\n"
            f"Tap below to start booking:"
        )
        await send_inline_keyboard(chat_id, text, [[
            {"text": "📅 Book This Room", "callback_data": f"book:{room_id}"}
        ]])

    elif data.startswith("cal_nav:"):
        # Navigate calendar month
        _, y, m, cal_type = data.split(":")
        y, m = int(y), int(m)
        session = await get_session(chat_id)
        s_data = session.get("data", {})
        min_date = None
        if cal_type == "checkout" and s_data.get("check_in"):
            ci = date.fromisoformat(s_data["check_in"])
            min_date = ci + timedelta(days=1)
        label = "check-in" if cal_type == "checkin" else "check-out"
        await send_calendar(chat_id,
            f"📅 Select your <b>{label} date</b>:",
            y, m, cal_type, min_date
        )

    elif data.startswith("cal_day:"):
        # Day selected from calendar
        _, y, m, d, cal_type = data.split(":")
        selected = date(int(y), int(m), int(d))
        session = await get_session(chat_id)
        s_data = session.get("data", {})

        if cal_type == "checkin":
            s_data["check_in"] = selected.isoformat()
            await set_session(chat_id, "booking_checkout", s_data)
            min_checkout = selected + timedelta(days=1)
            await send_calendar(chat_id,
                f"✅ Check-in: <b>{selected.strftime('%b %d, %Y')}</b>\n\nNow select your <b>check-out date</b>:",
                selected.year, selected.month, "checkout", min_checkout
            )

        elif cal_type == "checkout":
            check_in  = date.fromisoformat(s_data["check_in"])
            check_out = selected
            nights    = (check_out - check_in).days
            room_id   = s_data.get("room_id")

            # Get room price
            try:
                r = supabase.table("rooms") \
                    .select("price_per_night, room_type, room_number, hotels(name)") \
                    .eq("id", room_id).single().execute()
                room = r.data
            except Exception:
                room = None

            price       = float(room["price_per_night"]) if room else 0
            subtotal    = price * nights
            service_fee = round(subtotal * 0.05)
            total       = subtotal + service_fee

            s_data["check_out"] = check_out.isoformat()
            s_data["total"]     = total
            await set_session(chat_id, "booking_payment", s_data)

            hotel_name = (room.get("hotels") or {}).get("name", "") if room else ""
            room_label = f"{room['room_type']} #{room['room_number']}" if room else "Room"

            await send_message(chat_id,
                f"✅ <b>Booking Summary</b>\n\n"
                f"🏨 {hotel_name} — {room_label}\n"
                f"📅 Check-in:  <b>{check_in.strftime('%b %d, %Y')}</b>\n"
                f"📅 Check-out: <b>{check_out.strftime('%b %d, %Y')}</b>\n"
                f"🌙 {nights} night{'s' if nights > 1 else ''}\n\n"
                f"💰 ETB {price:,.0f} × {nights} = ETB {subtotal:,.0f}\n"
                f"Service fee (5%) = ETB {service_fee:,.0f}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<b>Total: ETB {total:,.0f}</b>\n\n"
                f"🏦 <b>Pay via CBE:</b>\n"
                f"Account: <code>{CBE_ACCOUNT}</code>\n"
                f"Name: {CBE_ACCOUNT_NAME}\n"
                f"Amount: <b>ETB {total:,.0f}</b>\n\n"
                f"After paying, <b>upload your payment screenshot</b> 👇"
            )

    elif data.startswith("book:"):
        room_id = data.split(":")[1]
        user = await get_user(chat_id)
        if not user:
            await send_message(chat_id, "Please /start first.")
            return
        await set_session(chat_id, "booking_checkin", {"room_id": room_id})
        today = date.today()
        await send_calendar(chat_id,
            "📅 Select your <b>check-in date</b>:",
            today.year, today.month, "checkin"
        )

async def handle_text(chat_id: int, text: str):
    session = await get_session(chat_id)
    state = session.get("state", "idle")
    data  = session.get("data", {})

    if state == "awaiting_phone":
        # User typed phone number instead of using the button
        phone = text.strip()
        if not phone.startswith("+"):
            if phone.startswith("251"):
                phone = "0" + phone[3:]
            elif not phone.startswith("0"):
                phone = "0" + phone
        if re.match(r'^0[79]\d{8}$', phone) or re.match(r'^\+251[79]\d{8}$', phone):
            user = await get_user(chat_id)
            full_name = user.get("full_name", text) if user else text
            try:
                supabase.table("users").upsert({
                    "telegram_chat_id": chat_id,
                    "phone_number": phone,
                    "full_name": full_name,
                    "role": "customer",
                }, on_conflict="telegram_chat_id").execute()
            except Exception as e:
                logger.error(f"Upsert user failed: {e}")
            await set_session(chat_id, "browsing", {})
            await send_message(chat_id,
                f"✅ Phone number saved!\n\n"
                f"Use the commands below:\n"
                f"🏨 /hotels — Browse & book rooms\n"
                f"📋 /mybookings — View your bookings\n"
                f"❓ /help — Show help",
                {"remove_keyboard": True}
            )
        else:
            await send_message(chat_id,
                "❌ That doesn't look like a valid phone number.\n"
                "Please enter like: <code>0911234567</code>\n"
                "Or press the button below to share automatically.",
                {
                    "keyboard": [[{"text": "📱 Share My Phone Number", "request_contact": True}]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                }
            )
        return

    if state in ("booking_checkin", "booking_checkout"):
        # Redirect to calendar if user types instead of tapping
        today = date.today()
        min_date = today
        if state == "booking_checkout" and data.get("check_in"):
            min_date = date.fromisoformat(data["check_in"]) + timedelta(days=1)
        cal_type = "checkin" if state == "booking_checkin" else "checkout"
        label = "check-in" if state == "booking_checkin" else "check-out"
        await send_calendar(chat_id,
            f"📅 Please tap a date to select your <b>{label}</b>:",
            today.year, today.month, cal_type, min_date
        )

    elif state == "booking_ref":
        txn_ref = text.strip()
        if len(txn_ref) < 4:
            await send_message(chat_id, "❌ Please enter a valid transaction reference.")
            return
        data["txn_ref"] = txn_ref
        # Create the booking
        await create_bot_booking(chat_id, data)

    else:
        await send_message(chat_id,
            "Use /hotels to browse rooms, /mybookings to see your bookings, or /help for commands.")

async def handle_photo(chat_id: int, file_id: str):
    session = await get_session(chat_id)
    state   = session.get("state", "idle")
    data    = session.get("data", {})
    if state != "booking_payment":
        await send_message(chat_id, "Send /hotels to start a new booking.")
        return
    data["screenshot_file_id"] = file_id
    await set_session(chat_id, "booking_ref", data)
    await send_message(chat_id,
        "📸 Screenshot received!\n\n"
        "Now enter your <b>CBE transaction reference number</b>\n"
        "(found in your bank SMS or app receipt):")

async def create_bot_booking(chat_id: int, data: dict):
    user = await get_user(chat_id)
    if not user:
        await send_message(chat_id, "Session expired. Please /start again.")
        return

    room_id   = data.get("room_id")
    check_in  = data.get("check_in")
    check_out = data.get("check_out")
    file_id   = data.get("screenshot_file_id", "")
    txn_ref   = data.get("txn_ref", "")
    total     = data.get("total", 0)

    # Get hotel_id for the room
    try:
        r = supabase.table("rooms").select("hotel_id").eq("id", room_id).single().execute()
        hotel_id = r.data["hotel_id"]
    except Exception:
        await send_message(chat_id, "❌ Could not find room. Please start over with /hotels.")
        return

    booking_uuid = str(uuid.uuid4())
    ref = "CBD-" + booking_uuid.replace("-","").upper()[:8]

    # Get direct Telegram file URL for the screenshot
    screenshot_url = f"tg-file:{file_id}"  # fallback
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{TG_API}/getFile", params={"file_id": file_id})
            result = r.json()
            if result.get("ok"):
                file_path = result["result"]["file_path"]
                screenshot_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                logger.info(f"Bot screenshot URL: {screenshot_url}")
    except Exception as e:
        logger.error(f"Bot getFile FAILED: {type(e).__name__}: {e}")

    try:
        supabase.table("bookings").insert({
            "id": booking_uuid,
            "room_id": room_id,
            "hotel_id": hotel_id,
            "guest_name": user.get("full_name", "Telegram User"),
            "guest_phone": user.get("phone_number", ""),
            "check_in_date": check_in,
            "check_out_date": check_out,
            "total_amount": total,
            "payment_method": "bank_transfer",
            "transaction_reference": txn_ref,
            "payment_screenshot_url": screenshot_url,
            "status": "pending",
            "telegram_chat_id": chat_id,
            "pending_expires_at": (datetime.utcnow() + timedelta(minutes=20)).isoformat(),
        }).execute()

        supabase.table("rooms").update({"status": "pending"}).eq("id", room_id).execute()
        await log_audit("bot_booking_created", "booking", booking_uuid, {"chat_id": chat_id})
    except Exception as e:
        logger.error(f"Bot booking insert failed: {e}")
        await send_message(chat_id, "❌ Something went wrong. Please try again.")
        return

    await set_session(chat_id, "browsing", {})
    await send_message(
        chat_id,
        f"🎉 <b>Booking Submitted!</b>\n\n"
        f"Reference: <code>{ref}</code>\n"
        f"Check-in: <b>{check_in}</b>\n"
        f"Check-out: <b>{check_out}</b>\n"
        f"Total: <b>ETB {int(total):,}</b>\n\n"
        f"⏳ Your booking is under review.\n"
        f"We'll notify you here once confirmed (within a few hours).\n\n"
        f"Use /mybookings to track your status."
    )



# ══════════════════════════════════════════════════════════════
# CALENDAR KEYBOARD BUILDER
# ══════════════════════════════════════════════════════════════
def build_calendar(year: int, month: int, cal_type: str, min_date: date = None) -> list:
    """Build an inline keyboard calendar for the given month."""
    today = date.today()
    if min_date is None:
        min_date = today

    month_name = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][month]
    cal = calendar.monthcalendar(year, month)

    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    buttons = []

    # Row 1: navigation
    buttons.append([
        {"text": "◀️", "callback_data": f"cal_nav:{prev_y}:{prev_m}:{cal_type}"},
        {"text": f"📅 {month_name} {year}", "callback_data": "ignore"},
        {"text": "▶️", "callback_data": f"cal_nav:{next_y}:{next_m}:{cal_type}"},
    ])

    # Row 2: day headers
    buttons.append([
        {"text": d, "callback_data": "ignore"}
        for d in ["Mo","Tu","We","Th","Fr","Sa","Su"]
    ])

    # Rows 3+: days
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append({"text": " ", "callback_data": "ignore"})
            else:
                d = date(year, month, day)
                if d < min_date:
                    row.append({"text": "·", "callback_data": "ignore"})
                else:
                    row.append({"text": str(day), "callback_data": f"cal_day:{year}:{month}:{day}:{cal_type}"})
        buttons.append(row)

    return buttons

async def send_calendar(chat_id: int, text: str, year: int, month: int, cal_type: str, min_date: date = None):
    buttons = build_calendar(year, month, cal_type, min_date)
    await send_message(chat_id, text, {"inline_keyboard": buttons})

# ══════════════════════════════════════════════════════════════
# TELEGRAM WEBHOOK ENDPOINT
# ══════════════════════════════════════════════════════════════
@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None)
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid webhook secret")

    update = await request.json()
    logger.info(f"TG update: {update.get('update_id')}")

    try:
        # Callback query (inline button tap)
        if "callback_query" in update:
            cq      = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            cb_data = cq.get("data", "")
            # Answer callback to remove loading spinner
            await tg_post("answerCallbackQuery", {"callback_query_id": cq["id"]})
            await handle_callback(chat_id, cb_data)
            return {"ok": True}

        # Regular message
        if "message" not in update:
            return {"ok": True}

        msg     = update["message"]
        chat_id = msg["chat"]["id"]
        fname   = msg.get("from", {}).get("first_name", "there")

        # Contact shared
        if "contact" in msg:
            await handle_contact(chat_id, msg["contact"])
            return {"ok": True}

        # Photo
        if "photo" in msg:
            file_id = msg["photo"][-1]["file_id"]
            await handle_photo(chat_id, file_id)
            return {"ok": True}

        # Text
        text = msg.get("text", "")
        if not text:
            return {"ok": True}

        if text.startswith("/start"):
            await handle_start(chat_id, fname)
        elif text.startswith("/hotels"):
            await handle_hotels(chat_id)
        elif text.startswith("/mybookings"):
            await handle_mybookings(chat_id)
        elif text.startswith("/cancel"):
            await set_session(chat_id, "browsing", {})
            await send_message(chat_id, "Booking cancelled. Use /hotels to start a new one.")
        elif text.startswith("/help"):
            await send_message(chat_id,
                "<b>ConnectBahirDar Bot Commands</b>\n\n"
                "/hotels — Browse hotels and rooms\n"
                "/mybookings — See your booking history\n"
                "/cancel — Cancel current booking flow\n"
                "/help — Show this message\n\n"
                "Questions? Contact us at +251911000001")
        else:
            await handle_text(chat_id, text)

    except Exception as e:
        logger.error(f"Webhook handler error: {e}", exc_info=True)

    return {"ok": True}


# ══════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
