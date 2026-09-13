-- =========================================================
-- Chicken Supremo — MySQL Schema (real menu, with photos)
-- Run with: mysql -u root -p < chicken_supremo_db.sql
-- =========================================================

CREATE DATABASE IF NOT EXISTS chicken_supremo_db;
USE chicken_supremo_db;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS categories;

-- ---------------------------
-- CATEGORIES
-- slug = value used in the frontend (data-cat="mains" etc.)
-- ---------------------------
CREATE TABLE categories (
    category_id     INT AUTO_INCREMENT PRIMARY KEY,
    slug            VARCHAR(30) NOT NULL UNIQUE,
    display_name    VARCHAR(50) NOT NULL,
    display_order   INT NOT NULL DEFAULT 0
);

INSERT INTO categories (slug, display_name, display_order) VALUES
  ('mains', 'Mains', 1),
  ('pasta', 'Pasta', 2),
  ('burger', 'Burger', 3),
  ('sides', 'Sides', 4);

-- ---------------------------
-- PRODUCTS  (your real menu + photos)
-- image_url is relative to your website folder — put the photo
-- files in an "images" folder next to chicken-supremo-ntu.html
-- ---------------------------
CREATE TABLE products (
    product_id      INT AUTO_INCREMENT PRIMARY KEY,
    category_id     INT NOT NULL,
    name            VARCHAR(150) NOT NULL,
    note            VARCHAR(100) DEFAULT NULL,
    price           DECIMAL(6,2) NOT NULL,
    is_available    TINYINT(1) NOT NULL DEFAULT 1,
    image_url       VARCHAR(255) DEFAULT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

-- Mains
INSERT INTO products (category_id, name, note, price, image_url) VALUES
  (1, 'Spring Chicken', NULL, 9.50, 'images/spring-chicken.jpg'),
  (1, '1/2 Spring Chicken', NULL, 5.50, 'images/half-spring-chicken.jpg'),
  (1, 'Lucky Plate', '2pcs chicken', 4.80, 'images/lucky-plate.jpg'),
  (1, 'Happiness Plate', '3pcs chicken', 6.30, 'images/happiness-plate.jpg'),
  (1, 'Do & Me', '5pcs chicken', 10.00, 'images/do-and-me.jpg'),
  (1, 'Chicken Wings', 'min 2pcs', 2.00, 'images/chicken-wings.jpg'),
  (1, 'Wings Rice Set', '2pcs chicken', 4.50, NULL),
  (1, 'Chicken Chop', NULL, 5.90, 'images/chicken-chop.jpg'),
  (1, 'Black Pepper Chicken Chop', NULL, 5.90, NULL),
  (1, 'Chicken Cutlet', NULL, 5.90, 'images/chicken-cutlet.jpg'),
  (1, 'Fish & Chips', NULL, 5.90, 'images/fish-and-chips.jpg');

-- Pasta
INSERT INTO products (category_id, name, note, price, image_url) VALUES
  (2, 'Bolognese Pasta', NULL, 5.50, NULL),
  (2, 'Mushroom Pasta', NULL, 5.50, NULL),
  (2, 'Chicken Chop Pasta', NULL, 8.50, NULL);

-- Burger (split into two, each with its own photo)
INSERT INTO products (category_id, name, note, price, image_url) VALUES
  (3, 'Fish Burger', NULL, 2.80, 'images/fish-burger.jpg'),
  (3, 'Chicken Burger', NULL, 2.80, 'images/chicken-burger.jpg'),
  (3, 'Beef Burger', NULL, 3.00, NULL);

-- Sides
INSERT INTO products (category_id, name, note, price, image_url) VALUES
  (4, 'French Fries', NULL, 2.50, 'images/french-fries.jpg'),
  (4, 'Cheese Fries', NULL, 3.00, 'images/cheese-fries.jpg'),
  (4, 'Chicken Nuggets', NULL, 2.00, 'images/chicken-nuggets.jpg'),
  (4, 'Onion Rings', NULL, 2.00, 'images/onion-rings.jpg'),
  (4, 'Sotong Ball', NULL, 2.00, NULL),
  (4, 'Coleslaw', NULL, 1.80, 'images/coleslaw.jpg'),
  (4, 'Whipped Potato', NULL, 1.80, 'images/whipped-potato.jpg'),
  (4, 'Bun', 'min 2pcs', 0.50, 'images/bun.jpg'),
  (4, 'Chicken Rice', NULL, 1.00, 'images/chicken-rice.jpg');

-- ---------------------------
-- ORDERS
-- ---------------------------
CREATE TABLE orders (
    order_id        INT AUTO_INCREMENT PRIMARY KEY,
    customer_name   VARCHAR(150) NOT NULL,
    customer_phone  VARCHAR(30) NOT NULL,
    customer_email  VARCHAR(254) DEFAULT NULL,
    hall_block      VARCHAR(100) NOT NULL,
    payment_method  VARCHAR(20) NOT NULL,
    txn_ref         VARCHAR(100) DEFAULT NULL,
    payment_proof_path VARCHAR(255) DEFAULT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'Pending',
    order_time      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total           DECIMAL(8,2) NOT NULL DEFAULT 0
);

-- ---------------------------
-- ORDER ITEMS
-- name/price are snapshotted at order time so the receipt stays
-- accurate even if you later change a product's price or name.
-- ---------------------------
CREATE TABLE order_items (
    order_item_id   INT AUTO_INCREMENT PRIMARY KEY,
    order_id        INT NOT NULL,
    product_id      INT NOT NULL,
    name            VARCHAR(150) NOT NULL,
    price           DECIMAL(6,2) NOT NULL,
    qty             INT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
