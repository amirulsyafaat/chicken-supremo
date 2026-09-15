from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import mysql.connector
import base64
from email.message import EmailMessage
from io import BytesIO
import json
import os
import smtplib
from datetime import datetime
from uuid import uuid4
from werkzeug.utils import secure_filename
from zoneinfo import ZoneInfo
import qrcode

import os

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads', 'payment-proofs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


app = Flask(__name__)
CORS(app)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'payment-proofs')
ALLOWED_PROOF_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MAX_PROOF_BYTES = 5 * 1024 * 1024
PAYNOW_MOBILE = '91473677'
PAYNOW_RECIPIENT = 'Chicken Supremo'
ORDER_CUTOFF_HOUR = int(os.getenv('ORDER_CUTOFF_HOUR', '19'))
DAILY_ORDER_LIMIT = 30
SINGAPORE_TZ = ZoneInfo('Asia/Singapore')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =========================================================
# Fill in your MySQL credentials here
# =========================================================
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "root"),
    "database": os.getenv("DB_NAME", "chicken_supremo_db"),
    "port": int(os.getenv("DB_PORT", "3306"))
}



def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def add_column_if_missing(column_sql):
    """Safe for existing local databases that were created before this feature."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE orders ADD COLUMN {column_sql}")
        conn.commit()
    except mysql.connector.Error as error:
        if error.errno != 1060:  # Duplicate column is expected after the first run.
            raise
    finally:
        cursor.close()
        conn.close()


def ensure_order_columns():
    add_column_if_missing('customer_email VARCHAR(254) DEFAULT NULL')
    add_column_if_missing('payment_proof_path VARCHAR(255) DEFAULT NULL')
    add_column_if_missing('delivery_instructions TEXT DEFAULT NULL')


def get_store_status():
    now = datetime.now(SINGAPORE_TZ)
    if now.hour >= ORDER_CUTOFF_HOUR:
        return {
            'isOpen': False,
            'reason': 'cutoff',
            'message': 'Ordering is closed for today. Please come back tomorrow.',
            'ordersToday': 0,
            'orderLimit': DAILY_ORDER_LIMIT,
        }

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT COUNT(*) FROM orders WHERE order_time >= %s', (today_start,))
        orders_today = cursor.fetchone()[0]
    finally:
        cursor.close()
        conn.close()

    if orders_today >= DAILY_ORDER_LIMIT:
        return {
            'isOpen': False,
            'reason': 'capacity',
            'message': 'We are fully booked for today. All items are out of stock — please come back tomorrow.',
            'ordersToday': orders_today,
            'orderLimit': DAILY_ORDER_LIMIT,
        }
    return {
        'isOpen': True,
        'reason': None,
        'message': f'{DAILY_ORDER_LIMIT - orders_today} order slots remaining today.',
        'ordersToday': orders_today,
        'orderLimit': DAILY_ORDER_LIMIT,
    }


def reject_if_store_closed():
    status = get_store_status()
    return status if not status['isOpen'] else None


def calculate_order(items):
    if not isinstance(items, list) or not items:
        raise ValueError('Your cart is empty.')

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        order_items, total = [], 0
        for entry in items:
            product_id = int(entry.get('id', 0))
            qty = int(entry.get('qty', 0))
            if qty < 1 or qty > 20:
                raise ValueError('Each item quantity must be between 1 and 20.')
            cursor.execute('SELECT name, price FROM products WHERE product_id = %s AND is_available = 1', (product_id,))
            product = cursor.fetchone()
            if not product:
                raise ValueError(f'One of the selected menu items is unavailable.')
            price = float(product['price'])
            total += price * qty
            order_items.append({'product_id': product_id, 'name': product['name'], 'price': price, 'qty': qty})
        return order_items, round(total, 2)
    finally:
        cursor.close()
        conn.close()


def tlv(tag, value):
    return f'{tag}{len(value):02d}{value}'


def crc16_ccitt(payload):
    crc = 0xFFFF
    for char in payload.encode('utf-8'):
        crc ^= char << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f'{crc:04X}'


def paynow_payload(amount):
    """EMVCo/PayNow mobile-proxy QR with a fixed SGD amount."""
    account = ''.join((
        tlv('00', 'SG.PAYNOW'),
        tlv('01', '0'),  # PayNow mobile proxy
        tlv('02', f'+65{PAYNOW_MOBILE}'),
        tlv('03', '0'),  # Customer cannot edit the amount
    ))
    payload = ''.join((
        tlv('00', '01'),
        tlv('01', '12'),
        tlv('26', account),
        tlv('52', '0000'),
        tlv('53', '702'),
        tlv('54', f'{amount:.2f}'),
        tlv('58', 'SG'),
        tlv('59', PAYNOW_RECIPIENT[:25]),
        tlv('60', 'Singapore'),
        '6304',
    ))
    return payload + crc16_ccitt(payload)


def make_qr_data_url(amount):
    image = qrcode.make(paynow_payload(amount))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def send_order_email(order, subject, heading):
    """Uses SMTP only when credentials are configured as environment variables."""
    smtp_host = os.getenv('SMTP_HOST')
    smtp_user = os.getenv('SMTP_USER')
    smtp_password = os.getenv('SMTP_PASSWORD')
    sender = os.getenv('SMTP_FROM', smtp_user or '')
    if not all((smtp_host, smtp_user, smtp_password, sender)):
        print('Email not sent: configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM.')
        return False

    lines = '\n'.join(f"- {item['name']} x{item['qty']} — ${item['price'] * item['qty']:.2f}" for item in order['items'])
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message['To'] = order['email']
    message.set_content(
        f"{heading}\n\nOrder #{order['id']}\nDelivery: {order['hall']}\n\n{lines}\n\nTotal: ${order['total']:.2f}\nStatus: {order['status']}\n\nChicken Supremo"
    )
    try:
        with smtplib.SMTP(smtp_host, int(os.getenv('SMTP_PORT', '587')), timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
        return True
    except Exception as error:
        print(f'Email not sent: {error}')
        return False


# ---------------------------------------------------------
# GET /api/menu
# Reads live from the products + categories tables.
# ---------------------------------------------------------
@app.route('/api/menu', methods=['GET'])
def get_menu():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.product_id AS id, p.name, p.note, p.price, p.image_url AS image, c.slug AS cat
        FROM products p
        JOIN categories c ON p.category_id = c.category_id
        WHERE p.is_available = 1
        ORDER BY c.display_order, p.product_id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    # Convert DECIMAL to float so it JSON-serializes cleanly
    for row in rows:
        row['price'] = float(row['price'])

    return jsonify(rows), 200


@app.route('/api/store-status', methods=['GET'])
def store_status():
    return jsonify(get_store_status()), 200


# ---------------------------------------------------------
# POST /api/orders
# Customer places an order — inserted into orders + order_items.
# ---------------------------------------------------------
@app.route('/api/orders', methods=['POST'])
def create_order():
    data = request.get_json()

    if not data or not all(data.get(key, '').strip() for key in ('name', 'phone', 'hall', 'email')):
        return jsonify({"success": False, "error": "Name, phone number, delivery location, and email are required."}), 400
    closed_status = reject_if_store_closed()
    if closed_status:
        return jsonify({"success": False, "error": closed_status['message']}), 409

    payment_method = data.get('paymentMethod', 'card')
    txn_ref = data.get('txnRef')

    if payment_method == 'paynow':
        status = 'Pending Bank Verification'
    elif payment_method == 'cash':
        status = 'Pending Cash on Delivery'
    else:
        status = 'Paid (Card)'

    try:
        order_items, total = calculate_order(data.get('items'))
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "error": str(error)}), 400

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO orders (customer_name, customer_phone, customer_email, hall_block, delivery_instructions, payment_method, txn_ref, status, total)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (data['name'], data['phone'], data['email'], data['hall'], data.get('deliveryInstructions', '').strip() or None, payment_method, txn_ref, status, total))
    order_id = cursor.lastrowid

    for item in order_items:
        cursor.execute("""
            INSERT INTO order_items (order_id, product_id, name, price, qty)
            VALUES (%s, %s, %s, %s, %s)
        """, (order_id, item['product_id'], item['name'], item['price'], item['qty']))

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\n--- NEW ORDER RECEIVED ---")
    print(f"Order ID: #{order_id} | Customer: {data['name']} | Total: ${total:.2f}")

    return jsonify({"success": True, "orderId": order_id, "status": status}), 201


# ---------------------------------------------------------
# PayNow QR + proof upload
# ---------------------------------------------------------
@app.route('/api/paynow-qr', methods=['POST'])
def get_paynow_qr():
    data = request.get_json() or {}
    closed_status = reject_if_store_closed()
    if closed_status:
        return jsonify({"success": False, "error": closed_status['message']}), 409
    try:
        _, total = calculate_order(data.get('items'))
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "error": str(error)}), 400
    return jsonify({
        "success": True,
        "amount": total,
        "recipient": PAYNOW_RECIPIENT,
        "mobile": PAYNOW_MOBILE,
        "qrDataUrl": make_qr_data_url(total),
    })


@app.route('/api/orders/paynow', methods=['POST'])
def create_paynow_order():
    try:
        data = json.loads(request.form.get('order', '{}'))
    except json.JSONDecodeError:
        return jsonify({"success": False, "error": "Invalid order details."}), 400

    if not all(data.get(key, '').strip() for key in ('name', 'phone', 'hall', 'email')):
        return jsonify({"success": False, "error": "Name, phone number, delivery location, and email are required."}), 400
    closed_status = reject_if_store_closed()
    if closed_status:
        return jsonify({"success": False, "error": closed_status['message']}), 409
    proof = request.files.get('paymentProof')
    if not proof or not proof.filename:
        return jsonify({"success": False, "error": "Please upload your PayNow payment screenshot."}), 400
    extension = proof.filename.rsplit('.', 1)[-1].lower() if '.' in proof.filename else ''
    if extension not in ALLOWED_PROOF_EXTENSIONS:
        return jsonify({"success": False, "error": "Payment proof must be a PNG or JPG image."}), 400
    proof.stream.seek(0, os.SEEK_END)
    size = proof.stream.tell()
    proof.stream.seek(0)
    if size > MAX_PROOF_BYTES:
        return jsonify({"success": False, "error": "Payment proof must be 5 MB or smaller."}), 400

    try:
        order_items, total = calculate_order(data.get('items'))
    except (ValueError, TypeError) as error:
        return jsonify({"success": False, "error": str(error)}), 400

    filename = f'{uuid4().hex}.{extension}'
    proof.save(os.path.join(UPLOAD_FOLDER, filename))
    proof_path = f'/uploads/payment-proofs/{filename}'
    status = 'Pending Bank Verification'
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO orders (customer_name, customer_phone, customer_email, hall_block, delivery_instructions, payment_method, payment_proof_path, status, total)
            VALUES (%s, %s, %s, %s, %s, 'paynow', %s, %s, %s)
        """, (data['name'], data['phone'], data['email'], data['hall'], data.get('deliveryInstructions', '').strip() or None, proof_path, status, total))
        order_id = cursor.lastrowid
        for item in order_items:
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, name, price, qty)
                VALUES (%s, %s, %s, %s, %s)
            """, (order_id, item['product_id'], item['name'], item['price'], item['qty']))
        conn.commit()
    except Exception:
        conn.rollback()
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, filename))
        except OSError:
            pass
        raise
    finally:
        cursor.close()
        conn.close()

    email_sent = send_order_email({
        'id': order_id, 'email': data['email'], 'hall': data['hall'], 'items': order_items,
        'total': total, 'status': status,
    }, f'Chicken Supremo order #{order_id} received', 'We received your PayNow screenshot. Your order is pending payment verification.')
    return jsonify({"success": True, "orderId": order_id, "status": status, "emailSent": email_sent}), 201


@app.route('/uploads/payment-proofs/<path:filename>', methods=['GET'])
def payment_proof(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ---------------------------------------------------------
# GET /api/admin/orders
# Reads live from orders + order_items, shaped for the dashboard.
# ---------------------------------------------------------
@app.route('/api/admin/orders', methods=['GET'])
def get_all_orders():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT order_id, customer_name, customer_phone, customer_email, hall_block, delivery_instructions,
               payment_method, txn_ref, payment_proof_path, status, order_time, total
        FROM orders
        ORDER BY order_id DESC
    """)
    orders = cursor.fetchall()

    result = []
    for o in orders:
        cursor.execute("SELECT product_id AS id, name, price, qty FROM order_items WHERE order_id = %s", (o['order_id'],))
        items = cursor.fetchall()
        for it in items:
            it['price'] = float(it['price'])

        result.append({
            "orderId": o['order_id'],
            "time": o['order_time'].strftime("%I:%M %p"),
            "name": o['customer_name'],
            "phone": o['customer_phone'],
            "email": o['customer_email'],
            "hall": o['hall_block'],
            "deliveryInstructions": o['delivery_instructions'],
            "paymentMethod": o['payment_method'],
            "txnRef": o['txn_ref'] or 'N/A',
            "paymentProof": o['payment_proof_path'],
            "status": o['status'],
            "total": float(o['total']),
            "items": items
        })

    cursor.close()
    conn.close()
    return jsonify(result), 200


# ---------------------------------------------------------
# POST /api/admin/orders/<id>/verify
# ---------------------------------------------------------
@app.route('/api/admin/orders/<int:order_id>/verify', methods=['POST'])
def verify_order(order_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT order_id, customer_email, hall_block, status, total
        FROM orders WHERE order_id = %s
    """, (order_id,))
    order = cursor.fetchone()
    if not order:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "error": "Order not found"}), 404

    cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s",
                   ('Payment Verified & Preparing', order_id))
    affected = cursor.rowcount
    conn.commit()
    cursor.execute("SELECT name, price, qty FROM order_items WHERE order_id = %s", (order_id,))
    order['items'] = [{**item, 'price': float(item['price'])} for item in cursor.fetchall()]
    cursor.close()
    conn.close()

    email_sent = False
    if order['customer_email']:
        order.update({'id': order_id, 'email': order['customer_email'], 'hall': order['hall_block'],
                      'total': float(order['total']), 'status': 'Payment Verified & Preparing'})
        email_sent = send_order_email(
            order,
            f'Chicken Supremo order #{order_id} confirmed',
            'Your payment has been verified. Your order is confirmed and is being prepared.',
        )
    return jsonify({"success": True, "message": f"Order #{order_id} verified!", "emailSent": email_sent}), 200


# ---------------------------------------------------------
# POST /api/admin/orders/<id>/complete
# ---------------------------------------------------------
@app.route('/api/admin/orders/<int:order_id>/complete', methods=['POST'])
def complete_order(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", ('Completed', order_id))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if affected == 0:
        return jsonify({"success": False, "error": "Order not found"}), 404
    return jsonify({"success": True, "message": f"Order #{order_id} completed!"}), 200


# ---------------------------------------------------------
# POST /api/admin/orders/reset
# ---------------------------------------------------------
@app.route('/api/admin/orders/reset', methods=['POST'])
def reset_orders():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM order_items")
    cursor.execute("DELETE FROM orders")
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "All orders have been reset!"}), 200
@app.route('/')
def home():
    return "Chicken Supremo API is up and running!"


if __name__ == '__main__':
    ensure_order_columns()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
