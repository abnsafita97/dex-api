from flask import Flask, request, send_file, jsonify
import os
import shutil
import subprocess
import uuid
import zipfile
import logging
import traceback
from datetime import datetime
import threading
import time
import psutil

# ===== إعداد نظام التسجيل =====
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ===== تعريف التطبيق والتهيئة =====
app = Flask(__name__)
UPLOAD_DIR = "/tmp"
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

BAKSMALI_PATH = "/usr/local/bin/baksmali.jar"
SMALI_PATH = "/usr/local/bin/smali.jar"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== وظيفة لتنظيف المجلدات المؤقتة =====
def delayed_cleanup(directory, delay=30):
    def cleanup():
        logger.info(f"Waiting {delay} seconds before cleaning {directory}")
        time.sleep(delay)
        try:
            if os.path.exists(directory):
                shutil.rmtree(directory, ignore_errors=True)
                logger.info(f"Cleaned up directory: {directory}")
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")
    threading.Thread(target=cleanup, daemon=True).start()

# ===== سجلات النظام =====
logger.info("Starting server with configuration:")
logger.info("Base directory: %s", BASE_DIR)
logger.info("Files: %s", os.listdir(BASE_DIR))
logger.info("Baksmali exists: %s", os.path.exists(BAKSMALI_PATH))
logger.info("Smali exists: %s", os.path.exists(SMALI_PATH))

# ===== طلبات واستجابات =====
@app.before_request
def log_request_info():
    logger.debug("Request: %s %s", request.method, request.url)
    logger.debug("Headers: %s", dict(request.headers))
    logger.debug("Files: %s", list(request.files.keys()))

@app.after_request
def log_response_info(response):
    logger.debug("Response status: %s", response.status)
    return response

# ===== الصفحة الرئيسية =====
@app.route("/")
def home():
    return "DEX API Server is running!", 200

# ===== رفع وتفكيك APK =====
@app.route("/upload", methods=["POST"])
def upload_apk():
    try:
        logger.info("Upload request started")

        # العثور على ملف .apk من أي حقل
        apk_file = None
        for field_name, file in request.files.items():
            if file and file.filename.lower().endswith(".apk"):
                apk_file = file
                logger.info(f"Found APK file in field: {field_name}")
                break

        if apk_file is None:
            logger.error("No file with .apk extension was found")
            return jsonify(error="No file ending with .apk was found"), 400

        if apk_file.filename == '':
            return jsonify(error="No selected file"), 400

        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"apkjob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)

        apk_path = os.path.join(job_dir, "input.apk")
        apk_file.save(apk_path)

        dex_files = []
        with zipfile.ZipFile(apk_path, 'r') as zip_ref:
            for name in zip_ref.namelist():
                if name.startswith('classes') and name.endswith('.dex'):
                    dex_files.append(name)
            if not dex_files:
                return jsonify(error="No DEX files found in APK"), 400
            for dex in dex_files:
                zip_ref.extract(dex, path=job_dir)

        out_dir = os.path.join(job_dir, "smali_out")
        os.makedirs(out_dir, exist_ok=True)

        for dex in dex_files:
            dex_path = os.path.join(job_dir, dex)
            result = subprocess.run(
                ["java", "-jar", BAKSMALI_PATH, "d", dex_path, "-o", out_dir],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                return jsonify(error=f"DEX disassembly failed: {result.stderr}"), 500

        file_count = sum(len(files) for _, _, files in os.walk(out_dir))
        if file_count == 0:
            return jsonify(error="No smali files generated"), 500

        zip_path = os.path.join(job_dir, "smali_out.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
            for root, _, files in os.walk(out_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, out_dir)
                    zipf.write(file_path, arcname)

        response = send_file(zip_path, as_attachment=True, download_name="smali_out.zip", mimetype='application/zip')
        response.headers["Cache-Control"] = "no-store"
        delayed_cleanup(job_dir)
        return response

    except Exception as e:
        logger.exception("Unhandled error in upload_apk")
        return jsonify(error=str(e)), 500

# ===== تجميع ملفات Smali إلى DEX =====
@app.route("/assemble", methods=["POST"])
def assemble_smali():
    try:
        logger.info("Assemble request started")

        # افتراض الحقل "smali"
        if 'smali' not in request.files:
            logger.error("Missing 'smali' field")
            return jsonify(error="'smali' field is required"), 400

        smali_zip = request.files['smali']

        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"assemblejob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)

        zip_path = os.path.join(job_dir, "smali.zip")
        smali_zip.save(zip_path)

        smali_dir = os.path.join(job_dir, "smali")
        os.makedirs(smali_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(smali_dir)

        dex_output = os.path.join(job_dir, "classes.dex")
        result = subprocess.run(
            ["java", "-jar", SMALI_PATH, "a", smali_dir, "-o", dex_output],
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            return jsonify(error=f"DEX assembly failed: {result.stderr}"), 500

        response = send_file(dex_output, as_attachment=True, download_name="classes.dex", mimetype='application/octet-stream')
        delayed_cleanup(job_dir)
        return response

    except Exception as e:
        logger.exception("Unhandled error in assemble_smali")
        return jsonify(error=str(e)), 500

# ===== فحص صحة الخادم =====
@app.route("/health", methods=["GET"])
def health_check():
    try:
        return jsonify({
            "status": "OK",
            "server_time": datetime.utcnow().isoformat(),
            "message": "Basic health check passed"
        })
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# ===== فحص إصدار جافا =====
@app.route("/javacheck", methods=["GET"])
def java_check():
    try:
        result = subprocess.run(
            ["java", "-version"],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            timeout=5
        )
        output = result.stdout + result.stderr
        return jsonify({"status": "OK", "java_version": output.strip()})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "ERROR", "error": "Java check timed out"}), 500
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

# ===== فحص الموارد =====
@app.route("/resources", methods=["GET"])
def resource_check():
    try:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return jsonify({
            "memory": {
                "total": mem.total,
                "available": mem.available,
                "used": mem.used,
                "percent": mem.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            }
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

# ===== فحص الملفات المؤقتة =====
@app.route("/tempfiles", methods=["GET"])
def list_temp_files():
    try:
        temp_files = []
        for f in os.listdir(UPLOAD_DIR):
            if f.startswith("apkjob_") or f.startswith("assemblejob_"):
                path = os.path.join(UPLOAD_DIR, f)
                temp_files.append({
                    "name": f,
                    "path": path,
                    "is_dir": os.path.isdir(path),
                    "created": os.path.getctime(path),
                    "modified": os.path.getmtime(path)
                })
        return jsonify({"status": "OK", "files": temp_files})
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

# ===== التشغيل المحلي =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)