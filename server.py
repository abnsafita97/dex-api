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
import xml.etree.ElementTree as ET

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

# ===== دالة للعثور على النشاط الرئيسي =====
def find_main_activity(manifest_path):
    try:
        # تحليل ملف AndroidManifest.xml
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        
        # الحصول على اسم الحزمة
        package = root.get('package')
        
        # البحث عن النشاط الذي يحتوي على تصفية نية LAUNCHER
        for activity in root.iter('activity'):
            # اسم النشاط (قد يكون مطلقًا أو نسبيًا)
            activity_name = activity.get('{http://schemas.android.com/apk/res/android}name')
            if activity_name is None:
                continue
                
            # البحث عن تصفية النية
            intent_filters = activity.findall('intent-filter')
            for intent_filter in intent_filters:
                has_main_action = False
                has_launcher_category = False
                
                for action in intent_filter.findall('action'):
                    action_name = action.get('{http://schemas.android.com/apk/res/android}name')
                    if action_name == "android.intent.action.MAIN":
                        has_main_action = True
                
                for category in intent_filter.findall('category'):
                    category_name = category.get('{http://schemas.android.com/apk/res/android}name')
                    if category_name == "android.intent.category.LAUNCHER":
                        has_launcher_category = True
                
                if has_main_action and has_launcher_category:
                    # إذا كان اسم النشاط نسبيًا (يبدأ بنقطة) فإننا ندمجه مع اسم الحزمة
                    if activity_name.startswith('.'):
                        return package + activity_name
                    elif '.' not in activity_name:
                        return package + '.' + activity_name
                    return activity_name
        
        raise Exception("MainActivity not found in AndroidManifest.xml")
    except Exception as e:
        logger.error(f"Failed to find main activity: {str(e)}")
        raise

# ===== دالة لحقن الكود في ملف Smali =====
def inject_code(smali_file, invoke_line):
    with open(smali_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    modified = []
    in_oncreate = False
    injected = False
    
    # الإستراتيجية 1: الحقن بعد .prologue في onCreate
    for line in lines:
        modified.append(line)
        
        if not in_oncreate and line.strip().startswith('.method') and 'onCreate(' in line:
            in_oncreate = True
            continue
        
        if in_oncreate and not injected and line.strip() == '.prologue':
            modified.append(f'    {invoke_line}\n')
            injected = True
            in_oncreate = False
        
        if in_oncreate and line.strip().startswith('.end method'):
            in_oncreate = False
    
    # الإستراتيجية 2: الحقن بعد invoke-super
    if not injected:
        modified = lines.copy()
        for i, line in enumerate(modified):
            if 'invoke-super' in line and 'onCreate' in line:
                modified.insert(i + 1, f'    {invoke_line}\n')
                injected = True
                break
    
    # الإستراتيجية 3: الحقن قبل نهاية الدالة
    if not injected:
        modified = lines.copy()
        for i in range(len(modified)-1, -1, -1):
            if '.end method' in modified[i]:
                modified.insert(i, f'    {invoke_line}\n')
                injected = True
                break
    
    if not injected:
        raise Exception("Failed to inject code in onCreate")
    
    with open(smali_file, 'w', encoding='utf-8') as f:
        f.writelines(modified)

# ===== رفع وتفكيك APK وحقن الحماية =====
@app.route("/upload", methods=["POST"])
def upload_apk():
    try:
        logger.info("Upload request started")

        # البحث عن ملف ينتهي بـ .apk من أي حقل
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

        # استخراج AndroidManifest.xml للعثور على النشاط الرئيسي
        manifest_path = os.path.join(job_dir, "AndroidManifest.xml")
        with zipfile.ZipFile(apk_path, 'r') as zip_ref:
            if 'AndroidManifest.xml' in zip_ref.namelist():
                zip_ref.extract('AndroidManifest.xml', job_dir)
            else:
                return jsonify(error="AndroidManifest.xml not found in APK"), 400

        # العثور على النشاط الرئيسي
        main_activity = find_main_activity(manifest_path)
        logger.info(f"Main activity found: {main_activity}")

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

        # ===== بدء عملية الحقن =====
        # تحويل اسم النشاط الرئيسي إلى مسار ملف Smali
        smali_path = os.path.join(out_dir, main_activity.replace('.', '/') + ".smali")
        if not os.path.exists(smali_path):
            # البحث في جميع المجلدات الفرعية
            found = False
            for root, dirs, files in os.walk(out_dir):
                candidate = os.path.join(root, main_activity.replace('.', '/') + ".smali")
                if os.path.exists(candidate):
                    smali_path = candidate
                    found = True
                    break
            
            if not found:
                return jsonify(error=f"MainActivity smali not found: {smali_path}"), 400

        logger.info(f"Injecting protection code into: {smali_path}")
        inject_code(smali_path, "invoke-static {p0}, Lcom/abnsafita/protection/ProtectionManager;->init(Landroid/content/Context;)V")
        # ===== نهاية عملية الحقن =====

        # ===== تجميع Smali إلى DEX =====
        dex_output = os.path.join(job_dir, "classes.dex")
        result = subprocess.run(
            ["java", "-jar", SMALI_PATH, "a", out_dir, "-o", dex_output],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            return jsonify(error=f"DEX assembly failed: {result.stderr}"), 500

        # ===== إرسال ملف classes.dex =====
        response = send_file(
            dex_output,
            as_attachment=True,
            download_name="classes.dex",
            mimetype='application/octet-stream'
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