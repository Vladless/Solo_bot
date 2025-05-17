CREATE TABLE IF NOT EXISTS users (
    tg_id         BIGINT PRIMARY KEY NOT NULL,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    language_code TEXT,
    is_bot        BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    balance       REAL NOT NULL DEFAULT 0.0,
    trial         INTEGER NOT NULL DEFAULT 0,
    source_code   TEXT REFERENCES tracking_sources (code)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' AND column_name = 'balance'
    ) THEN
        ALTER TABLE users ADD COLUMN balance REAL NOT NULL DEFAULT 0.0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' AND column_name = 'trial'
    ) THEN
        ALTER TABLE users ADD COLUMN trial INTEGER NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'users' AND column_name = 'source_code'
    ) THEN
        ALTER TABLE users ADD COLUMN source_code TEXT REFERENCES tracking_sources (code);
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS payments (
    id             SERIAL PRIMARY KEY,
    tg_id          BIGINT NOT NULL,
    amount         REAL   NOT NULL,
    payment_system TEXT   NOT NULL,
    status         TEXT   DEFAULT 'success',
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS keys (
    tg_id        BIGINT  NOT NULL,
    client_id    TEXT    NOT NULL,
    email        TEXT    NOT NULL,
    created_at   BIGINT  NOT NULL,
    expiry_time  BIGINT  NOT NULL,
    key          TEXT,
    server_id    TEXT    NOT NULL DEFAULT 'cluster1',
    notified     BOOLEAN NOT NULL DEFAULT FALSE,
    notified_24h BOOLEAN NOT NULL DEFAULT FALSE,
    remnawave_link TEXT,
    is_frozen    BOOLEAN DEFAULT FALSE,
    alias        TEXT,
    PRIMARY KEY (tg_id, client_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'keys' AND column_name = 'remnawave_link'
    ) THEN
        ALTER TABLE keys ADD COLUMN remnawave_link TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'keys' AND column_name = 'is_frozen'
    ) THEN
        ALTER TABLE keys ADD COLUMN is_frozen BOOLEAN DEFAULT FALSE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'keys' AND column_name = 'alias'
    ) THEN
        ALTER TABLE keys ADD COLUMN alias TEXT;
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS referrals (
    referred_tg_id BIGINT PRIMARY KEY NOT NULL,
    referrer_tg_id BIGINT NOT NULL,
    reward_issued  BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS coupons (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    amount      INTEGER NOT NULL,
    days        INTEGER CHECK (days > 0 OR days IS NULL),
    usage_limit INTEGER NOT NULL DEFAULT 1,
    usage_count INTEGER NOT NULL DEFAULT 0,
    is_used     BOOLEAN NOT NULL DEFAULT FALSE
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'coupons' AND column_name = 'days'
    ) THEN
        ALTER TABLE coupons ADD COLUMN days INTEGER CHECK (days > 0 OR days IS NULL);
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS coupon_usages (
    coupon_id INTEGER NOT NULL REFERENCES coupons (id) ON DELETE CASCADE,
    user_id   BIGINT NOT NULL,
    used_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (coupon_id, user_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    tg_id                  BIGINT NOT NULL,
    last_notification_time TIMESTAMP NOT NULL DEFAULT NOW(),
    notification_type      TEXT NOT NULL,
    PRIMARY KEY (tg_id, notification_type)
);

CREATE TABLE IF NOT EXISTS servers (
    id               SERIAL PRIMARY KEY,
    cluster_name     TEXT NOT NULL,
    server_name      TEXT NOT NULL,
    api_url          TEXT NOT NULL,
    subscription_url TEXT,
    inbound_id       TEXT NOT NULL,
    panel_type       TEXT NOT NULL DEFAULT '3x-ui',
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    max_keys         INTEGER,
    tariff_group     TEXT,
    UNIQUE (cluster_name, server_name)
);

CREATE TABLE IF NOT EXISTS gifts (
    gift_id         TEXT PRIMARY KEY NOT NULL,
    sender_tg_id    BIGINT NOT NULL REFERENCES users (tg_id),
    selected_months INTEGER NOT NULL,
    expiry_time     TIMESTAMP WITH TIME ZONE NOT NULL,
    gift_link       TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_used         BOOLEAN NOT NULL DEFAULT FALSE,
    recipient_tg_id BIGINT REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS temporary_data (
    tg_id      BIGINT PRIMARY KEY NOT NULL,
    state      TEXT NOT NULL,
    data       JSONB NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blocked_users (
    tg_id      BIGINT PRIMARY KEY,
    blocked_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tracking_sources (
    id         SERIAL PRIMARY KEY,
    code       TEXT UNIQUE NOT NULL,
    type       TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by BIGINT,
    is_active  BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS tariffs (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    group_code    TEXT NOT NULL,
    duration_days INTEGER NOT NULL CHECK (duration_days > 0),
    price_rub     INTEGER NOT NULL CHECK (price_rub >= 0),
    traffic_limit BIGINT,
    device_limit  INTEGER,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'tariffs' AND column_name = 'device_limit'
    ) THEN
        ALTER TABLE tariffs ADD COLUMN device_limit INTEGER;
    END IF;
END$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'connections'
    ) THEN
        EXECUTE $upd$
            UPDATE users
            SET balance = c.balance,
                trial = c.trial
            FROM connections c
            WHERE users.tg_id = c.tg_id;
        $upd$;

        EXECUTE 'DROP TABLE connections';
    END IF;
END$$;
