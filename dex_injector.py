import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
import tempfile
import logging

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

def rebuild_apk(src_dir, output_apk):
    """إعادة بناء APK من المجلد"""
    with zipfile.ZipFile(output_apk, 'w') as zipf:
        for root, _, files in os.walk(src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, src_dir)
                zipf.write(full_path, rel_path)

def decompile_dex(dex_path, out_dir, baksmali_path, dex_index=1):
    """تفكيك ملف DEX إلى Smali"""
    dex_out = os.path.join(out_dir, f"smali_classes{dex_index}" if dex_index > 1 else "smali")
    run_cmd(["java", "-jar", baksmali_path, "d", dex_path, "-o", dex_out])
    return dex_out

def recompile_dex(smali_dir, out_dex_path, smali_path):
    """تجميع Smali إلى DEX"""
    run_cmd(["java", "-jar", smali_path, "a", smali_dir, "-o", out_dex_path])

def insert_myapp(smali_dir, myapp_smali_path, myapp_class):
    """إضافة فئة التطبيق المخصصة"""
    dest_path = os.path.join(smali_dir, *myapp_class.split("/")) + ".smali"
    
    # إنشاء المجلد إذا لم يكن موجودًا
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    # نسخ ملف MyApp.smali
    shutil.copy(myapp_smali_path, dest_path)
    
    if not os.path.exists(dest_path):
        raise RuntimeError("Failed to copy MyApp.smali")
    
    logger.info("✅ MyApp.smali successfully added")

def modify_manifest(manifest_path):
    """تعديل AndroidManifest.xml"""
    ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    app_tag = root.find('application')

    if app_tag is None:
        raise RuntimeError("<application> tag not found in AndroidManifest.xml")

    # إضافة/تعديل سمة android:name
    app_tag.set('{http://schemas.android.com/apk/res/android}name', 'com.abnsafita.protection.MyApp')
    logger.info("✅ Manifest modified successfully")
    
    tree.write(manifest_path, encoding='utf-8', xml_declaration=True)

def process_apk(apk_path, baksmali_path, smali_path, myapp_smali_path, myapp_class):
    """معالجة APK الرئيسية"""
    with tempfile.TemporaryDirectory() as tmpdir:
        extract_dir = os.path.join(tmpdir, "apk")
        os.makedirs(extract_dir, exist_ok=True)
        extract_apk(apk_path, extract_dir)

        # تعديل AndroidManifest.xml
        manifest_path = os.path.join(extract_dir, "AndroidManifest.xml")
        modify_manifest(manifest_path)

        # معالجة ملفات DEX
        dex_files = sorted([f for f in os.listdir(extract_dir) if f.startswith("classes") and f.endswith(".dex")])
        if not dex_files:
            raise RuntimeError("No DEX files found in APK")

        # تفكيك جميع ملفات DEX وحقن MyApp في الأول فقط
        for i, dex_file in enumerate(dex_files):
            dex_path = os.path.join(extract_dir, dex_file)
            smali_out = decompile_dex(dex_path, tmpdir, baksmali_path, i + 1)
            
            # حقن MyApp فقط في ملف classes.dex الأول
            if i == 0:
                insert_myapp(smali_out, myapp_smali_path, myapp_class)

        # تجميع ملفات DEX
        for i, dex_file in enumerate(dex_files):
            smali_out = os.path.join(tmpdir, f"smali_classes{i+1}" if i>0 else "smali")
            new_dex_path = os.path.join(tmpdir, f"new_{dex_file}")
            recompile_dex(smali_out, new_dex_path, smali_path)
            shutil.move(new_dex_path, os.path.join(extract_dir, dex_file))

        # إنشاء حزمة الإرجاع (DEX + Manifest)
        output_zip = os.path.join(tmpdir, "protected.zip")
        with zipfile.ZipFile(output_zip, 'w') as zipf:
            # إضافة ملفات DEX
            for dex in dex_files:
                zipf.write(os.path.join(extract_dir, dex), dex)
            
            # إضافة AndroidManifest.xml المعدل
            zipf.write(manifest_path, "AndroidManifest.xml")
        
        return output_zip