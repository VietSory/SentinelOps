CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    currency CHAR(3) NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

INSERT INTO inventory(sku, name, quantity) VALUES
    ('sku-001', 'Mechanical Keyboard', 50),
    ('sku-002', 'Wireless Mouse', 100),
    ('sku-003', 'USB-C Dock', 25)
ON CONFLICT (sku) DO NOTHING;
