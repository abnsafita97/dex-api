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
def delayed_cleanup(directory, delay=120):
    """تنظيف مجلد بعد تأخير طويل (120 ثانية)"""
    def cleanup():
        logger.info(f"الانتظار {delay} ثانية قبل التنظيف: {directory}")
        time.sleep(delay)
        try:
            if os.path.exists(directory):
                shutil.rmtree(directory, ignore_errors=True)
                logger.info(f"تم التنظيف: {directory}")
        except Exception as e:
            logger.error(f"فشل التنظيف: {str(e)}")
    threading.Thread(target=cleanup, daemon=True).start()

# ===== نقاط النهاية =====
@app.route("/")
def home():
    return "خادم حماية APK - الإصدار 3.0", 200

@app.route("/upload", methods=["POST"])
def upload_apk():
    job_dir = None
    tmpdir = None
    try:
        # البحث عن ملف APK
        apk_file = next((f for f in request.files.values() if f.filename.lower().endswith(".apk")), None)
        if not apk_file:
            return jsonify(error="لم يتم العثور على ملف APK"), 400

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

        # التحقق من وجود الملف قبل الإرسال
        if not os.path.exists(output_zip):
            logger.error(f"ملف الإخراج غير موجود: {output_zip}")
            raise FileNotFoundError("فشل إنشاء ملف الإخراج")

        # إرسال الملف
        response = send_file(
            output_zip,
            as_attachment=True,
            download_name="protected.zip",
            mimetype='application/zip'
        )
        
        # جدولة التنظيف بعد وقت كافٍ
        if tmpdir: 
            delayed_cleanup(tmpdir, delay=120)
        if job_dir: 
            delayed_cleanup(job_dir, delay=120)
            
        return response

    except Exception as e:
        # تنظيف فوري في حالة الخطأ
        if tmpdir: 
            shutil.rmtree(tmpdir, ignore_errors=True)
        if job_dir: 
            shutil.rmtree(job_dir, ignore_errors=True)
            
        logger.exception("خطأ في تحميل APK")
        return jsonify(error=str(e)), 500

@app.route("/assemble", methods=["POST"])
def assemble_smali():
    job_dir = None
    try:
        if 'smali' not in request.files:
            return jsonify(error="الحقل 'smali' مطلوب"), 400

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

        # التحقق من وجود ملف DEX قبل الإرسال
        if not os.path.exists(dex_output):
            raise FileNotFoundError("فشل إنشاء ملف classes.dex")

        response = send_file(
            dex_output, 
            as_attachment=True, 
            download_name="classes.dex"
        )
        
        # جدولة التنظيف
        delayed_cleanup(job_dir, delay=120)
        return response

    except Exception as e:
        if job_dir: 
            shutil.rmtree(job_dir, ignore_errors=True)
        logger.exception("خطأ في تجميع Smali")
        return jsonify(error=str(e)), 500

# ===== نقاط فحص النظام =====
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify(
        status="OK", 
        time=datetime.utcnow().isoformat(),
        version="3.0"
    )

@app.route("/javacheck", methods=["GET"])
def java_check():
    try:
        result = subprocess.run(
            ["java", "-version"], 
            stderr=subprocess.PIPE, 
            text=True,
            timeout=10
        )
        return jsonify(status="OK", version=result.stderr.strip())
    except Exception as e:
        return jsonify(status="ERROR", error=str(e)), 500

@app.route("/resources", methods=["GET"])
def resource_check():
    """فحص موارد النظام بالميغابايت (MB)"""
    try:
        # تحويل البايت إلى ميغابايت
        def bytes_to_mb(bytes_value):
            return round(bytes_value / (1024 * 1024), 2)
        
        # الحصول على معلومات الذاكرة
        mem = psutil.virtual_memory()
        
        # الحصول على معلومات الذاكرة التبادلية (Swap)
        swap = psutil.swap_memory()
        
        # الحصول على معلومات القرص
        disk = psutil.disk_usage('/')
        
        # الحصول على استخدام المعالج
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # الحصول على متوسط الحمل
        load_avg = os.getloadavg() if hasattr(os, 'getloadavg') else None
        
        # الحصول على معلومات العمليات
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'memory_info', 'cpu_percent']):
            try:
                processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "memory_mb": bytes_to_mb(proc.info['memory_info'].rss),
                    "memory_percent": round(proc.info['memory_percent'], 2),
                    "cpu_percent": round(proc.info['cpu_percent'], 2)
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        # فرز العمليات حسب استخدام الذاكرة
        processes_sorted = sorted(processes, key=lambda x: x['memory_mb'], reverse=True)[:10]
        
        return jsonify({
            "status": "OK",
            "memory_mb": {
                "total": bytes_to_mb(mem.total),
                "available": bytes_to_mb(mem.available),
                "used": bytes_to_mb(mem.used),
                "free": bytes_to_mb(mem.free),
                "percent": mem.percent
            },
            "swap_mb": {
                "total": bytes_to_mb(swap.total),
                "used": bytes_to_mb(swap.used),
                "free": bytes_to_mb(swap.free),
                "percent": swap.percent
            },
            "disk_mb": {
                "total": bytes_to_mb(disk.total),
                "used": bytes_to_mb(disk.used),
                "free": bytes_to_mb(disk.free),
                "percent": disk.percent
            },
            "cpu": {
                "percent": cpu_percent,
                "cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True)
            },
            "load_average": load_avg,
            "top_processes": processes_sorted,
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "server_time": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)