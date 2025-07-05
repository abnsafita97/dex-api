import os
import sys
import subprocess
import argparse
import logging
import xml.etree.ElementTree as ET

# إعداد نظام التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# مسارات الأدوات (يمكن تعديلها)
DEFAULT_BAKSMALI_PATH = "/usr/local/bin/baksmali.jar"
DEFAULT_SMALI_PATH = "/usr/local/bin/smali.jar"

def find_main_activity(manifest_path):
    """استخراج اسم النشاط الرئيسي من AndroidManifest.xml"""
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

        return None
    except Exception as e:
        logger.error(f"Failed to parse manifest: {str(e)}")
        return None

def inject_code_into_smali(smali_file_path, invoke_line):
    """حقن كود الحماية في ملف Smali"""
    try:
        with open(smali_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        modified = []
        in_oncreate = False
        injected = False

        # الإستراتيجية 1: الحقن بعد .prologue في onCreate
        for line in lines:
            modified.append(line)

            # الدخول إلى دالة onCreate
            if not in_oncreate and line.strip().startswith('.method') and 'onCreate(' in line:
                in_oncreate = True
                continue

            # الحقن بعد .prologue
            if in_oncreate and not injected and line.strip() == '.prologue':
                modified.append(f'    {invoke_line}\n')
                injected = True
                in_oncreate = False

            # الخروج من الدالة
            if in_oncreate and line.strip().startswith('.end method'):
                in_oncreate = False

        # الإستراتيجية 2: الحقن بعد استدعاء invoke-super في onCreate
        if not injected:
            modified = lines.copy()
            for i, line in enumerate(modified):
                if 'invoke-super' in line and 'onCreate' in line:
                    modified.insert(i+1, f'    {invoke_line}\n')
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

        # كتابة الملف المعدل
        with open(smali_file_path, 'w', encoding='utf-8') as f:
            f.writelines(modified)

        return True
    except Exception as e:
        logger.error(f"Injection failed: {str(e)}")
        return False

def process_dex(dex_path, output_dex_path, main_activity_fqn, baksmali_path=None, smali_path=None):
    """معالجة ملف DEX: تفكيك، حقن، إعادة تجميع"""
    # استخدام المسارات الافتراضية إذا لم يتم توفيرها
    if baksmali_path is None:
        baksmali_path = DEFAULT_BAKSMALI_PATH
    if smali_path is None:
        smali_path = DEFAULT_SMALI_PATH

    # إنشاء مجلد عمل مؤقت
    work_dir = os.path.dirname(dex_path)
    smali_dir = os.path.join(work_dir, "smali_out")
    os.makedirs(smali_dir, exist_ok=True)

    try:
        # 1. تفكيك DEX إلى Smali
        logger.info(f"Disassembling DEX: {dex_path}")
        result = subprocess.run(
            ["java", "-jar", baksmali_path, "d", dex_path, "-o", smali_dir],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            logger.error(f"Baksmali error: {result.stderr}")
            return False

        # 2. تحويل FQN إلى مسار ملف Smالي
        smali_file_path = os.path.join(smali_dir, main_activity_fqn.replace('.', '/') + ".smali")
        if not os.path.exists(smali_file_path):
            logger.error(f"Smali file not found: {smali_file_path}")
            return False

        # 3. حقن الكود
        invoke_line = "invoke-static {p0}, Lcom/abnsafita/protection/ProtectionManager;->init(Landroid/content/Context;)V"
        logger.info(f"Injecting code into: {smali_file_path}")
        if not inject_code_into_smali(smali_file_path, invoke_line):
            return False

        # 4. إعادة تجميع Smالي إلى DEX
        logger.info("Assembling Smali to DEX")
        result = subprocess.run(
            ["java", "-jar", smali_path, "a", smali_dir, "-o", output_dex_path],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            logger.error(f"Smali assembly error: {result.stderr}")
            return False

        return True
    except Exception as e:
        logger.exception("Processing failed")
        return False
    finally:
        # تنظيف مجلد Smali (اختياري)
        shutil.rmtree(smali_dir, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description='Inject protection code into DEX')
    parser.add_argument('--dex', required=True, help='Input DEX file path')
    parser.add_argument('--output', required=True, help='Output DEX file path')
    parser.add_argument('--main-activity', required=True, help='Fully qualified name of main activity')
    parser.add_argument('--baksmali', help='Path to baksmali.jar')
    parser.add_argument('--smali', help='Path to smali.jar')

    args = parser.parse_args()

    success = process_dex(
        dex_path=args.dex,
        output_dex_path=args.output,
        main_activity_fqn=args.main_activity,
        baksmali_path=args.baksmali,
        smali_path=args.smali
    )

    if success:
        logger.info("Injection completed successfully")
        sys.exit(0)
    else:
        logger.error("Injection failed")
        sys.exit(1)

if __name__ == "__main__":
    main()