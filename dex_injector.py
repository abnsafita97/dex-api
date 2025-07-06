import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
import tempfile
import logging
import re

logger = logging.getLogger(__name__)

def run_cmd(cmd, cwd=None):
    """تنفيذ أمر في سطر الأوامر"""
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr.decode()}")
    return result.stdout.decode()

def extract_apk(apk_path, out_dir):
    """استخراج محتويات APK"""
    with zipfile.ZipFile(apk_path, 'r') as zip_ref:
        zip_ref.extractall(out_dir)

def insert_myapp(decode_dir, myapp_smali_path, myapp_class):
    """إضافة فئة التطبيق المخصصة إلى مجلد smali المناسب"""
    try:
        # تحديد مجلد smali الرئيسي (classes.dex)
        smali_dir = os.path.join(decode_dir, "smali")
        
        # إذا لم يكن موجود، نبحث عن أول مجلد smali
        if not os.path.exists(smali_dir):
            for dir_name in os.listdir(decode_dir):
                if dir_name.startswith("smali"):
                    smali_dir = os.path.join(decode_dir, dir_name)
                    break
            else:
                raise RuntimeError("No smali folder found in decoded APK")
        
        # إنشاء مسار الوجهة
        app_dir = os.path.join(smali_dir, *myapp_class.split("."))
        dest_path = os.path.join(app_dir, "MyApp.smali")
        
        # إنشاء المجلدات إذا لزم الأمر
        os.makedirs(app_dir, exist_ok=True)
        
        # نسخ ملف MyApp.smali
        shutil.copy(myapp_smali_path, dest_path)
        
        if not os.path.exists(dest_path):
            raise RuntimeError("Failed to copy MyApp.smali")
        
        logger.info(f"✅ MyApp.smali successfully added to {dest_path}")
        return True
    except Exception as e:
        logger.error(f"Error adding custom application: {str(e)}")
        return False

def modify_manifest(manifest_path):
    """تعديل AndroidManifest.xml (نصي)"""
    try:
        # سجل محاولة فتح الملف
        logger.info(f"Attempting to parse manifest: {manifest_path}")
        
        # تحقق من وجود الملف
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")
        
        # سجل حجم الملف
        logger.info(f"Manifest file size: {os.path.getsize(manifest_path)} bytes")
        
        # سجل محتوى الملف (الجزء الأول فقط)
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                first_lines = ''.join([next(f) for _ in range(5)])
                logger.debug(f"First lines of manifest:\n{first_lines}")
        except Exception as e:
            logger.warning(f"Could not read manifest content: {str(e)}")
        
        # تحليل XML
        ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        
        # البحث عن وسم التطبيق
        app_tag = root.find('application')
        if app_tag is None:
            # البحث في جميع العناصر إذا لم يكن في الموقع المتوقع
            for elem in root.iter():
                if elem.tag == 'application':
                    app_tag = elem
                    break
            if app_tag is None:
                raise RuntimeError("<application> tag not found in AndroidManifest.xml")
        
        # إضافة/تعديل سمة android:name
        app_tag.set('{http://schemas.android.com/apk/res/android}name', 'com.abnsafita.protection.MyApp')
        logger.info("✅ Manifest modified successfully")
        
        # حفظ التعديلات
        tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
        return True
    except Exception as e:
        logger.error(f"Error modifying manifest: {str(e)}")
        return False

def process_apk(apk_path, apktool_path, myapp_smali_path, myapp_class):
    """معالجة APK الرئيسية باستخدام apktool فقط"""
    try:
        # إنشاء مجلد مؤقت
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. تفكيك APK باستخدام apktool
            decode_dir = os.path.join(tmpdir, "decoded")
            logger.info(f"Decoding APK with apktool to: {decode_dir}")
            run_cmd(["java", "-jar", apktool_path, "d", apk_path, "-o", decode_dir, "-f"])
            
            # 2. تعديل AndroidManifest.xml
            manifest_path = os.path.join(decode_dir, "AndroidManifest.xml")
            if not modify_manifest(manifest_path):
                raise RuntimeError("Failed to modify AndroidManifest.xml")
            
            # 3. إضافة MyApp.smali إلى مجلد smali المناسب
            if not insert_myapp(decode_dir, myapp_smali_path, myapp_class):
                raise RuntimeError("Failed to add custom application class")
            
            # 4. إعادة تجميع APK
            output_apk = os.path.join(tmpdir, "protected.apk")
            logger.info(f"Rebuilding APK with apktool to: {output_apk}")
            run_cmd(["java", "-jar", apktool_path, "b", decode_dir, "-o", output_apk, "-f"])
            
            # 5. إنشاء حزمة الإخراج (DEX + Manifest)
            output_zip = os.path.join(tmpdir, "protected.zip")
            with zipfile.ZipFile(output_zip, 'w') as zipf:
                # استخراج ملفات DEX وManifest من APK المعدل
                with zipfile.ZipFile(output_apk, 'r') as apk_zip:
                    for file in apk_zip.namelist():
                        if file.startswith("classes") and file.endswith(".dex") or file == "AndroidManifest.xml":
                            # استخراج الملف مؤقتاً
                            apk_zip.extract(file, tmpdir)
                            extracted_path = os.path.join(tmpdir, file)
                            
                            # إضافة الملف إلى ZIP النهائي
                            zipf.write(extracted_path, file)
                            logger.info(f"Added {file} to output zip")
            
            return output_zip
    except Exception as e:
        logger.error(f"Error in process_apk: {str(e)}")
        raise

def extract_files_from_apk(apk_path, output_dir):
    """استخراج ملفات DEX وManifest من APK"""
    with zipfile.ZipFile(apk_path, 'r') as zip_ref:
        # إنشاء مجلد الإخراج
        os.makedirs(output_dir, exist_ok=True)
        
        # استخراج ملفات DEX وManifest
        for file in zip_ref.namelist():
            if file.startswith("classes") and file.endswith(".dex") or file == "AndroidManifest.xml":
                zip_ref.extract(file, output_dir)
                logger.info(f"Extracted {file} to {output_dir}")
        
        # التحقق من وجود ملفات
        extracted_files = os.listdir(output_dir)
        if not any(f.startswith("classes") for f in extracted_files):
            raise RuntimeError("No DEX files extracted from APK")
        if "AndroidManifest.xml" not in extracted_files:
            raise RuntimeError("AndroidManifest.xml not found in APK")
        
        return output_dir