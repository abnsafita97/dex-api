from flask import Flask, request, send_file, jsonify
import os
import uuid
import logging
import traceback
from datetime import datetime
import threading
import time
import psutil
import tempfile
import shutil
from dex_injector import process_apk

# ===== إعداد نظام التسجيل =====
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ===== تعريف التطبيق والتهيئة =====
app = Flask(__name__)
UPLOAD_DIR = tempfile.gettempdir()
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# مسارات الأدوات
BAKSMALI_PATH = "/usr/local/bin/baksmali.jar"
SMALI_PATH = "/usr/local/bin/smali.jar"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MYAPP_SMALI_PATH = os.path.join(BASE_DIR, "MyApp.smali")
MYAPP_CLASS = "com/abnsafita/protection/MyApp"

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
logger.info("MyApp.smali exists: %s", os.path.exists(MYAPP_SMALI_PATH))

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
    return "DEX Protection Server is running!", 200

# ===== رفع وتفكيك APK وإضافة حماية =====
@app.route("/upload", methods=["POST"])
def upload_apk():
    try:
        logger.info("Upload request started")

        # البحث عن ملف APK
        apk_file = None
        for field_name, file in request.files.items():
            if file and file.filename.lower().endswith(".apk"):
                apk_file = file
                logger.info(f"Found APK file in field: {field_name}")
                break

        if apk_file is None:
            logger.error("No file with .apk extension was found")
            return jsonify(error="No file ending with .apk was found"), 400

        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"apkjob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)

        apk_path = os.path.join(job_dir, "input.apk")
        apk_file.save(apk_path)

        # استدعاء العملية الرئيسية من dex_injector
        output_zip = process_apk(
            apk_path=apk_path,
            baksmali_path=BAKSMALI_PATH,
            smali_path=SMALI_PATH,
            myapp_smali_path=MYAPP_SMALI_PATH,
            myapp_class=MYAPP_CLASS
        )

        # إرسال الحزمة
        response = send_file(
            output_zip,
            as_attachment=True,
            download_name="protected.zip",
            mimetype='application/zip'
        )
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
        subprocess.run(
            ["java", "-jar", SMALI_PATH, "a", smali_dir, "-o", dex_output],
            check=True
        )

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
            "message": "Server is operational"
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

# ===== التشغيل المحلي =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)