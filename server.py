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
import subprocess
import zipfile
from dex_injector import process_apk

# ===== إعداد النظام =====
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
UPLOAD_DIR = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

# مسارات الأدوات
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APKTOOL_PATH = os.path.join(BASE_DIR, "apktool.jar")
MYAPP_SMALI_PATH = os.path.join(BASE_DIR, "MyApp.smali")
MYAPP_CLASS = "com.abnsafita.protection.MyApp"

# ===== وظائف المساعدة =====
def delayed_cleanup(directory, delay=5):
    """تنظيف مجلد بعد تأخير"""
    def cleanup():
        time.sleep(delay)
        try:
            if os.path.exists(directory):
                shutil.rmtree(directory, ignore_errors=True)
                logger.info(f"Cleaned: {directory}")
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")
    threading.Thread(target=cleanup, daemon=True).start()

# ===== نقاط النهاية =====
@app.route("/")
def home():
    return "APK Protection Server v2.0", 200

@app.route("/upload", methods=["POST"])
def upload_apk():
    job_dir = None
    tmpdir = None
    try:
        # البحث عن ملف APK
        apk_file = next((f for f in request.files.values() if f.filename.lower().endswith(".apk")), None)
        if not apk_file:
            return jsonify(error="No APK file found"), 400

        # إنشاء مجلد العمل
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"apkjob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        apk_path = os.path.join(job_dir, "input.apk")
        apk_file.save(apk_path)

        # معالجة APK
        output_zip, tmpdir = process_apk(
            apk_path=apk_path,
            apktool_path=APKTOOL_PATH,
            myapp_smali_path=MYAPP_SMALI_PATH,
            myapp_class=MYAPP_CLASS
        )

        # إرسال النتيجة
        response = send_file(
            output_zip,
            as_attachment=True,
            download_name="protected.zip",
            mimetype='application/zip'
        )
        
        # جدولة التنظيف
        if tmpdir: delayed_cleanup(tmpdir)
        if job_dir: delayed_cleanup(job_dir)
            
        return response

    except Exception as e:
        if tmpdir: shutil.rmtree(tmpdir, ignore_errors=True)
        if job_dir: shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("Upload error")
        return jsonify(error=str(e)), 500

@app.route("/assemble", methods=["POST"])
def assemble_smali():
    job_dir = None
    try:
        if 'smali' not in request.files:
            return jsonify(error="'smali' field required"), 400

        # إنشاء مجلد العمل
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"assemblejob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        
        # حفظ ملف ZIP
        zip_path = os.path.join(job_dir, "smali.zip")
        request.files['smali'].save(zip_path)
        
        # استخراج الملفات
        smali_dir = os.path.join(job_dir, "smali")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(smali_dir)

        # تجميع Smali إلى DEX
        temp_apk_dir = os.path.join(job_dir, "temp_apk")
        os.makedirs(temp_apk_dir, exist_ok=True)
        shutil.move(smali_dir, os.path.join(temp_apk_dir, "smali"))
        
        temp_apk = os.path.join(job_dir, "temp.apk")
        subprocess.run(
            ["java", "-jar", APKTOOL_PATH, "b", temp_apk_dir, "-o", temp_apk, "-f"],
            check=True
        )
        
        # استخراج DEX
        dex_output = os.path.join(job_dir, "classes.dex")
        with zipfile.ZipFile(temp_apk, 'r') as apk_zip:
            for file in apk_zip.namelist():
                if file.startswith("classes") and file.endswith(".dex"):
                    apk_zip.extract(file, job_dir)
                    os.rename(os.path.join(job_dir, file), dex_output)
                    break

        response = send_file(
            dex_output, 
            as_attachment=True, 
            download_name="classes.dex"
        )
        delayed_cleanup(job_dir)
        return response

    except Exception as e:
        if job_dir: shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("Assemble error")
        return jsonify(error=str(e)), 500

# ===== نقاط فحص النظام =====
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(status="OK", time=datetime.utcnow().isoformat())

@app.route("/javacheck", methods=["GET"])
def java_check():
    try:
        result = subprocess.run(["java", "-version"], stderr=subprocess.PIPE, text=True)
        return jsonify(status="OK", version=result.stderr.strip())
    except Exception as e:
        return jsonify(status="ERROR", error=str(e)), 500

@app.route("/resources", methods=["GET"])
def resource_check():
    try:
        return jsonify(
            memory=dict(psutil.virtual_memory()._asdict()),
            disk=dict(psutil.disk_usage('/')._asdict())
        )
    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)