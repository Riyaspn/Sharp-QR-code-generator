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
)
from werkzeug.utils import secure_filename
import qrcode

app = Flask(__name__)

# Basic configuration
app.config["SECRET_KEY"] = "change-this-in-production"
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "uploads")
app.config["QR_FOLDER"] = os.path.join(app.root_path, "static", "qr_codes")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload

# Create folders if they don't exist
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["QR_FOLDER"], exist_ok=True)

# Optional: restrict file types if you want
ALLOWED_EXTENSIONS = None  # or set like {"png", "jpg", "jpeg", "gif", "mp4", "pdf"}

def allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    if ALLOWED_EXTENSIONS is None:
        return True
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@app.route("/", methods=["GET", "POST"])
def index():
    qr_image_url = None
    target_url = None

    if request.method == "POST":
        mode = request.form.get("mode")

        # Case 1: user provides URL or text
        if mode == "url":
            raw_value = request.form.get("url", "").strip()
            if not raw_value:
                flash("Please enter a URL or some text.")
                return redirect(url_for("index"))

            # If it looks like a URL without scheme, prepend https://
            if raw_value.startswith(("http://", "https://")):
                target_url = raw_value
            else:
                # You can treat any text as-is, or force URL format.
                # Here we assume if it has spaces we keep it as plain text,
                # otherwise we treat it as URL and prepend https.
                if " " in raw_value:
                    target_url = raw_value
                else:
                    target_url = "https://" + raw_value

        # Case 2: user uploads a file
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

            # Public URL that QR will point to
            target_url = url_for("serve_file", filename=filename, _external=True)

        else:
            flash("Invalid submission.")
            return redirect(url_for("index"))

        # Generate QR code image for target_url (or text)
        qr_filename = f"{uuid.uuid4().hex}.png"
        qr_path = os.path.join(app.config["QR_FOLDER"], qr_filename)

        img = qrcode.make(target_url)
        img.save(qr_path)

        qr_image_url = url_for("static", filename=f"qr_codes/{qr_filename}")

        return render_template(
            "index.html",
            qr_image_url=qr_image_url,
            target_url=target_url,
        )

    return render_template("index.html", qr_image_url=qr_image_url, target_url=target_url)


@app.route("/file/<path:filename>")
def serve_file(filename):
    """
    Serves uploaded files so that QR codes can point to them.
    Scans are unlimited as long as the file exists on the server.
    """
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)


if __name__ == "__main__":
    # For local development
    app.run(host="0.0.0.0", port=5000, debug=True)
