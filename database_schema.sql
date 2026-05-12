-- Database Schema for Connect Bahir Dar Rental Platform

-- 1. Users Table (Authentication and Profile)
CREATE TABLE users (
    user_id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Listings Table (Core property data)
CREATE TABLE listings (
    listing_id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    address VARCHAR(500) NOT NULL,
    latitude DECIMAL(10, 8),
    longitude DECIMAL(10, 8),
    rent_amount DECIMAL(10, 2) NOT NULL,
    property_type VARCHAR(50) NOT NULL, -- e.g., Apartment, House, Room
    bedrooms INTEGER NOT NULL,
    bathrooms INTEGER NOT NULL,
    is_available BOOLEAN DEFAULT TRUE,
    listing_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Images Table (Property photos)
CREATE TABLE images (
    image_id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    url VARCHAR(500) NOT NULL,
    caption VARCHAR(255),
    sort_order INTEGER DEFAULT 1
);

-- 4. Bookings Table (Tracking reservations)
CREATE TABLE bookings (
    booking_id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING', -- PENDING, CONFIRMED, CANCELLED
    booking_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Reviews Table (User feedback)
CREATE TABLE reviews (
    review_id SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    review_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance improvement
CREATE INDEX idx_listings_user_id ON listings (user_id);
CREATE INDEX idx_bookings_listing_id ON bookings (listing_id);
CREATE INDEX idx_reviews_listing_id ON reviews (listing_id);