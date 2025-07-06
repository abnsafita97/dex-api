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

# ===== Advanced System Setup =====
def setup_logger():
    """Configure advanced logging system"""
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Unified formatter for all handlers
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(process)d - %(message)s')
    
    # Error log file handler
    error_handler = logging.FileHandler("server_errors.log")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    
    # Console info handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

# Initialize Flask app
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=100 * 1024 * 1024,  # 100MB
    TEMP_FILE_TIMEOUT=300,  # 5 minutes for temp files
    UPLOAD_DIR=tempfile.gettempdir()
)

# Tool paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APKTOOL_PATH = os.path.join(BASE_DIR, "apktool.jar")
MYAPP_SMALI_PATH = os.path.join(BASE_DIR, "MyApp.smali")
MYAPP_CLASS = "com.abnsafita.protection.MyApp"

# ===== Advanced Temp File Manager =====
class TempFileManager:
    """Centralized temp file management"""
    def __init__(self):
        self.active_jobs = {}
        self.lock = threading.Lock()
    
    def create_job_dir(self, prefix):
        """Create tracked temp directory"""
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(app.config['UPLOAD_DIR'], f"{prefix}_{job_id}")
        os.makedirs(job_dir, exist_ok=True)
        
        with self.lock:
            self.active_jobs[job_dir] = {
                "created": time.time(),
                "last_access": time.time(),
                "size": 0
            }
        
        logger.info(f"Created temp directory: {job_dir}")
        return job_dir
    
    def update_access(self, job_dir):
        """Update last access time"""
        with self.lock:
            if job_dir in self.active_jobs:
                self.active_jobs[job_dir]["last_access"] = time.time()
    
    def schedule_cleanup(self, job_dir, delay=None):
        """Schedule directory cleanup"""
        if delay is None:
            delay = app.config['TEMP_FILE_TIMEOUT']
        
        def cleanup():
            logger.info(f"⏳ Waiting {delay}s before cleaning {job_dir}")
            time.sleep(delay)
            try:
                if os.path.exists(job_dir):
                    # Calculate directory size
                    size = 0
                    for path, _, files in os.walk(job_dir):
                        for f in files:
                            fp = os.path.join(path, f)
                            size += os.path.getsize(fp)
                    
                    shutil.rmtree(job_dir, ignore_errors=True)
                    logger.info(f"🧹 Cleaned {job_dir} (Size: {size/(1024*1024):.2f} MB)")
                    
                    with self.lock:
                        if job_dir in self.active_jobs:
                            del self.active_jobs[job_dir]
            except Exception as e:
                logger.error(f"❌ Cleanup failed: {str(e)}")
        
        threading.Thread(target=cleanup, daemon=True).start()
    
    def cleanup_expired(self):
        """Clean up all expired temp files"""
        with self.lock:
            now = time.time()
            for job_dir, info in list(self.active_jobs.items()):
                if now - info["last_access"] > app.config['TEMP_FILE_TIMEOUT']:
                    self.schedule_cleanup(job_dir, delay=0)

# Initialize file manager
file_manager = TempFileManager()

# ===== Enhanced API Endpoints =====
@app.route("/")
def home():
    return "🛡️ APK Protection Server - Version 5.0 | Fixed Manifest Issues", 200

@app.before_request
def log_request():
    """Log incoming requests"""
    logger.info(f"📥 Incoming: {request.method} {request.url}")

@app.route("/upload", methods=["POST"])
def upload_apk():
    job_dir = None
    tmpdir = None
    
    try:
        # Validate APK file
        if 'apk' not in request.files:
            return jsonify(error="Missing 'apk' field"), 400
            
        apk_file = request.files['apk']
        if not apk_file.filename.lower().endswith('.apk'):
            return jsonify(error="File must be APK format"), 400

        # Create job directory
        job_dir = file_manager.create_job_dir("apkjob")
        apk_path = os.path.join(job_dir, "input.apk")
        apk_file.save(apk_path)
        logger.info(f"💾 Saved APK: {os.path.getsize(apk_path)/(1024*1024):.2f} MB")

        # Process APK with CORRECTED parameter names
        output_zip, tmpdir = process_apk(
            apk_path=apk_path,
            apktool_path=APKTOOL_PATH,
            smali_file_path=MYAPP_SMALI_PATH,
            app_class=MYAPP_CLASS
        )

        # Validate output
        if not os.path.exists(output_zip):
            raise FileNotFoundError("Output file creation failed")

        # Update access time
        file_manager.update_access(job_dir)
        if tmpdir:
            file_manager.update_access(tmpdir)

        # Send response
        response = send_file(
            output_zip,
            as_attachment=True,
            download_name="protected.zip",
            mimetype='application/zip'
        )

        # Schedule cleanup
        file_manager.schedule_cleanup(job_dir)
        if tmpdir:
            file_manager.schedule_cleanup(tmpdir)

        return response

    except Exception as e:
        # Immediate cleanup on error
        if job_dir:
            file_manager.schedule_cleanup(job_dir, delay=0)
        if tmpdir:
            file_manager.schedule_cleanup(tmpdir, delay=0)

        logger.exception("APK processing error")
        return jsonify(
            error=str(e), 
            traceback=traceback.format_exc()
        ), 500

@app.route("/assemble", methods=["POST"])
def assemble_smali():
    job_dir = None
    
    try:
        if 'smali' not in request.files:
            return jsonify(error="Missing 'smali' field"), 400

        # Create job directory
        job_dir = file_manager.create_job_dir("assemblejob")
        zip_path = os.path.join(job_dir, "smali.zip")
        request.files['smali'].save(zip_path)
        logger.info(f"💾 Saved Smali ZIP: {os.path.getsize(zip_path)/(1024*1024):.2f} MB")

        # Extract files
        smali_dir = os.path.join(job_dir, "smali")
        os.makedirs(smali_dir, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(smali_dir)

        # Assemble Smali to APK
        temp_apk_dir = os.path.join(job_dir, "temp_apk")
        os.makedirs(temp_apk_dir, exist_ok=True)
        shutil.move(smali_dir, os.path.join(temp_apk_dir, "smali"))

        temp_apk = os.path.join(job_dir, "temp.apk")
        result = subprocess.run(
            ["java", "-Xmx2G", "-jar", APKTOOL_PATH, "b", temp_apk_dir, "-o", temp_apk, "-f"],  # Increased memory
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            logger.error(f"❌ APK build failed: {result.stderr}")
            raise RuntimeError(f"APK assembly failed: {result.stderr}")

        # Extract DEX files
        dex_files = []
        with zipfile.ZipFile(temp_apk, 'r') as apk_zip:
            for file in apk_zip.namelist():
                if file.startswith("classes") and file.endswith(".dex"):
                    output_path = os.path.join(job_dir, file)
                    apk_zip.extract(file, job_dir)
                    dex_files.append(output_path)
                    logger.info(f"Extracted DEX: {file}")

        # Validate extraction
        if not dex_files:
            raise FileNotFoundError("No DEX files found in APK")

        # Create DEX package
        dex_zip = os.path.join(job_dir, "dex_files.zip")
        with zipfile.ZipFile(dex_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for dex in dex_files:
                zipf.write(dex, os.path.basename(dex))
        
        # Update access time
        file_manager.update_access(job_dir)

        response = send_file(
            dex_zip, 
            as_attachment=True, 
            download_name="dex_files.zip",
            mimetype='application/zip'
        )

        # Schedule cleanup
        file_manager.schedule_cleanup(job_dir)
        return response

    except Exception as e:
        if job_dir:
            file_manager.schedule_cleanup(job_dir, delay=0)
        logger.exception("Smali assembly error")
        return jsonify(
            error=str(e), 
            traceback=traceback.format_exc()
        ), 500

# ===== System Monitoring Endpoints =====
@app.route("/health", methods=["GET"])
def health_check():
    """Comprehensive system health check"""
    health_status = {
        "status": "OK",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "5.0",
        "components": {}
    }
    
    try:
        # Check essential files
        health_status["components"]["apktool"] = os.path.exists(APKTOOL_PATH)
        health_status["components"]["myapp_smali"] = os.path.exists(MYAPP_SMALI_PATH)
        
        # Check Java availability
        java_result = subprocess.run(
            ["java", "-version"],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=5
        )
        health_status["components"]["java"] = java_result.returncode == 0
        
        # Disk space check
        disk = psutil.disk_usage('/')
        health_status["disk"] = {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": disk.percent
        }
        
        # Temp files status
        with file_manager.lock:
            health_status["temp_files"] = {
                "active_jobs": len(file_manager.active_jobs),
                "total_size_mb": sum(
                    info["size"] / (1024*1024) 
                    for info in file_manager.active_jobs.values()
                )
            }
        
        # Check for failed components
        if not all(health_status["components"].values()):
            health_status["status"] = "WARNING"
            health_status["message"] = "Some components are missing"
        
    except Exception as e:
        health_status["status"] = "ERROR"
        health_status["error"] = str(e)
    
    return jsonify(health_status)

@app.route("/resources", methods=["GET"])
def resource_check():
    """System resource metrics"""
    try:
        # Memory metrics
        mem = psutil.virtual_memory()
        
        # Convert bytes to MB
        def to_mb(bytes_val):
            return round(bytes_val / (1024 * 1024), 2)
        
        return jsonify({
            "memory_mb": {
                "total": to_mb(mem.total),
                "available": to_mb(mem.available),
                "used": to_mb(mem.used),
                "free": to_mb(mem.free),
                "percent": mem.percent
            },
            "cpu_percent": psutil.cpu_percent(interval=1),
            "server_time": datetime.utcnow().isoformat(),
            "uptime_seconds": int(time.time() - psutil.boot_time())
        })
    except Exception as e:
        return jsonify(error=str(e)), 500

# ===== File Inspection Endpoint =====
@app.route("/inspect/<job_id>", methods=["GET"])
def inspect_job(job_id):
    """Inspect job files for debugging"""
    job_dir = os.path.join(app.config['UPLOAD_DIR'], f"apkjob_{job_id}")
    if not os.path.exists(job_dir):
        return jsonify(error="Job not found"), 404
    
    files = []
    for root, _, filenames in os.walk(job_dir):
        for f in filenames:
            fp = os.path.join(root, f)
            files.append({
                "path": fp,
                "size": os.path.getsize(fp),
                "modified": os.path.getmtime(fp)
            })
    
    return jsonify(files=files)

# ===== Background Services =====
def background_cleaner():
    """Background temp file cleanup service"""
    while True:
        try:
            file_manager.cleanup_expired()
            time.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Background cleaner error: {str(e)}")
        time.sleep(60)

# Start background services
threading.Thread(target=background_cleaner, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, threaded=True)