import os
import uuid

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    flash,
    session,
    jsonify,
)
from werkzeug.utils import secure_filename
import qrcode
import razorpay
from dotenv import load_dotenv

# Load environment variables from .env if present. [web:63]
load_dotenv()

app = Flask(__name__)

# Basic configuration
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-this-in-production")
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
app.config["QR_FOLDER"] = os.path.join(app.root_path, "static", "qr_codes")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["QR_FOLDER"], exist_ok=True)

# Optional: restrict types
ALLOWED_EXTENSIONS = None  # or set like {"png", "jpg", "jpeg", "gif", "mp4", "pdf"}


def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    if ALLOWED_EXTENSIONS is None:
        return True
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


# ---------------- Razorpay config ----------------

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    print("WARNING: Razorpay keys not set. Payment will not work.")

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
PRICE_INR = 1  # change this to your price in rupees


# ---------------- Core QR routes ----------------

@app.route("/", methods=["GET", "POST"])
def index():
    paid = session.get("paid", False)
    qr_image_url = None
    target_url = None

    if request.method == "POST":
        # Block QR generation if not paid
        if not paid:
            flash("Please complete payment to generate QR codes.")
            return redirect(url_for("index"))

        mode = request.form.get("mode")

        # Case 1: URL or text
        if mode == "url":
            raw_value = request.form.get("url", "").strip()
            if not raw_value:
                flash("Please enter a URL or some text.")
                return redirect(url_for("index"))

            if raw_value.startswith(("http://", "https://")):
                target_url = raw_value
            else:
                if " " in raw_value:
                    target_url = raw_value
                else:
                    target_url = "https://" + raw_value

        # Case 2: file upload
        elif mode == "file":
            file = request.files.get("file")
            if not file or file.filename == "":
                flash("Please choose a file to upload.")
                return redirect(url_for("index"))

            if not allowed_file(file.filename):
                flash("File type not allowed.")
                return redirect(url_for("index"))

            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(save_path)

            target_url = url_for("serve_file", filename=filename, _external=True)

        else:
            flash("Invalid submission.")
            return redirect(url_for("index"))

        # Generate QR
        qr_filename = f"{uuid.uuid4().hex}.png"
        qr_path = os.path.join(app.config["QR_FOLDER"], qr_filename)

        img = qrcode.make(target_url)
        img.save(qr_path)

        qr_image_url = url_for("static", filename=f"qr_codes/{qr_filename}")

    return render_template(
        "index.html",
        qr_image_url=qr_image_url,
        target_url=target_url,
        paid=paid,
        razorpay_key_id=RAZORPAY_KEY_ID,
        price_inr=PRICE_INR,
    )


@app.route("/file/<path:filename>")
def serve_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)


# ---------------- Razorpay order endpoint ----------------
# Server-side order creation using Orders API. [web:42][web:48]

@app.route("/create_order", methods=["POST"])
def create_order():
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return jsonify({"error": "Payment not configured"}), 500

    data = request.get_json(silent=True) or {}
    amount_rupees = data.get("amount") or PRICE_INR

    try:
        amount_rupees = float(amount_rupees)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount_rupees <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    amount_paise = int(round(amount_rupees * 100))  # Razorpay uses paise. [web:42]

    try:
        order = razorpay_client.order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": f"rcpt_{uuid.uuid4().hex[:10]}",
                "payment_capture": 1,
                "notes": {"product": "Sharp QR Code Generator Access"},
            }
        )
    except Exception as e:
        print("Razorpay order creation failed:", e)
        return jsonify({"error": "Failed to create order"}), 500

    return jsonify(
        {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
        }
    )


# ---------------- Razorpay callback handler ----------------
# Razorpay posts here after successful payment if callback_url is set. [web:42][web:53]

@app.route("/payment_handler", methods=["POST"])
def payment_handler():
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        flash("Payment is not configured on the server.")
        return redirect(url_for("index"))

    razorpay_payment_id = request.form.get("razorpay_payment_id")
    razorpay_order_id = request.form.get("razorpay_order_id")
    razorpay_signature = request.form.get("razorpay_signature")

    if not (razorpay_payment_id and razorpay_order_id and razorpay_signature):
        flash("Missing payment details from Razorpay.")
        return redirect(url_for("index"))

    params_dict = {
        "razorpay_order_id": razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id,
        "razorpay_signature": razorpay_signature,
    }

    try:
        razorpay_client.utility.verify_payment_signature(params_dict)  # raises on error [web:42][web:54]
    except razorpay.errors.SignatureVerificationError as e:
        print("Payment signature verification failed:", e)
        flash("Payment verification failed. If you were charged, please contact support.")
        return redirect(url_for("index"))

    # Success: unlock generator for this session
    session["paid"] = True
    flash("Payment successful! QR generator unlocked.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
