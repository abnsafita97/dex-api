import os
import shutil
import tempfile
import zipfile
import subprocess
import xml.etree.ElementTree as ET

MYAPP_PATH = os.path.abspath("MyApp.smali")
MYAPP_CLASS = "com/abnsafita/protection/MyApp"

def run_cmd(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr.decode()}")
    return result.stdout.decode()

def extract_apk(apk_path, out_dir):
    with zipfile.ZipFile(apk_path, 'r') as zip_ref:
        zip_ref.extractall(out_dir)

def rebuild_apk(src_dir, output_apk):
    with zipfile.ZipFile(output_apk, 'w') as zipf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, src_dir)
                zipf.write(full_path, rel_path)

def decompile_dex(dex_path, out_dir, dex_index):
    dex_out = os.path.join(out_dir, f"smali_classes{dex_index}" if dex_index > 1 else "smali")
    run_cmd(f"baksmali d {dex_path} -o {dex_out}")
    return dex_out

def recompile_dex(smali_dir, out_dex_path):
    run_cmd(f"smali a {smali_dir} -o {out_dex_path}")

def insert_myapp(smali_dir):
    dest_path = os.path.join(smali_dir, *MYAPP_CLASS.split("/")) + ".smali"
    if os.path.exists(dest_path):
        print("MyApp.smali already exists in smali tree. Skipping copy.")
        return

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy(MYAPP_PATH, dest_path)

    if not os.path.exists(dest_path):
        raise RuntimeError("Failed to copy MyApp.smali")
    print("✅ MyApp.smali successfully copied.")

def modify_manifest(manifest_path):
    ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    app_tag = root.find('application')

    if app_tag is not None:
        app_tag.set('{http://schemas.android.com/apk/res/android}name', 'com.abnsafita.protection.MyApp')
        print("✅ Manifest modified.")
    else:
        raise RuntimeError("<application> tag not found in AndroidManifest.xml")

    tree.write(manifest_path, encoding='utf-8', xml_declaration=True)

def process_apk(apk_path):
    with tempfile.TemporaryDirectory() as tmpdir:
        extract_dir = os.path.join(tmpdir, "apk")
        os.makedirs(extract_dir, exist_ok=True)
        extract_apk(apk_path, extract_dir)

        dex_files = sorted([f for f in os.listdir(extract_dir) if f.startswith("classes") and f.endswith(".dex")])
        smali_dirs = []

        for i, dex_file in enumerate(dex_files):
            dex_path = os.path.join(extract_dir, dex_file)
            smali_out = decompile_dex(dex_path, tmpdir, i + 1)
            smali_dirs.append((smali_out, dex_file))

        # Inject MyApp into classes.dex only (index 0)
        insert_myapp(smali_dirs[0][0])

        # Recompile and replace all dex files
        for smali_out, dex_filename in smali_dirs:
            new_dex_path = os.path.join(tmpdir, f"new_{dex_filename}")
            recompile_dex(smali_out, new_dex_path)
            shutil.move(new_dex_path, os.path.join(extract_dir, dex_filename))

        # Modify Manifest
        manifest_path = os.path.join(extract_dir, "AndroidManifest.xml")
        modify_manifest(manifest_path)

        # Repack APK
        patched_apk_path = os.path.join(tmpdir, "patched.apk")
        rebuild_apk(extract_dir, patched_apk_path)

        return patched_apk_path