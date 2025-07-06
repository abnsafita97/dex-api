import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
import tempfile
import logging
import time

logger = logging.getLogger(__name__)

def run_cmd(cmd, cwd=None, timeout=60):
    """تنفيذ أمر في سطر الأوامر مع مهلة زمنية"""
    try:
        result = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            cwd=cwd,
            timeout=timeout
        )
        if result.returncode != 0:
            raise RuntimeError(f"فشل الأمر: {' '.join(cmd)}\n{result.stderr.decode()}")
        return result.stdout.decode()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"انتهت المهلة الزمنية للأمر: {' '.join(cmd)}")

def insert_myapp(decode_dir, myapp_smali_path, myapp_class):
    """إضافة فئة التطبيق المخصصة إلى مجلد smali المناسب"""
    try:
        # تحديد مجلد smali الرئيسي
        smali_dir = os.path.join(decode_dir, "smali")
        if not os.path.exists(smali_dir):
            for dir_name in os.listdir(decode_dir):
                if dir_name.startswith("smali"):
                    smali_dir = os.path.join(decode_dir, dir_name)
                    break
            else:
                raise RuntimeError("لم يتم العثور على مجلد smali في APK المفكك")
        
        # تحويل اسم الفئة إلى مسار
        class_path = myapp_class.replace(".", "/")
        if class_path.endswith(".smali"):
            class_path = class_path[:-6]
        
        # إنشاء مسار الوجهة
        app_dir = os.path.join(smali_dir, os.path.dirname(class_path))
        class_name = os.path.basename(class_path)
        dest_path = os.path.join(app_dir, f"{class_name}.smali")
        
        # إنشاء المجلدات ونسخ الملف
        os.makedirs(app_dir, exist_ok=True)
        shutil.copy(myapp_smali_path, dest_path)
        
        logger.info(f"✅ تمت إضافة MyApp.smali إلى {dest_path}")
        return True
    except Exception as e:
        logger.error(f"خطأ في إضافة الفئة المخصصة: {str(e)}")
        return False

def modify_manifest(manifest_path):
    """تعديل AndroidManifest.xml"""
    try:
        # التحقق من وجود الملف
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"الملف غير موجود: {manifest_path}")
        
        # تحليل XML
        ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        
        # البحث عن وسم التطبيق
        app_tag = None
        for elem in root.iter():
            if elem.tag == 'application':
                app_tag = elem
                break
        if app_tag is None:
            raise RuntimeError("لم يتم العثور على وسم <application> في AndroidManifest.xml")
        
        # إضافة/تعديل سمة android:name
        app_tag.set('{http://schemas.android.com/apk/res/android}name', 'com.abnsafita.protection.MyApp')
        
        # حفظ التعديلات
        tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
        logger.info("✅ تم تعديل المانيفست بنجاح")
        return True
    except Exception as e:
        logger.error(f"خطأ في تعديل المانيفست: {str(e)}")
        return False

def process_apk(apk_path, apktool_path, myapp_smali_path, myapp_class):
    """معالجة APK الرئيسية"""
    # إنشاء مجلد مؤقت يدويًا
    tmpdir = tempfile.mkdtemp()
    try:
        # 1. تفكيك APK
        decode_dir = os.path.join(tmpdir, "decoded")
        logger.info(f"تفكيك APK إلى: {decode_dir}")
        run_cmd(["java", "-jar", apktool_path, "d", apk_path, "-o", decode_dir, "-f"], timeout=300)
        
        # 2. تعديل AndroidManifest.xml
        manifest_path = os.path.join(decode_dir, "AndroidManifest.xml")
        if not modify_manifest(manifest_path):
            raise RuntimeError("فشل تعديل المانيفست")
        
        # 3. إضافة MyApp.smali
        if not insert_myapp(decode_dir, myapp_smali_path, myapp_class):
            raise RuntimeError("فشل إضافة الفئة المخصصة")
        
        # 4. إعادة تجميع APK
        output_apk = os.path.join(tmpdir, "protected.apk")
        logger.info(f"إعادة تجميع APK إلى: {output_apk}")
        run_cmd(["java", "-jar", apktool_path, "b", decode_dir, "-o", output_apk, "-f"], timeout=300)
        
        # 5. إنشاء حزمة الإخراج (DEX + Manifest)
        output_zip = os.path.join(tmpdir, "protected.zip")
        with zipfile.ZipFile(output_zip, 'w') as zipf:
            with zipfile.ZipFile(output_apk, 'r') as apk_zip:
                for file in apk_zip.namelist():
                    if (file.startswith("classes") and file.endswith(".dex")) or file == "AndroidManifest.xml":
                        extracted_path = os.path.join(tmpdir, file)
                        apk_zip.extract(file, tmpdir)
                        zipf.write(extracted_path, file)
                        logger.info(f"تمت إضافة {file} إلى الإخراج")
        
        # التحقق من إنشاء الملف بنجاح
        if not os.path.exists(output_zip):
            raise RuntimeError("فشل إنشاء ملف ZIP الناتج")
        
        logger.info(f"تم إنشاء ملف الإخراج: {output_zip} ({os.path.getsize(output_zip)} بايت)")
        return output_zip, tmpdir
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        logger.error(f"خطأ في process_apk: {str(e)}")
        raise