import os
import psutil
import GPUtil
import hashlib
import platform
import subprocess


def get_cpu_info():
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            cpu_name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            winreg.CloseKey(key)
            return cpu_name.strip()
        except:
            pass

    elif platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        except:
            pass

    elif platform.system() == "Darwin":  # macOS
        try:
            output = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"]).decode()
            return output.strip()
        except:
            pass

    return platform.processor() or "Unknown CPU"

def get_os_info():
    system = platform.system()
    if system == "Linux":
        try:
            distro_info = {}
            if os.path.exists('/etc/os-release'):
                with open('/etc/os-release') as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.rstrip().split('=', 1)
                            distro_info[key] = value.strip('"')

            if 'VERSION_ID' in distro_info:
                return f"Ubuntu {distro_info['VERSION_ID']}"
            else:
                return f"{platform.system()} {platform.release()}"
        except:
            return f"{platform.system()} {platform.release()}"
    elif system == "Windows":
        return f"Windows {platform.release()}"
    elif system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    else:
        return f"{system} {platform.release()}"

def get_mac_address():

    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == psutil.AF_LINK:
                return addr.address.upper().replace(":", "").replace("-", "")
    return None

def generate_serial():
    mac = get_mac_address()
    if not mac:
        return "UNKNOWN"

    mac_bytes = mac.encode()
    hash_value = hashlib.sha256(mac_bytes).hexdigest()
    serial = hash_value[:8].upper()
    return serial

def get_system_info():
    try:
        cpu = get_cpu_info()

        try:
            gpus = GPUtil.getGPUs()
            gpu = gpus[0].name if gpus else "GPU not found"
        except:
            gpu = "GPU information unavailable"

        os_info = get_os_info()

        disk = psutil.disk_usage('/')
        total_disk = f"{disk.total / (1024 ** 3):.2f}G"
        used_disk = f"{disk.used / (1024 ** 3):.2f}G"
        disk_info = f"{total_disk}/{used_disk}"

        memory = psutil.virtual_memory()
        memory_info = f"{memory.total / (1024 ** 3):.2f} GB"

        architecture = platform.machine()

        serial = generate_serial()

        return {
            "cpu": cpu,
            "gpu": gpu,
            "os": os_info,
            "disk": disk_info,
            "memory": memory_info,
            "architecture": architecture,
            "serial": serial
        }
    except Exception as e:
        return {
            "error": f"Error getting system info: {str(e)}"
        }

if __name__ == "__main__":
    system_info = get_system_info()