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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("server.log", encoding="utf-8")
    ]
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
        logger.info(f"⏳ الانتظار {delay} ثانية قبل التنظيف: {directory}")
        time.sleep(delay)
        try:
            if os.path.exists(directory):
                logger.info(f"🧹 بدء تنظيف: {directory}")
                shutil.rmtree(directory, ignore_errors=True)
                logger.info(f"✅ تم تنظيف: {directory}")
        except Exception as e:
            logger.error(f"❌ فشل التنظيف: {str(e)}")
    threading.Thread(target=cleanup, daemon=True).start()

def log_system_status():
    """تسجيل حالة النظام للمساعدة في التشخيص"""
    try:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu = psutil.cpu_percent()
        load = os.getloadavg()
        
        logger.info(f"📊 حالة النظام: "
                    f"الذاكرة: {mem.percent}%, "
                    f"القرص: {disk.percent}%, "
                    f"المعالج: {cpu}%, "
                    f"الحِمل: {load}")
    except Exception as e:
        logger.warning(f"⚠️ فشل تسجيل حالة النظام: {str(e)}")

# ===== نقاط النهاية =====
@app.route("/")
def home():
    return "🛡️ خادم حماية APK - الإصدار 3.0 | تم التحديث لحل مشكلة الملفات المؤقتة", 200

@app.before_request
def before_request_logging():
    """تسجيل معلومات الطلب الوارد"""
    logger.info(f"📥 طلب وارد: {request.method} {request.url}")
    logger.debug(f"🔍 رؤوس الطلب: {dict(request.headers)}")
    if request.files:
        logger.info(f"📎 الملفات المرفقة: {list(request.files.keys())}")

@app.after_request
def after_request_logging(response):
    """تسجيل معلومات الاستجابة الصادرة"""
    logger.info(f"📤 استجابة صادرة: {response.status}")
    return response

@app.route("/upload", methods=["POST"])
def upload_apk():
    job_dir = None
    tmpdir = None
    start_time = time.time()
    
    try:
        logger.info("🚀 بدء معالجة طلب تحميل APK")
        log_system_status()
        
        # البحث عن ملف APK
        apk_file = None
        for field_name, file in request.files.items():
            if file and file.filename.lower().endswith(".apk"):
                apk_file = file
                logger.info(f"🔍 تم العثور على ملف APK في الحقل: {field_name}")
                break

        if not apk_file:
            logger.warning("⚠️ لم يتم العثور على ملف APK في الطلب")
            return jsonify(error="لم يتم العثور على ملف APK. الرجاء التأكد من إرسال ملف بصيغة .apk"), 400

        # إنشاء مجلد العمل
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"apkjob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        logger.info(f"📁 تم إنشاء مجلد العمل: {job_dir}")
        
        apk_path = os.path.join(job_dir, "input.apk")
        apk_file.save(apk_path)
        logger.info(f"💾 تم حفظ APK في: {apk_path} ({os.path.getsize(apk_path)} بايت)")

        # معالجة APK
        logger.info("⚙️ بدء معالجة APK...")
        output_zip, tmpdir = process_apk(
            apk_path=apk_path,
            apktool_path=APKTOOL_PATH,
            myapp_smali_path=MYAPP_SMALI_PATH,
            myapp_class=MYAPP_CLASS
        )

        # التحقق من وجود الملف قبل الإرسال
        if not os.path.exists(output_zip):
            logger.error(f"❌ ملف الإخراج غير موجود: {output_zip}")
            raise FileNotFoundError("فشل إنشاء ملف الإخراج. الرجاء التحقق من السجلات")

        logger.info(f"✅ تم إنشاء ملف الإخراج: {output_zip} ({os.path.getsize(output_zip)} بايت)")

        # إرسال الملف
        response = send_file(
            output_zip,
            as_attachment=True,
            download_name="protected.zip",
            mimetype='application/zip'
        )
        
        # جدولة التنظيف بعد وقت كافٍ
        if tmpdir: 
            logger.info(f"⏳ جدولة تنظيف المجلد المؤقت: {tmpdir}")
            delayed_cleanup(tmpdir, delay=120)
        if job_dir: 
            logger.info(f"⏳ جدولة تنظيف مجلد العمل: {job_dir}")
            delayed_cleanup(job_dir, delay=120)
        
        duration = time.time() - start_time
        logger.info(f"🎉 اكتملت المعالجة بنجاح في {duration:.2f} ثانية")
        return response

    except Exception as e:
        logger.error(f"❌ خطأ في معالجة APK: {str(e)}")
        logger.error(traceback.format_exc())
        
        # تنظيف فوري في حالة الخطأ
        if tmpdir: 
            logger.warning(f"🧹 تنظيف فوري للمجلد المؤقت: {tmpdir}")
            shutil.rmtree(tmpdir, ignore_errors=True)
        if job_dir: 
            logger.warning(f"🧹 تنظيف فوري لمجلد العمل: {job_dir}")
            shutil.rmtree(job_dir, ignore_errors=True)
            
        return jsonify(
            error="فشل معالجة APK",
            message=str(e),
            traceback=traceback.format_exc()
        ), 500

@app.route("/assemble", methods=["POST"])
def assemble_smali():
    job_dir = None
    start_time = time.time()
    
    try:
        logger.info("🚀 بدء معالجة طلب تجميع Smali")
        log_system_status()
        
        if 'smali' not in request.files:
            logger.warning("⚠️ الحقل 'smali' مفقود في الطلب")
            return jsonify(error="الحقل 'smali' مطلوب. الرجاء إرسال ملف ZIP يحتوي على ملفات Smali"), 400

        # إنشاء مجلد العمل
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(UPLOAD_DIR, f"assemblejob_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        logger.info(f"📁 تم إنشاء مجلد العمل: {job_dir}")
        
        # حفظ ملف ZIP
        zip_path = os.path.join(job_dir, "smali.zip")
        request.files['smali'].save(zip_path)
        logger.info(f"💾 تم حفظ ملف Smali ZIP: {zip_path} ({os.path.getsize(zip_path)} بايت)")
        
        # استخراج الملفات
        smali_dir = os.path.join(job_dir, "smali")
        os.makedirs(smali_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(smali_dir)
        
        logger.info(f"📂 تم استخراج ملفات Smali إلى: {smali_dir}")

        # تجميع Smali إلى DEX
        temp_apk_dir = os.path.join(job_dir, "temp_apk")
        os.makedirs(temp_apk_dir, exist_ok=True)
        shutil.move(smali_dir, os.path.join(temp_apk_dir, "smali"))
        logger.info(f"🔨 بدء تجميع Smali إلى APK...")
        
        temp_apk = os.path.join(job_dir, "temp.apk")
        result = subprocess.run(
            ["java", "-jar", APKTOOL_PATH, "b", temp_apk_dir, "-o", temp_apk, "-f"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode != 0:
            logger.error(f"❌ فشل تجميع APK: {result.stderr}")
            raise RuntimeError(f"فشل تجميع APK: {result.stderr}")
        
        logger.info(f"✅ تم تجميع APK بنجاح: {temp_apk} ({os.path.getsize(temp_apk)} بايت)")
        
        # استخراج DEX
        dex_output = os.path.join(job_dir, "classes.dex")
        found_dex = False
        
        with zipfile.ZipFile(temp_apk, 'r') as apk_zip:
            for file in apk_zip.namelist():
                if file.startswith("classes") and file.endswith(".dex"):
                    extracted_path = os.path.join(job_dir, file)
                    apk_zip.extract(file, job_dir)
                    
                    # إذا كان اسم الملف ليس classes.dex، نقوم بتغيير الاسم
                    if file != "classes.dex":
                        os.rename(extracted_path, dex_output)
                    else:
                        dex_output = extracted_path
                    
                    found_dex = True
                    logger.info(f"🔧 تم استخراج ملف DEX: {file} -> {dex_output}")
                    break
        
        if not found_dex:
            raise FileNotFoundError("لم يتم العثور على ملف classes.dex في APK المجمع")

        # التحقق من وجود ملف DEX قبل الإرسال
        if not os.path.exists(dex_output):
            raise FileNotFoundError("فشل إنشاء ملف classes.dex")

        logger.info(f"✅ جاهز للإرسال: {dex_output} ({os.path.getsize(dex_output)} بايت)")
        
        response = send_file(
            dex_output, 
            as_attachment=True, 
            download_name="classes.dex",
            mimetype='application/octet-stream'
        )
        
        # جدولة التنظيف
        logger.info(f"⏳ جدولة تنظيف مجلد العمل: {job_dir}")
        delayed_cleanup(job_dir, delay=120)
        
        duration = time.time() - start_time
        logger.info(f"🎉 اكتمل التجميع بنجاح في {duration:.2f} ثانية")
        return response

    except Exception as e:
        logger.error(f"❌ خطأ في تجميع Smali: {str(e)}")
        logger.error(traceback.format_exc())
        
        if job_dir: 
            logger.warning(f"🧹 تنظيف فوري لمجلد العمل: {job_dir}")
            shutil.rmtree(job_dir, ignore_errors=True)
            
        return jsonify(
            error="فشل تجميع Smali",
            message=str(e),
            traceback=traceback.format_exc()
        ), 500

# ===== نقاط فحص النظام =====
@app.route("/health", methods=["GET"])
def health_check():
    """فحص صحة الخادم"""
    try:
        # فحص وجود الملفات الأساسية
        essential_files = {
            "apktool.jar": os.path.exists(APKTOOL_PATH),
            "MyApp.smali": os.path.exists(MYAPP_SMALI_PATH)
        }
        
        # فحص توفر جافا
        java_ok = False
        try:
            subprocess.run(["java", "-version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            java_ok = True
        except Exception:
            pass
            
        return jsonify(
            status="OK" if all(essential_files.values()) and java_ok else "WARNING",
            time=datetime.utcnow().isoformat(),
            version="3.0",
            essential_files=essential_files,
            java_available=java_ok,
            system_load=os.getloadavg(),
            uptime=time.time() - psutil.boot_time()
        )
    except Exception as e:
        return jsonify(
            status="ERROR",
            error=str(e),
            traceback=traceback.format_exc()
        ), 500

@app.route("/javacheck", methods=["GET"])
def java_check():
    """فحص إصدار جافا"""
    try:
        result = subprocess.run(
            ["java", "-version"], 
            stderr=subprocess.PIPE, 
            stdout=subprocess.PIPE,
            text=True,
            timeout=10
        )
        output = result.stderr or result.stdout
        return jsonify(status="OK", version=output.strip())
    except subprocess.TimeoutExpired:
        return jsonify(status="ERROR", error="انتهت المهلة أثناء فحص جافا"), 500
    except Exception as e:
        return jsonify(status="ERROR", error=str(e)), 500

@app.route("/resources", methods=["GET"])
def resource_check():
    """فحص موارد النظام"""
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage('/')
        cpu = psutil.cpu_percent(interval=1)
        load = os.getloadavg()
        
        return jsonify(
            status="OK",
            memory={
                "total": mem.total,
                "available": mem.available,
                "used": mem.used,
                "percent": mem.percent
            },
            swap={
                "total": swap.total,
                "used": swap.used,
                "percent": swap.percent
            },
            disk={
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            },
            cpu_percent=cpu,
            load_average=list(load)
    except Exception as e:
        return jsonify(status="ERROR", error=str(e)), 500

@app.route("/tempfiles", methods=["GET"])
def list_temp_files():
    """سرد الملفات المؤقتة (لأغراض التصحيح)"""
    try:
        temp_files = []
        count = 0
        total_size = 0
        
        for f in os.listdir(UPLOAD_DIR):
            if f.startswith(("apkjob_", "assemblejob_")):
                path = os.path.join(UPLOAD_DIR, f)
                size = 0
                if os.path.isdir(path):
                    for root, dirs, files in os.walk(path):
                        for file in files:
                            fp = os.path.join(root, file)
                            size += os.path.getsize(fp) if os.path.exists(fp) else 0
                else:
                    size = os.path.getsize(path) if os.path.exists(path) else 0
                
                temp_files.append({
                    "name": f,
                    "path": path,
                    "is_dir": os.path.isdir(path),
                    "size": size,
                    "created": os.path.getctime(path),
                    "modified": os.path.getmtime(path)
                })
                
                count += 1
                total_size += size
        
        return jsonify(
            status="OK",
            count=count,
            total_size=total_size,
            files=temp_files
        )
    except Exception as e:
        return jsonify(status="ERROR", error=str(e)), 500

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🚀 بدء تشغيل خادم حماية APK")
    logger.info(f"📂 مجلد التحميل: {UPLOAD_DIR}")
    logger.info(f"🛠️ مسار apktool: {APKTOOL_PATH}")
    logger.info(f"🛠️ مسار MyApp.smali: {MYAPP_SMALI_PATH}")
    logger.info(f"🔍 فحص وجود الملفات: apktool.jar exists={os.path.exists(APKTOOL_PATH)}, MyApp.smali exists={os.path.exists(MYAPP_SMALI_PATH)}")
    log_system_status()
    logger.info("=" * 60)
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)