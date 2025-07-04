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
import psutil  # إضافة استيراد psutil

# ===== إعداد نظام التسجيل =====
logging.basicConfig(
    level=logging.DEBUG,  # تغيير إلى DEBUG للحصول على تفاصيل أكثر
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ===== تعريف التطبيق والتهيئة =====
app = Flask(__name__)
UPLOAD_DIR = "/tmp"
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# ===== تحديث المسارات لملفات smali و baksmali =====
BAKSMALI_PATH = "/usr/local/bin/baksmali.jar"
SMALI_PATH = "/usr/local/bin/smali.jar"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== وظيفة لتنظيف المجلدات المؤقتة بعد التأخير =====
def delayed_cleanup(directory, delay=30):
    def cleanup():
        logger.info(f"Waiting {delay} seconds before cleaning {directory}")
        time.sleep(delay)
        try:
            if os.path.exists(directory):
                shutil.rmtree(directory, ignore_errors=True)
                logger.info(f"Cleaned up directory: {directory}")
            else:
                logger.warning(f"Directory not found for cleanup: {directory}")
        except Exception as e:
            logger.error(f"Cleanup failed for {directory}: {str(e)}")

    thread = threading.Thread(target=cleanup)
    thread.daemon = True
    thread.start()

# ===== تسجيل معلومات البداية =====
logger.info("Starting server with configuration:")
logger.info("Base directory: %s", BASE_DIR)
logger.info("Files in directory: %s", os.listdir(BASE_DIR))
logger.info("Baksmali path: %s", BAKSMALI_PATH)
logger.info("Smali path: %s", SMALI_PATH)
logger.info("Baksmali exists: %s", os.path.exists(BAKSMALI_PATH))
logger.info("Smali exists: %s", os.path.exists(SMALI_PATH))

# ===== تسجيل الطلبات والاستجابات =====
@app.before_request
def log_request_info():
    logger.debug("Request: %s %s", request.method, request.url)
    logger.debug("Headers: %s", dict(request.headers))
    if request.files:
        logger.debug("Files received: %s", list(request.files.keys()))

@app.after_request
def log_response_info(response):
    logger.debug("Response status: %s", response.status)
    return response

# ===== نقطة البداية =====
@app.route("/")
def home():
    return "DEX API Server is running!", 200

# ===== رفع وتفكيك APK =====
@app.route("/upload", methods=["POST"])
def upload_apk():
    try:
        logger.info("Upload request started")

        # التحقق من وجود ملف APK
        if 'apk' not in request.files:
            logger.error("Missing 'apk' field")
            return jsonify(error="'apk' field is required"), 400

        apk_file = request.files['apk']

        # التحقق من اسم الملف
        if apk_file.filename == '':
            logger.error("No file selected")
            return jsonify(error="No selected file"), 400

        # إنشاء مجلد مؤقت
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"apkjob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        logger.info("Created job directory: %s", job_dir)

        try:
            # حفظ ملف الـ APK
            apk_path = os.path.join(job_dir, "input.apk")
            apk_file.save(apk_path)
            file_size = os.path.getsize(apk_path)
            logger.info("APK saved: %s (%d bytes)", apk_path, file_size)

            # استخراج جميع ملفات DEX
            dex_files = []
            with zipfile.ZipFile(apk_path, 'r') as zip_ref:
                for name in zip_ref.namelist():
                    if name.startswith('classes') and name.endswith('.dex'):
                        dex_files.append(name)

                logger.info("Found DEX files: %s", dex_files)

                if not dex_files:
                    logger.error("No DEX files found in APK")
                    return jsonify(error="APK does not contain any DEX files"), 400

                # استخراج جميع ملفات DEX
                for dex in dex_files:
                    zip_ref.extract(dex, path=job_dir)
                    logger.info("Extracted DEX: %s", dex)

            # إنشاء مجلد الإخراج
            out_dir = os.path.join(job_dir, "smali_out")
            os.makedirs(out_dir, exist_ok=True)
            logger.info("Created output directory: %s", out_dir)

            # تفكيك كل ملفات DEX
            for dex in dex_files:
                dex_path = os.path.join(job_dir, dex)
                logger.info("Disassembling %s", dex_path)

                # تفكيك DEX
                try:
                    result = subprocess.run(
                        ["java", "-jar", BAKSMALI_PATH, "d", dex_path, "-o", out_dir],
                        capture_output=True,
                        text=True,
                        timeout=300  # 5 دقائق لكل ملف DEX
                    )

                    if result.returncode != 0:
                        logger.error("Baksmali failed for %s: %s", dex_path, result.stderr)
                        return jsonify(error=f"DEX disassembly failed: {result.stderr}"), 500

                    logger.info("Baksmali completed for %s", dex)

                except subprocess.TimeoutExpired:
                    logger.error("Baksmali timed out for %s", dex_path)
                    return jsonify(error="DEX disassembly timed out"), 500

            # التحقق من وجود ملفات في مجلد الإخراج
            file_count = sum([len(files) for _, _, files in os.walk(out_dir)])
            if file_count == 0:
                logger.error("No smali files generated in output directory")
                return jsonify(error="No smali files generated"), 500

            # ضغط النتيجة
            zip_path = os.path.join(job_dir, "smali_out.zip")
            logger.info("Creating ZIP archive at: %s", zip_path)

            # استخدم ZIP_STORED لتجنب مشاكل الضغط مع الملفات الكبيرة
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
                for root, dirs, files in os.walk(out_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, out_dir)
                        zipf.write(file_path, arcname)
                        logger.debug("Added to ZIP: %s as %s", file_path, arcname)

            # التحقق من أن ملف ZIP غير فارغ
            zip_size = os.path.getsize(zip_path)
            if zip_size < 1024:  # أقل من 1KB
                logger.error("ZIP file too small: %d bytes", zip_size)
                return jsonify(error="Empty ZIP file created"), 500

            logger.info("ZIP created successfully, size: %d bytes", zip_size)

            # إرسال الملف مع رؤوس HTTP محسنة
            response = send_file(
                zip_path,
                as_attachment=True,
                download_name="smali_out.zip",
                mimetype='application/zip'
            )

            # إضافة رؤوس للتحكم في التخزين المؤقت
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

            # تنظيف المجلد بعد تأخير
            delayed_cleanup(job_dir)

            return response

        except zipfile.BadZipFile:
            logger.exception("Invalid APK file")
            return jsonify(error="Invalid APK file format"), 400
        except Exception as e:
            logger.exception("APK processing error")
            return jsonify(error=f"Internal server error: {str(e)}"), 500

    except Exception as e:
        logger.error("Unhandled error in upload_apk: %s", traceback.format_exc())
        return jsonify(error="Internal server error"), 500

# ===== تجميع Smali إلى DEX =====
@app.route("/assemble", methods=["POST"])
def assemble_smali():
    try:
        logger.info("Assemble request started")

        # التحقق من وجود ملف Smali
        if 'smali' not in request.files:
            logger.error("Missing 'smali' field")
            return jsonify(error="'smali' field is required"), 400

        smali_zip = request.files['smali']

        # إنشاء مجلد مؤقت
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"assemblejob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        logger.info("Created job directory: %s", job_dir)

        try:
            # حفظ ملف الـ ZIP
            zip_path = os.path.join(job_dir, "smali.zip")
            smali_zip.save(zip_path)
            logger.info("Saved smali ZIP: %s", zip_path)

            # استخراج ملفات Smali
            smali_dir = os.path.join(job_dir, "smali")
            os.makedirs(smali_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(smali_dir)
                logger.info("Extracted smali files to: %s", smali_dir)

            # تجميع ملف DEX
            dex_output = os.path.join(job_dir, "classes.dex")
            logger.info("Assembling DEX to: %s", dex_output)

            result = subprocess.run(
                ["java", "-jar", SMALI_PATH, "a", smali_dir, "-o", dex_output],
                capture_output=True,
                text=True,
                timeout=300  # 5 دقائق
            )

            if result.returncode != 0:
                logger.error("Smali assembly failed: %s", result.stderr)
                return jsonify(error=f"DEX assembly failed: {result.stderr}"), 500

            logger.info("Smali assembly succeeded")

            # إرسال الملف
            response = send_file(
                dex_output,
                as_attachment=True,
                download_name="classes.dex",
                mimetype='application/octet-stream'
            )

            # تنظيف المجلد بعد تأخير
            delayed_cleanup(job_dir)

            return response

        except Exception as e:
            logger.exception("Assembly error")
            return jsonify(error=f"Internal server error: {str(e)}"), 500

    except Exception as e:
        logger.error("Unhandled error in assemble_smali: %s", traceback.format_exc())
        return jsonify(error="Internal server error"), 500

# ===== فحص صحة الخادم (مبسط) =====
@app.route("/health", methods=["GET"])
def health_check():
    try:
        # إرجاع فحص صحة مبسط دون استدعاء java -version
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

# ===== فحص جافا =====
@app.route("/javacheck", methods=["GET"])
def java_check():
    try:
        # محاولة الحصول على إصدار جافا
        result = subprocess.run(
            ["java", "-version"],
            stderr=subprocess.PIPE,  # java -version يكتب إلى stderr
            stdout=subprocess.PIPE,
            text=True,
            timeout=5
        )
        # الجمع بين stdout و stderr
        output = result.stdout + result.stderr
        return jsonify({
            "status": "OK",
            "java_version": output.strip()
        })
    except subprocess.TimeoutExpired:
        return jsonify({
            "status": "ERROR",
            "error": "Java check timed out"
        }), 500
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

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
        return jsonify({
            "status": "ERROR",
            "error": str(e)
        }), 500

# ===== نقطة فحص الملفات المؤقتة =====
@app.route("/tempfiles", methods=["GET"])
def list_temp_files():
    try:
        temp_files = []
        for f in os.listdir(UPLOAD_DIR):
            if f.startswith("apkjob_") or f.startswith("assemblejob_"):
                path = os.path.join(UPLOAD_DIR, f)
                size = os.path.getsize(path) if os.path.isfile(path) else 0
                is_dir = os.path.isdir(path)
                temp_files.append({
                    "name": f,
                    "path": path,
                    "size": size,
                    "is_dir": is_dir,
                    "created": os.path.getctime(path),
                    "modified": os.path.getmtime(path)
                })

        return jsonify({
            "status": "OK",
            "temp_dir": UPLOAD_DIR,
            "files": temp_files
        })
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "error": str(e)
        }), 500

# ===== نقطة الدخول للتشغيل المحلي =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting local development server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)