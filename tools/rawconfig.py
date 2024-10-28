#!/usr/bin/env python3
# 
# This script reads and modifies the user-specific selection of the partition 
# scheme preferences for ESP32 boards in the Arduino IDE. It interacts with the 
# local storage LevelDB used by Eclipse Theia-based IDE's to change the default 
# partitioning scheme settings according to user's Sketch custom partitions.csv
#
# Dependencies: depends on Python module 'plyvel' for interacting with LevelDB
#     github.com/wbolster/plyvel or
#     pypi.org/project/plyvel-wheels
#     www.microsoft.com/en-us/download/details.aspx?id=48145
#
# Version: 0.0.1 (pre-alpha)
# Powered by ChatGPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import urllib.request
import subprocess
import importlib
import platform
import tempfile
import zipfile
import ctypes
#import plyvel # from import_module()
import shutil
import json
import sys
import os
import re



# function check for python package
def package_installed(package_name):
    try:
        __import__(package_name)
        return True
    except ImportError:
        return False


# function install package using pip
def install_package(package_name):
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--user', package_name, '--no-warn-script-location'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print(f"Error: module installation failed. Please install '{package_name}' manually.")
        sys.exit(1)


# function download pip if not installed
def install_pip():
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except subprocess.CalledProcessError:
        pass
    try:
        url = "https://bootstrap.pypa.io/get-pip.py"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.close()
            urllib.request.urlretrieve(url, temp_file.name)
            subprocess.check_call([sys.executable, temp_file.name, '--user', '--no-warn-script-location'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(temp_file.name)
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print("Error: pip installation failed. Please install 'python3-pip' manually.")
            sys.exit(1)
    except Exception:
        print(f"Error: pip installation failed. Please install 'python3-pip' manually.")
        sys.exit(1)


# function import python module if not installed
def import_module(package_name):
    module_name = package_name.split('-')[0]
    if package_installed(module_name):
        globals()[module_name] = importlib.import_module(module_name)
    else:
        install_pip()
        install_package(package_name)
        # add user-site directory to path
        user_site = subprocess.check_output([sys.executable, "-m", "site", "--user-site"], text=True).strip()
        if user_site not in sys.path:
            sys.path.append(user_site)
        try:
            importlib.invalidate_caches()
            globals()[module_name] = importlib.import_module(module_name)
        except ImportError:
            print(f"Error: module '{package_name}' is not installed properly.")
            sys.exit(1)


# function download sysinternals handle.exe
def get_sysinternals():
    url = "https://download.sysinternals.com/files/Handle.zip"
    temp_dir = os.path.join(os.environ.get('TEMP', tempfile.gettempdir()), 'arduino-ide2-sysinternals')
    os.makedirs(temp_dir, exist_ok=True)
    handle_zip = os.path.join(temp_dir, "Handle.zip")
    handle_exe = os.path.join(temp_dir, "handle.exe")
    if os.path.exists(handle_exe):
        return handle_exe
    urllib.request.urlretrieve(url, handle_zip)
    if os.path.exists(handle_zip):
        with zipfile.ZipFile(handle_zip, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
    if os.path.exists(handle_exe):
        return handle_exe
    else:
        return None


# function close database blocking file handles (Arduino IDE.exe)
def close_db_handles(db_path):
    command = [handle_exe, db_path, "-v", "-nobanner"]
    result = subprocess.run(command, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    for line in lines[1:]:
        if line.strip():
            parts = line.split(',')
            if len(parts) >= 5:
                pid = parts[1].strip()
                handle_id = parts[3].strip()
                close_command = [handle_exe, "-c", handle_id, "-p", pid, "-y"]
                subprocess.run(close_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# function check dependencies
def check_dependencies():
    os_type = platform.system()
    if os_type == "Linux":
        import_module('plyvel')
    elif os_type == "Windows":
        dll_file = os.path.join(os.environ['SystemRoot'], 'System32', 'msvcp140.dll')
        if os.path.exists(dll_file):
            import_module('plyvel-wheels')
        else:
            print("Error: unsolved dependency: MSVCP140.dll: Please install Visual C++ Redistributable for Visual Studio 2015.")
            sys.exit(1)
        handle_exe = get_sysinternals()
    elif os_type == "Darwin":
        import_module('plyvel-wheels')


# function dump whole database to stdout
def print_table(db_path):
    print(f"{'Key':<40} | {'Value':<40}")
    print("="*85)
    for key, value in db:
        print(f"{key!r:<40} | {value!r:<40}")


# function extract sketch directory path from database key
def get_sketch(db, board):
    prefix = b'_file://' + b'\x00' + b'\x01' + f'theia:'.encode('utf-8')
    sketch_paths = []
    for key, value in db.iterator(prefix=prefix):
        key_str = key.decode('utf-8')
        if f".arduinoIDE-configOptions-{board}" in key_str:
            start = key_str.find('theia:') + len('theia:')
            end = key_str.find(f":.arduinoIDE-configOptions-{board}")
            if start != -1 and end != -1 and start < end:
                sketch = key_str[start:end]
                sketch_paths.append(sketch)
    if sketch_paths:
        return sketch_paths
    return []


# function read whole key-value pair and extract JSON struct from value string
def read_key(db, key):
    byte_string = db.get(key)
    if byte_string:
        decoded_string = byte_string.decode('utf-8')
        start_index = decoded_string.find('{')
        end_index = decoded_string.rfind('}')
        if start_index != -1 and end_index != -1:
            try:
                return json.loads(decoded_string[start_index:end_index + 1])
            except Exception as e:
                print(f"Error parsing JSON: {e}")
                return {}
    return {}


# function write whole key-value pair into database
def write_key(db, key, value):
    try:
        db.put(key, value)
    except Exception as e:
        print(f"Error writing to the database: {e}")


# function read values from PartitionScheme
def read_value(db, key, json_property, json_option, json_object):
    data = read_key(db, key)
    if data:
        properties = data.get(json_property)
        if properties:
            options = [item for item in properties if item.get("option") == json_option]
            if options:
                array_found = False
                array = []
                for item in options:
                    values = item.get("values", [])
                    for value in values:
                        if value.get("value") == json_object:
                            array.append(value)
                            array_found = True
                if array_found:
                    return array
    return []


# function read all Partition Schemes from configOptions
def read_object(db, key, json_property, json_option):
    data = read_key(db, key)
    if data:
        properties = data.get(json_property)
        if properties:
            options = [item for item in properties if item.get("option") == json_option]
            if options:
                return options
    return []


# function read configOptions from .arduinoIDE-configOptions
def read_property(db, key, json_property):
    data = read_key(db, key)
    if data:
        properties = data.get(json_property)
        if properties:
            return properties
    return []


# function read .arduinoIDE-configOptions for board id
def read_configOptions(db, key):
    data = read_key(db, key)
    if data:
        return data
    return {}


# function set new Partition Scheme
def write_value(db, key, json_property, json_option, json_object):

    # read value json_object from database
    json_object_write = read_value(db, key, json_property, json_option, json_object)

    # set selected true
    if json_object_write:
        for item in json_object_write:
            if item.get("value") == json_object:
                item["selected"] = True

        # read object json_option from database
        json_option_write = read_object(db, key, json_property, json_option)
        if json_option_write:
            # iterate through the values inside the object
            for item in json_option_write:
                if item.get("option") == json_option:
                    for value_item in item["values"]:
                        value_item["selected"] = False
                    # insert json_object into json_option
                    for value_item in item["values"]:
                        if value_item.get("value") == json_object:
                            if isinstance(json_object_write[0], dict):
                                value_item.update(json_object_write[0])
                                not_written = False
                            else:
                                not_written = True
                            break
                        else:
                            not_written = True
                    if not_written:
                        print(f"Error: {json_option} '{json_object}' not written")
                        sys.exit(1)

            # read property json_property from database
            json_property_write = read_property(db, key, json_property)
            if json_property_write:
                # find and update the specific option
                for item in json_property_write:
                    if item.get("option") == json_option:
                        # insert json_option into json_property
                        item.update(json_option_write[0])
                        not_written = False
                        break
                    else:
                        not_written = True
                if not_written:
                    print(f"Error: {json_property} '{json_option}' not written")
                    sys.exit(1)

                # read configOptions from database
                json_configOptions_write = read_configOptions(db, key)
                if json_configOptions_write:
                    # insert json_property into configOptions
                    if json_property in json_configOptions_write:
                        json_configOptions_write[json_property] = json_property_write
                    else:
                        print(f"Error: '{json_property}' not written")
                        sys.exit(1)

                    # write the key-value pair to the database
                    encoded_value = b'\x01' + json.dumps(json_configOptions_write).encode('utf-8')
                    write_key(db, key, encoded_value)


# function update database
def update_database(mode, db, key, json_property, json_option, json_object):

    # print the key-value pairs depending on the number of calling arguments
    if mode.lower() in ['r', 'read']:
        if len(sys.argv) > 6:
            print(json.dumps(read_value(db, key, json_property, json_option, json_object), indent=2))
        elif len(sys.argv) > 5:
            print(json.dumps(read_object(db, key, json_property, json_option), indent=2))
        elif len(sys.argv) > 4:
            print(json.dumps(read_property(db, key, json_property), indent=2))
        elif len(sys.argv) > 3:
            print(json.dumps(read_configOptions(db, key), indent=2))
        else:
            print_table(db_path)

    # set new Partition Scheme and write back the key-value pair in database
    elif mode.lower() in ['w', 'write']:
        if len(sys.argv) > 6:
            write_value(db, key, json_property, json_option, json_object)



# main: usage: script leveldb [r|w] esp32:esp32:esp32 configOptions PartitionScheme custom BlinkRGB
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: script leveldb [r|w] id property object value [sketch]\n")
        os_type = platform.system()
        if os_type == "Linux":
            print(f'python3 {os.path.basename(sys.argv[0])} ~/.config/arduino-ide/Local\\ Storage/leveldb w esp32:esp32:esp32 configOptions PartitionScheme custom')
        elif os_type == "Windows":
            print(f'python.exe {os.path.basename(sys.argv[0])} "%UserProfile%\\AppData\\Roaming\\arduino-ide\\Local Storage\\leveldb" w esp32:esp32:esp32 configOptions PartitionScheme custom')
        elif os_type == "Darwin":
            print(f'python3 {os.path.basename(sys.argv[0])} ~/Library/Application\\ Support/arduino-ide/Local\\ Storage/leveldb w esp32:esp32:esp32 configOptions PartitionScheme custom')
        sys.exit(1)

    # install python package
    check_dependencies()

    # ensure the provided path is a directory and exists
    db_path       = sys.argv[1]
    mode          = sys.argv[2]
    if not os.path.isdir(db_path):
        print(f"Error: {db_path} is not a LevelDB database")
        sys.exit(1)

    # close database blocking file handles (Arduino IDE.exe)
    if platform.system() == "Windows":
        if ctypes.windll.shell32.IsUserAnAdmin():
            handle_exe = get_sysinternals()
            if handle_exe:
                close_db_handles(db_path)

    # open the LevelDB database
    try:
        db = plyvel.DB(db_path, create_if_missing=False)
    except Exception as e:
        print(f"Error opening LevelDB: {e}")
        sys.exit(1)

    # construct the LevelDB database key
    if len(sys.argv) > 3:
        board         = sys.argv[3]
        sketch_paths  = get_sketch(db, board)
        if sketch_paths:
            all_keys = [
                b'_file://' + b'\x00' + b'\x01' + f'theia:{sketch}:.arduinoIDE-configOptions-{board}'.encode('utf-8')
                for sketch in sketch_paths
            ]
        else:
            all_keys = [
                b'_file://' + b'\x00' + b'\x01' + f'theia:.arduinoIDE-configOptions-{board}'.encode('utf-8')
            ]
    if len(sys.argv) > 4:
        json_property = sys.argv[4]
    if len(sys.argv) > 5:
        json_option   = sys.argv[5]
    if len(sys.argv) > 6:
        json_object   = sys.argv[6]
    if len(sys.argv) > 7:
        filter_sketch = sys.argv[7]

    # set new Partition Scheme and write back the key-value pair in database
    try:
        if filter_sketch and all_keys:
            # update database sketch specific if sketch name is given
            matching_keys = []
            for key in all_keys:
                key_str = key.decode('utf-8')
                prefix = "_file://\x00\x01theia"
                suffix = f".arduinoIDE-configOptions-{board}"
                if key_str.startswith(prefix) and key_str.endswith(suffix):
                    start_idx = len(prefix)
                    end_idx = key_str.find(suffix)
                    sketch_path = key_str[start_idx:end_idx]
                    sketch_path = sketch_path.strip(":")
                    pattern = rf"^file:///.*?/{filter_sketch}$"
                    if re.search(pattern, sketch_path):
                        matching_keys.append(key)
            if matching_keys:
                for key in matching_keys:
                    update_database(mode, db, key, json_property, json_option, json_object)
    except NameError:
        if 'all_keys' in locals():
            for key in all_keys:
                # update database board specific if no sketch is given
                update_database(mode, db, key, json_property if 'json_property' in locals() else None, json_option if 'json_option' in locals() else None, json_object if 'json_object' in locals() else None)
        else:
            # dump whole database to stdout if no board is given
            update_database(mode, db, None, None, None, None)

    # close the database
    db.close()
