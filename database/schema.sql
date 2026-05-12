-- ============================================================
-- ConnectBahirDar — Full Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → New Query
-- Safe to re-run: drops existing tables first
-- ============================================================

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS audit_log        CASCADE;
DROP TABLE IF EXISTS bot_sessions     CASCADE;
DROP TABLE IF EXISTS room_occupancy   CASCADE;
DROP TABLE IF EXISTS bookings         CASCADE;
DROP TABLE IF EXISTS rooms            CASCADE;
DROP TABLE IF EXISTS users            CASCADE;
DROP TABLE IF EXISTS hotels           CASCADE;

-- Drop enums if they exist
DROP TYPE IF EXISTS user_role      CASCADE;
DROP TYPE IF EXISTS booking_status CASCADE;
DROP TYPE IF EXISTS room_status    CASCADE;
DROP TYPE IF EXISTS payment_method CASCADE;

-- Drop function if exists
DROP FUNCTION IF EXISTS update_updated_at CASCADE;

-- ============================================================
-- ENUMS
-- ============================================================
CREATE TYPE user_role       AS ENUM ('customer', 'receptionist', 'super_admin');
CREATE TYPE booking_status  AS ENUM ('pending', 'confirmed', 'rejected', 'expired');
CREATE TYPE room_status     AS ENUM ('available', 'pending', 'occupied', 'maintenance');
CREATE TYPE payment_method  AS ENUM ('telebirr', 'bank_transfer');

-- ============================================================
-- HOTELS
-- ============================================================
CREATE TABLE hotels (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    location            TEXT NOT NULL,
    description         TEXT,
    contact_phone       TEXT,
    bank_account_number TEXT,
    telebirr_number     TEXT,
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_chat_id  BIGINT UNIQUE,
    phone_number      TEXT UNIQUE,
    full_name         TEXT,
    role              user_role DEFAULT 'customer',
    hotel_id          UUID REFERENCES hotels(id),
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ROOMS
-- ============================================================
CREATE TABLE rooms (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_id        UUID NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
    room_number     TEXT NOT NULL,
    room_type       TEXT NOT NULL,
    price_per_night NUMERIC(10,2) NOT NULL,
    description     TEXT,
    amenities       TEXT[] DEFAULT ARRAY[]::TEXT[],
    status          room_status DEFAULT 'available',
    image_url       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(hotel_id, room_number)
);

-- ============================================================
-- BOOKINGS
-- ============================================================
CREATE TABLE bookings (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID REFERENCES users(id),
    room_id                  UUID NOT NULL REFERENCES rooms(id),
    hotel_id                 UUID NOT NULL REFERENCES hotels(id),
    guest_name               TEXT NOT NULL,
    guest_phone              TEXT NOT NULL,
    guest_email              TEXT,
    check_in_date            DATE NOT NULL,
    check_out_date           DATE NOT NULL,
    nights                   INTEGER GENERATED ALWAYS AS (check_out_date - check_in_date) STORED,
    total_amount             NUMERIC(10,2) NOT NULL,
    payment_method           payment_method,
    transaction_reference    TEXT,
    payment_screenshot_url   TEXT,
    status                   booking_status DEFAULT 'pending',
    rejection_reason         TEXT,
    telegram_chat_id         BIGINT,
    pending_expires_at       TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '20 minutes'),
    confirmed_at             TIMESTAMPTZ,
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    updated_at               TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ROOM OCCUPANCY
-- ============================================================
CREATE TABLE room_occupancy (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id        UUID NOT NULL REFERENCES rooms(id),
    booking_id     UUID NOT NULL REFERENCES bookings(id),
    occupancy_date DATE NOT NULL,
    UNIQUE(room_id, occupancy_date)
);

-- ============================================================
-- BOT SESSIONS
-- ============================================================
CREATE TABLE bot_sessions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    state            TEXT DEFAULT 'idle',
    data             JSONB DEFAULT '{}',
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- AUDIT LOG
-- ============================================================
CREATE TABLE audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action       TEXT NOT NULL,
    entity_type  TEXT,
    entity_id    UUID,
    performed_by UUID REFERENCES users(id),
    details      JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX idx_rooms_hotel_id       ON rooms(hotel_id);
CREATE INDEX idx_rooms_status         ON rooms(status);
CREATE INDEX idx_bookings_room_id     ON bookings(room_id);
CREATE INDEX idx_bookings_hotel_id    ON bookings(hotel_id);
CREATE INDEX idx_bookings_status      ON bookings(status);
CREATE INDEX idx_bookings_phone       ON bookings(guest_phone);
CREATE INDEX idx_bookings_expires     ON bookings(pending_expires_at) WHERE status = 'pending';
CREATE INDEX idx_occupancy_room_date  ON room_occupancy(room_id, occupancy_date);
CREATE INDEX idx_bot_sessions_chat    ON bot_sessions(telegram_chat_id);

-- ============================================================
-- AUTO-UPDATE updated_at TRIGGER
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_bookings_updated_at
    BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
ALTER TABLE hotels         ENABLE ROW LEVEL SECURITY;
ALTER TABLE rooms          ENABLE ROW LEVEL SECURITY;
ALTER TABLE users          ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_occupancy ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_sessions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log      ENABLE ROW LEVEL SECURITY;

-- Public read for hotels and rooms (anyone can browse)
CREATE POLICY "Public read hotels" ON hotels FOR SELECT USING (is_active = TRUE);
CREATE POLICY "Public read rooms"  ON rooms  FOR SELECT USING (TRUE);

-- Service role (used by FastAPI backend) bypasses all RLS automatically

-- ============================================================
-- SEED DATA — 1 hotel, 6 rooms
-- ============================================================
INSERT INTO hotels (id, name, location, description, contact_phone, bank_account_number)
VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'Tana Resort Hotel',
    'Lake Tana Shore, Bahir Dar',
    'Luxury lakefront hotel with stunning views of Lake Tana',
    '+251911000001',
    '1000039338789'
);

INSERT INTO rooms (hotel_id, room_number, room_type, price_per_night, description, amenities, image_url) VALUES
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '101', 'Single',  320,  'Comfortable single room with garden view',  ARRAY['WiFi','Fan','TV'],                        'https://images.unsplash.com/photo-1631049422822-ada1cf5e5c44?w=600'),
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '201', 'Double',  520,  'Spacious double room with city view',       ARRAY['WiFi','AC','TV'],                         'https://images.unsplash.com/photo-1566665797739-1674de7a421a?w=600'),
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '202', 'Double',  520,  'Cozy twin room with garden access',         ARRAY['WiFi','AC','TV'],                         'https://images.unsplash.com/photo-1590490360182-c33d57733427?w=600'),
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '301', 'Deluxe',  680,  'Deluxe king room with mini bar',            ARRAY['WiFi','AC','TV','Mini Bar'],              'https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=600'),
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '401', 'Suite',   850,  'Lake view suite with private balcony',      ARRAY['WiFi','AC','Lake View','Breakfast'],      'https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600'),
('a1b2c3d4-e5f6-7890-abcd-ef1234567890', '501', 'Suite',   1500, 'Presidential suite with panoramic view',    ARRAY['WiFi','AC','Panoramic View','Butler','Jacuzzi'], 'https://images.unsplash.com/photo-1611892440504-42a792e24d32?w=600');
