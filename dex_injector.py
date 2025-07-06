import os
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
import tempfile
import logging
import time
from xml.dom import minidom

logger = logging.getLogger(__name__)

# ===== Advanced Command Execution =====
def run_command(cmd, cwd=None, timeout=300):
    """Execute command with robust error handling"""
    try:
        logger.debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            cwd=cwd,
            timeout=timeout
        )
        
        if result.returncode != 0:
            error_output = result.stderr.decode().strip()
            logger.error(f"Command failed ({result.returncode}): {error_output}")
            
            # Special handling for resource errors
            if "unbound prefix" in error_output:
                logger.warning("Detected XML namespace error, attempting recovery")
                raise RuntimeError("XML namespace error - recovery attempted")
                
            raise RuntimeError(f"Command error: {error_output}")
        
        return result.stdout.decode()
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout exceeded for command: {' '.join(cmd)}")
        raise RuntimeError("Process timeout")
    except Exception as e:
        logger.error(f"Unexpected execution error: {str(e)}")
        raise

# ===== XML Validation =====
def validate_xml(xml_path):
    """Validate XML file structure"""
    try:
        minidom.parse(xml_path)
        return True
    except Exception as e:
        logger.error(f"Invalid XML structure: {str(e)}")
        return False

# ===== Resource Issue Fixer =====
def fix_resource_issues(decode_dir):
    """Fix common APK resource issues with robust error handling"""
    res_dir = os.path.join(decode_dir, "res")
    if not os.path.exists(res_dir):
        return

    # Fix public.xml issues
    public_xml = os.path.join(res_dir, "values", "public.xml")
    if os.path.exists(public_xml):
        try:
            # Validate XML first
            if not validate_xml(public_xml):
                logger.warning("Invalid XML detected, attempting repair")
                
            # Register namespaces
            ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
            ET.register_namespace('tools', "http://schemas.android.com/tools")
            
            parser = ET.XMLParser(encoding='utf-8')
            tree = ET.parse(public_xml, parser=parser)
            root = tree.getroot()
            
            # Ensure tools namespace is defined
            if 'tools' not in root.nsmap:
                root.set('xmlns:tools', 'http://schemas.android.com/tools')
                logger.info("Added missing tools namespace to public.xml")
            
            # Remove problematic elements
            for elem in root.findall(".//*[@type='c']"):
                root.remove(elem)
                
            # Add ignore attributes only if tools namespace exists
            for elem in root.findall(".//public"):
                if 'tools' in root.nsmap:
                    elem.set('tools:ignore', 'MissingTranslation')
                else:
                    logger.warning("Skipping tools:ignore - namespace not defined")
            
            # Save with proper XML declaration
            tree.write(public_xml, encoding='utf-8', xml_declaration=True)
            logger.info("Fixed public.xml")
            
            # Re-validate after fix
            if not validate_xml(public_xml):
                logger.error("XML still invalid after fix, removing tools attributes")
                for elem in root.findall(".//public"):
                    if 'tools:ignore' in elem.attrib:
                        del elem.attrib['tools:ignore']
                tree.write(public_xml, encoding='utf-8', xml_declaration=True)
            
            logger.info("Successfully fixed public.xml")
            
        except Exception as e:
            logger.error(f"Critical error fixing public.xml: {str(e)}")
            logger.warning("Attempting to remove problematic file")
            try:
                # Fallback: remove tools attributes
                tree = ET.parse(public_xml)
                root = tree.getroot()
                for elem in root.findall(".//public"):
                    if 'tools:ignore' in elem.attrib:
                        del elem.attrib['tools:ignore']
                tree.write(public_xml, encoding='utf-8', xml_declaration=True)
                logger.info("Removed tools attributes from public.xml")
            except:
                # Final fallback: remove file completely
                try:
                    os.remove(public_xml)
                    logger.warning("Deleted problematic public.xml file")
                except Exception as e_remove:
                    logger.error(f"Failed to delete public.xml: {str(e_remove)}")

# ===== Smali Injection =====
def inject_application(decode_dir, smali_file_path, app_class):
    """Inject custom application class"""
    try:
        # Find all smali directories
        smali_dirs = [
            os.path.join(decode_dir, d) 
            for d in os.listdir(decode_dir) 
            if d.startswith("smali")
        ]
        
        if not smali_dirs:
            raise RuntimeError("No smali directories found")
        
        # Convert class to path
        class_path = app_class.replace(".", "/")
        if class_path.endswith(".smali"):
            class_path = class_path[:-6]
        
        # Prepare target directory
        class_parts = class_path.split("/")
        class_name = class_parts[-1]
        relative_path = "/".join(class_parts[:-1])
        
        # Inject into first smali directory (can be extended to multi-dex)
        target_dir = os.path.join(smali_dirs[0], relative_path)
        os.makedirs(target_dir, exist_ok=True)
        target_file = os.path.join(target_dir, f"{class_name}.smali")
        
        # Copy application file
        shutil.copy(smali_file_path, target_file)
        
        if not os.path.exists(target_file):
            raise RuntimeError("File copy failed")
        
        logger.info(f"✅ Injected application to: {target_file}")
        return True
    except Exception as e:
        logger.error(f"❌ Injection failed: {str(e)}")
        return False

# ===== Manifest Modification =====
def modify_manifest(manifest_path, app_class):
    """Modify AndroidManifest.xml with namespace validation"""
    try:
        # Validate manifest
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"Missing file: {manifest_path}")
        
        # Validate XML structure
        if not validate_xml(manifest_path):
            logger.warning("Manifest XML is invalid, attempting repair")
        
        # Register namespaces
        ET.register_namespace('android', "http://schemas.android.com/apk/res/android")
        ET.register_namespace('tools', "http://schemas.android.com/tools")
        
        # Parse XML
        parser = ET.XMLParser(encoding='utf-8')
        tree = ET.parse(manifest_path, parser=parser)
        root = tree.getroot()
        
        # Ensure tools namespace is defined
        if 'xmlns:tools' not in root.attrib:
            root.attrib['xmlns:tools'] = 'http://schemas.android.com/tools'
            logger.info("Added missing tools namespace to manifest")
        
        # Find application tag
        namespaces = {'android': 'http://schemas.android.com/apk/res/android'}
        app_tag = root.find('application', namespaces=namespaces)
        
        if app_tag is None:
            # Alternative search
            for elem in root.iter():
                if 'application' in elem.tag:
                    app_tag = elem
                    break
            if app_tag is None:
                raise RuntimeError("Application tag not found")
        
        # Set custom application class
        app_tag.set('{http://schemas.android.com/apk/res/android}name', app_class)
        
        # Add tools attributes
        app_tag.set('{http://schemas.android.com/tools}ignore', 'HardcodedDebugMode')
        
        # Save modifications
        tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
        
        # Re-validate after modification
        if not validate_xml(manifest_path):
            logger.error("Manifest XML still invalid after modification")
            # Remove problematic tools attribute if needed
            if 'tools:ignore' in app_tag.attrib:
                del app_tag.attrib['{http://schemas.android.com/tools}ignore']
            tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
        
        logger.info("✅ Manifest modified successfully")
        return True
    except ET.ParseError as e:
        logger.error(f"❌ XML parse error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Manifest modification failed: {str(e)}")
        return False

# ===== APK Processing Pipeline =====
def process_apk(apk_path, apktool_path, smali_file_path, app_class):
    """Main APK processing workflow with enhanced error recovery"""
    # Create temp workspace
    tmpdir = tempfile.mkdtemp()
    logger.info(f"📁 Temp workspace: {tmpdir}")
    
    try:
        # Step 1: Decode APK
        decode_dir = os.path.join(tmpdir, "decoded")
        logger.info(f"🔧 Decoding APK to: {decode_dir}")
        
        decode_cmd = [
            "java", "-jar", apktool_path, "d",
            "--use-aapt2",  # Use modern resource compiler
            "--force",      # Force overwrite
            apk_path,
            "-o", decode_dir
        ]
        run_command(decode_cmd, timeout=600)
        
        # Step 2: Fix resource issues
        fix_resource_issues(decode_dir)
        
        # Step 3: Modify manifest
        manifest_path = os.path.join(decode_dir, "AndroidManifest.xml")
        if not modify_manifest(manifest_path, app_class):
            raise RuntimeError("Manifest modification failed")
        
        # Step 4: Inject application class
        if not inject_application(decode_dir, smali_file_path, app_class):
            raise RuntimeError("Application injection failed")
        
        # Step 5: Rebuild APK with aapt2
        output_apk = os.path.join(tmpdir, "protected.apk")
        logger.info(f"🔧 Rebuilding APK to: {output_apk}")
        
        build_cmd = [
            "java", "-jar", apktool_path, "b", 
            decode_dir, 
            "-o", output_apk,
            "--use-aapt2"  # Ensure using modern resource compiler
        ]
        
        # Attempt build with recovery mechanism
        try:
            run_command(build_cmd, timeout=600)
        except RuntimeError as e:
            if "XML namespace error" in str(e):
                logger.warning("Resource error detected, attempting recovery")
                
                # Remove potentially problematic public.xml
                public_xml = os.path.join(decode_dir, "res", "values", "public.xml")
                if os.path.exists(public_xml):
                    logger.info(f"Removing problematic file: {public_xml}")
                    os.remove(public_xml)
                    
                    # Retry build
                    run_command(build_cmd, timeout=600)
                else:
                    raise
            else:
                raise
        
        # Step 6: Create output package
        output_zip = os.path.join(tmpdir, "protected.zip")
        logger.info(f"📦 Creating output package: {output_zip}")
        
        with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            with zipfile.ZipFile(output_apk, 'r') as apk_zip:
                # Add all DEX files
                for file in apk_zip.namelist():
                    if file.startswith("classes") and file.endswith(".dex"):
                        zipf.writestr(file, apk_zip.read(file))
                        logger.debug(f"Added: {file}")
                
                # Add manifest
                if "AndroidManifest.xml" in apk_zip.namelist():
                    zipf.writestr("AndroidManifest.xml", apk_zip.read("AndroidManifest.xml"))
        
        # Validate output
        if not os.path.exists(output_zip):
            raise RuntimeError("Output ZIP creation failed")
        
        size_mb = os.path.getsize(output_zip) / (1024 * 1024)
        logger.info(f"✅ Created output: {output_zip} ({size_mb:.2f} MB)")
        return output_zip, tmpdir
        
    except Exception as e:
        # Cleanup on failure
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception as cleanup_err:
            logger.error(f"Cleanup error: {str(cleanup_err)}")
        
        logger.exception("❌ Critical APK processing failure")
        raise