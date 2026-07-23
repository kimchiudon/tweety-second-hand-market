PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    nickname TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    bio TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 100000,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(username) BETWEEN 3 AND 24),
    CHECK (length(nickname) BETWEEN 2 AND 20),
    CHECK (length(bio) <= 300),
    CHECK (balance BETWEEN 0 AND 1000000000),
    CHECK (role IN ('user', 'admin')),
    CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    buyer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    price INTEGER NOT NULL,
    category TEXT NOT NULL,
    item_condition TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    moderation_status TEXT NOT NULL DEFAULT 'visible',
    image_filename TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(title) BETWEEN 2 AND 80),
    CHECK (length(description) BETWEEN 10 AND 2000),
    CHECK (price BETWEEN 0 AND 100000000),
    CHECK (category IN ('digital', 'fashion', 'home', 'books', 'sports', 'other')),
    CHECK (item_condition IN ('new', 'like_new', 'good', 'fair')),
    CHECK (status IN ('available', 'sold')),
    CHECK (moderation_status IN ('visible', 'hidden'))
);

CREATE INDEX IF NOT EXISTS idx_products_status_created ON products(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_seller ON products(seller_id);

CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 9),
    UNIQUE(product_id, position)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    image_filename TEXT,
    read_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(body) BETWEEN 1 AND 500),
    CHECK (recipient_id <> sender_id)
);


CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (target_type IN ('user', 'product')),
    CHECK (length(reason) BETWEEN 10 AND 500),
    CHECK (status IN ('open', 'resolved', 'dismissed')),
    UNIQUE(reporter_id, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS message_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 9),
    UNIQUE(message_id, position)
);

CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id, status);

CREATE TABLE IF NOT EXISTS user_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (blocker_id <> blocked_id),
    UNIQUE(blocker_id, blocked_id)
);

CREATE INDEX IF NOT EXISTS idx_user_blocks_pair ON user_blocks(blocker_id, blocked_id);

CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (sender_id <> recipient_id)
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_hash TEXT NOT NULL,
    attempted_at INTEGER NOT NULL,
    successful INTEGER NOT NULL DEFAULT 0 CHECK (successful IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup ON login_attempts(identity_hash, attempted_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    ip_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
