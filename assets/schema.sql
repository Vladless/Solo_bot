CREATE TABLE IF NOT EXISTS users
(
    tg_id         BIGINT PRIMARY KEY NOT NULL,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    language_code TEXT,
    is_bot        BOOLEAN                  DEFAULT FALSE,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS connections
(
    tg_id   BIGINT PRIMARY KEY NOT NULL,
    balance REAL               NOT NULL DEFAULT 0.0,
    trial   INTEGER            NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payments
(
    id             SERIAL PRIMARY KEY,
    tg_id          BIGINT NOT NULL,
    amount         REAL   NOT NULL,
    payment_system TEXT   NOT NULL,
    status         TEXT                     DEFAULT 'success',
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS keys
(
    tg_id        BIGINT  NOT NULL,
    client_id    TEXT    NOT NULL,
    email        TEXT    NOT NULL,
    created_at   BIGINT  NOT NULL,
    expiry_time  BIGINT  NOT NULL,
    key          TEXT    NOT NULL,
    server_id    TEXT    NOT NULL DEFAULT 'cluster1',
    notified     BOOLEAN NOT NULL DEFAULT FALSE,
    notified_24h BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tg_id, client_id)
);

CREATE TABLE IF NOT EXISTS referrals
(
    referred_tg_id BIGINT PRIMARY KEY NOT NULL,
    referrer_tg_id BIGINT             NOT NULL,
    reward_issued  BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS coupons
(
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    amount      INTEGER     NOT NULL,
    usage_limit INTEGER     NOT NULL DEFAULT 1,
    usage_count INTEGER     NOT NULL DEFAULT 0,
    is_used     BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS coupon_usages
(
    coupon_id INTEGER   NOT NULL REFERENCES coupons (id) ON DELETE CASCADE,
    user_id   BIGINT    NOT NULL,
    used_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (coupon_id, user_id)
);

CREATE TABLE IF NOT EXISTS notifications
(
    tg_id                  BIGINT    NOT NULL,
    last_notification_time TIMESTAMP NOT NULL DEFAULT NOW(),
    notification_type      TEXT      NOT NULL,
    PRIMARY KEY (tg_id, notification_type)
);

CREATE TABLE IF NOT EXISTS servers
(
    id               SERIAL PRIMARY KEY,
    cluster_name     TEXT NOT NULL,
    server_name      TEXT NOT NULL,
    api_url          TEXT NOT NULL,
    subscription_url TEXT NOT NULL,
    inbound_id       TEXT NOT NULL,
    UNIQUE (cluster_name, server_name) 
);


CREATE TABLE IF NOT EXISTS gifts
(
    gift_id        TEXT PRIMARY KEY NOT NULL,      
    sender_tg_id   BIGINT NOT NULL,            
    selected_months INTEGER NOT NULL,          
    expiry_time    TIMESTAMP WITH TIME ZONE NOT NULL,   
    gift_link      TEXT NOT NULL,                
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, 
    is_used        BOOLEAN NOT NULL DEFAULT FALSE,  
    recipient_tg_id BIGINT,                            
    CONSTRAINT fk_sender FOREIGN KEY (sender_tg_id) REFERENCES users (tg_id),  
    CONSTRAINT fk_recipient FOREIGN KEY (recipient_tg_id) REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS temporary_data (
    tg_id BIGINT PRIMARY KEY NOT NULL,
    state TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blocked_users (
    tg_id BIGINT PRIMARY KEY,
    blocked_at TIMESTAMP DEFAULT NOW()
);
