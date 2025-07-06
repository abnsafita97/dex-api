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
            elif "duplicate attribute" in error_output:
                logger.warning("Detected duplicate attribute error")
                raise RuntimeError("Duplicate attribute error - recovery attempted")
                
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
            
            # Ensure tools namespace is defined (check both nsmap and attrib)
            if 'tools' not in root.nsmap and 'xmlns:tools' not in root.attrib:
                root.set('xmlns:tools', 'http://schemas.android.com/tools')
                logger.info("Added missing tools namespace to public.xml")
            elif 'xmlns:tools' in root.attrib:
                logger.info("Tools namespace already present in public.xml")
            
            # Remove problematic elements
            for elem in root.findall(".//*[@type='c']"):
                root.remove(elem)
                
            # Add ignore attributes only if tools namespace exists
            for elem in root.findall(".//public"):
                if 'tools' in root.nsmap or 'xmlns:tools' in root.attrib:
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
    """Modify AndroidManifest.xml with enhanced error handling"""
    try:
        # Validate manifest
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"Missing file: {manifest_path}")
        
        # Backup original manifest
        backup_path = manifest_path + ".bak"
        shutil.copyfile(manifest_path, backup_path)
        logger.info(f"Created manifest backup: {backup_path}")
        
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
        
        # 1. Handle namespaces carefully
        ns_map = root.nsmap
        android_ns = 'http://schemas.android.com/apk/res/android'
        tools_ns = 'http://schemas.android.com/tools'
        
        # Check if tools namespace is already defined
        tools_prefix = None
        for prefix, uri in ns_map.items():
            if uri == tools_ns:
                tools_prefix = prefix
                break
        
        # Add tools namespace only if not present
        if not tools_prefix:
            if 'xmlns:tools' in root.attrib:
                logger.info("Tools namespace already defined in manifest attributes")
            else:
                root.attrib['xmlns:tools'] = tools_ns
                logger.info("Added tools namespace to manifest")
        else:
            logger.info(f"Tools namespace already present as '{tools_prefix}'")
        
        # 2. Find application tag with multiple strategies
        app_tag = None
        search_methods = [
            lambda: root.find('application'),
            lambda: root.find('application', {'android': android_ns}),
            lambda: next((elem for elem in root.iter() if 'application' in elem.tag), None)
        ]
        
        for method in search_methods:
            app_tag = method()
            if app_tag:
                break
        
        if app_tag is None:
            logger.error("Application tag not found in manifest. Creating one.")
            # Create application tag if missing
            app_tag = ET.Element('application')
            root.append(app_tag)
            logger.warning("Created new application tag in manifest")
        
        # 3. Set custom application class
        android_name = f'{{{android_ns}}}name'
        current_class = app_tag.get(android_name)
        
        if current_class:
            logger.info(f"Replacing existing application class: {current_class}")
        else:
            logger.info("No existing application class found")
        
        app_tag.set(android_name, app_class)
        
        # 4. Add tools attribute only if tools namespace is present
        tools_ignore = f'{{{tools_ns}}}ignore'
        if tools_prefix or 'xmlns:tools' in root.attrib:
            if tools_ignore in app_tag.attrib:
                logger.info("tools:ignore attribute already exists")
            else:
                app_tag.set(tools_ignore, 'HardcodedDebugMode')
        else:
            logger.warning("Skipping tools:ignore - namespace not defined")
        
        # 5. Save modifications
        tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
        logger.info("Manifest modifications saved")
        
        # 6. Validate after modification
        if not validate_xml(manifest_path):
            logger.error("Manifest XML invalid after modification. Restoring backup.")
            shutil.copyfile(backup_path, manifest_path)
            logger.info("Restored original manifest from backup")
            
            # Attempt minimal modification without tools namespace
            tree = ET.parse(manifest_path, parser=parser)
            root = tree.getroot()
            app_tag = None
            for method in search_methods:
                app_tag = method()
                if app_tag:
                    break
            
            if app_tag:
                app_tag.set(android_name, app_class)
                tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
                logger.info("Applied minimal manifest modification")
            else:
                logger.error("Failed to find application tag in backup manifest")
                return False
            
        logger.info("✅ Manifest modified successfully")
        return True
    except ET.ParseError as e:
        logger.error(f"❌ XML parse error: {str(e)}")
        # Try to restore backup
        if os.path.exists(backup_path):
            shutil.copyfile(backup_path, manifest_path)
            logger.info("Restored manifest from backup after parse error")
        return False
    except Exception as e:
        logger.error(f"❌ Manifest modification failed: {str(e)}")
        # Try to restore backup
        if os.path.exists(backup_path):
            shutil.copyfile(backup_path, manifest_path)
            logger.info("Restored manifest from backup after general error")
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
            "java", "-Xmx2G", "-jar", apktool_path, "d",  # Increased memory
            "--use-aapt2",  # Use modern resource compiler
            "--force",      # Force overwrite
            apk_path,
            "-o", decode_dir
        ]
        run_command(decode_cmd, timeout=600)
        
        # Step 2: Fix resource issues
        fix_resource_issues(decode_dir)
        
        # Step 3: Modify manifest with detailed logging
        manifest_path = os.path.join(decode_dir, "AndroidManifest.xml")
        manifest_success = modify_manifest(manifest_path, app_class)
        
        if not manifest_success:
            # Log manifest content for debugging
            try:
                with open(manifest_path, 'r') as f:
                    content = f.read()
                logger.error(f"Manifest content after failed modification:\n{content}")
            except Exception as e:
                logger.error(f"Failed to read manifest: {str(e)}")
            
            # Try to use the backup manifest
            backup_path = manifest_path + ".bak"
            if os.path.exists(backup_path):
                logger.warning("Attempting to use backup manifest")
                shutil.copyfile(backup_path, manifest_path)
                
                # Try minimal modification (only application class)
                tree = ET.parse(manifest_path)
                root = tree.getroot()
                
                # Find application tag
                app_tag = None
                search_methods = [
                    lambda: root.find('application'),
                    lambda: root.find('application', {'android': 'http://schemas.android.com/apk/res/android'}),
                    lambda: next((elem for elem in root.iter() if 'application' in elem.tag), None)
                ]
                
                for method in search_methods:
                    app_tag = method()
                    if app_tag:
                        break
                
                if app_tag:
                    app_tag.set('{http://schemas.android.com/apk/res/android}name', app_class)
                    tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
                    logger.info("Applied minimal manifest modification")
                else:
                    raise RuntimeError("Application tag not found in backup manifest")
            else:
                raise RuntimeError("Manifest modification failed and no backup available")
        
        # Step 4: Inject application class
        if not inject_application(decode_dir, smali_file_path, app_class):
            raise RuntimeError("Application injection failed")
        
        # Step 5: Rebuild APK with aapt2
        output_apk = os.path.join(tmpdir, "protected.apk")
        logger.info(f"🔧 Rebuilding APK to: {output_apk}")
        
        build_cmd = [
            "java", "-Xmx2G", "-jar", apktool_path, "b",  # Increased memory
            decode_dir, 
            "-o", output_apk,
            "--use-aapt2"  # Ensure using modern resource compiler
        ]
        
        # Attempt build with recovery mechanism
        try:
            run_command(build_cmd, timeout=600)
        except RuntimeError as e:
            if "XML namespace error" in str(e) or "Duplicate attribute error" in str(e):
                logger.warning("Resource error detected, attempting recovery")
                
                # Remove potentially problematic files
                public_xml = os.path.join(decode_dir, "res", "values", "public.xml")
                manifest_path = os.path.join(decode_dir, "AndroidManifest.xml")
                
                if os.path.exists(public_xml):
                    logger.info(f"Removing potentially problematic file: {public_xml}")
                    os.remove(public_xml)
                    
                # Remove duplicate tools namespace from manifest
                try:
                    tree = ET.parse(manifest_path)
                    root = tree.getroot()
                    
                    # Remove duplicate tools namespace if exists
                    if root.attrib.get('xmlns:tools') and 'xmlns:tools' in root.attrib:
                        del root.attrib['xmlns:tools']
                    
                    tree.write(manifest_path, encoding='utf-8', xml_declaration=True)
                    logger.info("Cleaned duplicate tools namespace from manifest")
                except Exception as manifest_fix_error:
                    logger.error(f"Failed to fix manifest: {str(manifest_fix_error)}")
                
                # Retry build
                run_command(build_cmd, timeout=600)
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