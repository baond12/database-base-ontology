CREATE TABLE brands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    brand_id INTEGER REFERENCES brands(id),
    price REAL CHECK (price >= 0)
);
