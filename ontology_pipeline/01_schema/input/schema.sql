-- Schema only: no rows or sample values.
CREATE TABLE addresses (
  address_id TEXT NOT NULL PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id) ON UPDATE CASCADE ON DELETE CASCADE,
  recipient_name TEXT,
  phone TEXT,
  formatted_address TEXT NOT NULL,
  address_line TEXT,
  ward TEXT,
  district TEXT,
  province TEXT,
  country TEXT NOT NULL DEFAULT 'Vietnam',
  latitude REAL CHECK(latitude BETWEEN -90 AND 90),
  longitude REAL CHECK(longitude BETWEEN -180 AND 180),
  google_place_id TEXT,
  address_source TEXT NOT NULL DEFAULT 'CUSTOMER_INPUT'
    CHECK(address_source IN ('MOCK','CUSTOMER_INPUT','GOOGLE_PLACES','GOOGLE_GEOCODING','OTHER')),
  customer_confirmed_at TEXT,
  google_last_checked_at TEXT,
  is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE agents (
  agent_id TEXT NOT NULL PRIMARY KEY,
  agent_code TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  store_id INTEGER REFERENCES stores(store_id),
  email TEXT UNIQUE,
  phone TEXT,
  team TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','INACTIVE')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE brands (
  brand_id INTEGER PRIMARY KEY,
  brand_name TEXT NOT NULL UNIQUE
);

CREATE TABLE categories (
  category_id INTEGER PRIMARY KEY,
  category_name TEXT NOT NULL,
  parent_category_id INTEGER REFERENCES categories(category_id)
);

CREATE TABLE channels (
  channel_id INTEGER PRIMARY KEY,
  channel_code TEXT NOT NULL UNIQUE,
  channel_name TEXT NOT NULL,
  channel_type TEXT NOT NULL CHECK(channel_type IN ('marketplace','web','offline','social','chat'))
);

CREATE TABLE chats (
  chat_id TEXT NOT NULL PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  agent_id TEXT REFERENCES agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  product_id VARCHAR(50) REFERENCES products(product_id) ON UPDATE CASCADE ON DELETE SET NULL,
  channel_id INTEGER REFERENCES channels(channel_id),
  chat_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sender_type TEXT NOT NULL CHECK(sender_type IN ('AGENT','CUSTOMER','SYSTEM')),
  content TEXT NOT NULL CHECK(length(trim(content)) > 0),
  intent_label TEXT,
  sentiment_label TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  customer_code TEXT NOT NULL UNIQUE,
  region_id INTEGER NOT NULL REFERENCES regions(region_id),
  customer_segment TEXT NOT NULL CHECK(customer_segment IN ('Retail','VIP','Wholesale')),
  joined_at TEXT NOT NULL
);

CREATE TABLE inventory_snapshots (
  snapshot_id INTEGER PRIMARY KEY,
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  product_id VARCHAR(50) NOT NULL REFERENCES products(product_id),
  snapshot_date TEXT NOT NULL,
  qty_on_hand INTEGER NOT NULL CHECK(qty_on_hand >= 0),
  qty_reserved INTEGER NOT NULL CHECK(qty_reserved >= 0 AND qty_reserved <= qty_on_hand),
  UNIQUE(store_id, product_id, snapshot_date)
);

CREATE TABLE order_items (
  order_item_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  product_id VARCHAR(50) NOT NULL REFERENCES products(product_id),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_price INTEGER NOT NULL CHECK(unit_price >= 0),
  line_discount INTEGER NOT NULL CHECK(line_discount >= 0),
  promotion_id INTEGER REFERENCES promotions(promotion_id)
);

CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY,
  order_code TEXT NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  channel_id INTEGER NOT NULL REFERENCES channels(channel_id),
  store_id INTEGER NOT NULL REFERENCES stores(store_id),
  chat_id TEXT REFERENCES chats(chat_id) ON UPDATE CASCADE ON DELETE SET NULL,
  agent_id TEXT REFERENCES agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL,
  ordered_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('placed','paid','shipped','delivered','cancelled')),
  gross_amount INTEGER NOT NULL CHECK(gross_amount >= 0),
  discount_amount INTEGER NOT NULL CHECK(discount_amount >= 0),
  shipping_fee INTEGER NOT NULL CHECK(shipping_fee >= 0)
);

CREATE TABLE payments (
  payment_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(order_id),
  payment_method TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','failed','success')),
  amount INTEGER NOT NULL CHECK(amount >= 0),
  paid_at TEXT
);

CREATE TABLE product_suppliers (
  product_id VARCHAR(50) NOT NULL REFERENCES products(product_id),
  supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
  is_primary INTEGER NOT NULL CHECK(is_primary IN (0,1)),
  PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE products (
  product_id VARCHAR(50) NOT NULL PRIMARY KEY,
  sku VARCHAR(100) UNIQUE NOT NULL,
  name VARCHAR(255) NOT NULL,
  product_url TEXT,
  brand_id INTEGER REFERENCES brands(brand_id),
  category_id INTEGER REFERENCES categories(category_id),
  promotion_id INTEGER REFERENCES promotions(promotion_id),
  original_price NUMERIC(12,2),
  discount_price NUMERIC(12,2),
  currency VARCHAR(10) DEFAULT 'VND',
  discount_percentage NUMERIC(5,2),
  is_in_stock BOOLEAN,
  stock_quantity INTEGER,
  delivery_locations JSONB,
  specifications JSONB,
  warranty JSONB,
  images JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  description TEXT,
  description_embedding BLOB
);

CREATE TABLE promotions (
  promotion_id INTEGER PRIMARY KEY,
  promo_code TEXT NOT NULL UNIQUE,
  promo_name TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  discount_pct INTEGER NOT NULL CHECK(discount_pct BETWEEN 0 AND 100)
);

CREATE TABLE regions (
  region_id INTEGER PRIMARY KEY,
  region_code TEXT NOT NULL UNIQUE,
  region_name TEXT NOT NULL
);

CREATE TABLE returns (
  return_id INTEGER PRIMARY KEY,
  order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
  return_qty INTEGER NOT NULL CHECK(return_qty > 0),
  refund_amount INTEGER NOT NULL CHECK(refund_amount >= 0),
  status TEXT NOT NULL CHECK(status IN ('requested','approved','rejected')),
  requested_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE TABLE shipments (
  shipment_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id),
  shipping_address_id TEXT REFERENCES addresses(address_id) ON UPDATE CASCADE ON DELETE SET NULL,
  carrier TEXT NOT NULL,
  shipped_at TEXT NOT NULL,
  delivered_at TEXT,
  shipping_status TEXT NOT NULL CHECK(shipping_status IN ('in_transit','delivered')),
  ship_to_recipient_name TEXT,
  ship_to_phone TEXT,
  ship_to_formatted_address TEXT,
  ship_to_ward TEXT,
  ship_to_district TEXT,
  ship_to_province TEXT,
  ship_to_country TEXT DEFAULT 'Vietnam',
  ship_to_latitude REAL CHECK(ship_to_latitude BETWEEN -90 AND 90),
  ship_to_longitude REAL CHECK(ship_to_longitude BETWEEN -180 AND 180)
);

CREATE TABLE stores (
  store_id INTEGER PRIMARY KEY,
  store_code TEXT NOT NULL UNIQUE,
  store_name TEXT NOT NULL,
  region_id INTEGER NOT NULL REFERENCES regions(region_id),
  city TEXT NOT NULL,
  opened_on TEXT NOT NULL
);

CREATE TABLE suppliers (
  supplier_id INTEGER PRIMARY KEY,
  supplier_name TEXT NOT NULL UNIQUE,
  region_id INTEGER NOT NULL REFERENCES regions(region_id)
);

CREATE TABLE support_tickets (
  ticket_id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES orders(order_id),
  customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
  issue_type TEXT NOT NULL,
  priority TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','in_progress','resolved')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX idx_addresses_customer ON addresses(customer_id);

CREATE INDEX idx_addresses_google_place ON addresses(google_place_id);

CREATE UNIQUE INDEX idx_addresses_one_default_per_customer ON addresses(customer_id) WHERE is_default = 1;

CREATE INDEX idx_agents_store ON agents(store_id);

CREATE INDEX idx_chats_agent_time ON chats(agent_id, chat_time);

CREATE INDEX idx_chats_channel_time ON chats(channel_id, chat_time);

CREATE INDEX idx_chats_conversation_time ON chats(conversation_id, chat_time);

CREATE INDEX idx_chats_customer_time ON chats(customer_id, chat_time);

CREATE INDEX idx_chats_product ON chats(product_id);

CREATE INDEX idx_customers_region ON customers(region_id);

CREATE INDEX idx_inventory_asof ON inventory_snapshots(store_id, product_id, snapshot_date);

CREATE INDEX idx_items_order ON order_items(order_id);

CREATE INDEX idx_items_product ON order_items(product_id);

CREATE INDEX idx_orders_agent ON orders(agent_id);

CREATE INDEX idx_orders_channel ON orders(channel_id);

CREATE INDEX idx_orders_chat ON orders(chat_id);

CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE INDEX idx_orders_time_status ON orders(ordered_at, status);

CREATE INDEX idx_payments_order_status ON payments(order_id, status);

CREATE INDEX idx_product_suppliers_supplier ON product_suppliers(supplier_id);

CREATE INDEX idx_products_brand ON products(brand_id);

CREATE INDEX idx_products_category ON products(category_id);

CREATE INDEX idx_products_price ON products(discount_price);

CREATE INDEX idx_products_promotion ON products(promotion_id);

CREATE INDEX idx_products_stock ON products(is_in_stock);

CREATE INDEX idx_returns_item_status ON returns(order_item_id, status);

CREATE INDEX idx_shipments_address ON shipments(shipping_address_id);

CREATE INDEX idx_stores_region ON stores(region_id);

CREATE INDEX idx_suppliers_region ON suppliers(region_id);

CREATE INDEX idx_tickets_created ON support_tickets(created_at);

CREATE INDEX idx_tickets_customer ON support_tickets(customer_id);
