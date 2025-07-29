import os
import psutil
import hashlib
import platform
import subprocess
import socket

system = platform.system()

def get_gpu_info():
    if system == "Linux":
        try:
            output = subprocess.check_output("nvidia-smi --query-gpu=name --format=csv,noheader", shell=True).decode().strip()
            if output:
                processor = "gpu"
                return output, processor
        except:
            pass

        try:
            subprocess.call("sudo update-pciids", shell=True)
            output = subprocess.check_output("lspci | grep VGA", shell=True).decode().strip()
            processor = "gpu"
            return output.split(":")[2].strip(), processor
        except:
            pass

    elif system == "Windows":
        try:
            command = "powershell -command \"Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name\""
            result = subprocess.check_output(command, shell=True).decode('utf-8', errors='ignore').strip()
            processor = "gpu"
            return result.split('\n'), processor
        except:
            pass

    elif system == "Darwin":
        try:
            import Metal
            device = Metal.MTLCreateSystemDefaultDevice()
            if device:
                processor = "mps"
                return device.name(), processor
        except:
            pass

    processor = "cpu"
    return "GPU not found", processor

def get_cpu_info():
    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            cpu_name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            winreg.CloseKey(key)
            return cpu_name.strip()
        except:
            pass

    elif system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        except:
            pass

    elif system == "Darwin":  # macOS
        try:
            output = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"]).decode()
            return output.strip()
        except:
            pass

    return platform.processor() or "Unknown CPU"

def get_os_info():
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

def get_device_platform():
    if os.path.exists('/etc/nv_tegra_release'):
        return "Jetson"

    if os.path.exists('/sys/firmware/devicetree/base/model'):
        try:
            with open('/sys/firmware/devicetree/base/model', 'r') as f:
                model = f.read().strip('\0').lower()
                if "raspberry pi" in model:
                    return "Raspberry Pi"
        except Exception:
            pass

    if system == "Darwin":
        return "Mac"

    if system == "Windows" or system == "Linux":
        return "PC"

    return "Unknown"

def get_device_name():
    if system == "Darwin":
        import subprocess
        result = subprocess.check_output(['scutil', '--get', 'ComputerName'], text=True)
        return result.split(' (')[0] if ' (' in result and result.endswith(')') else result
    elif system == "Windows":
        return os.environ.get('COMPUTERNAME', socket.gethostname())
    elif system == "Linux":
        return socket.gethostname()
    else:
        return socket.gethostname()


def get_serial():
    try:
        if system == "Windows":
            result = subprocess.run(["powershell", "-command", "(Get-WmiObject Win32_BIOS).SerialNumber"],
                                    capture_output=True, text=True, check=True)
            serial = result.stdout.strip()

        elif system == "Darwin":  # macOS
            result = subprocess.run(["system_profiler", "SPHardwareDataType"], capture_output=True, text=True,
                                    check=True)
            for line in result.stdout.split("\n"):
                if "Serial Number" in line:
                    serial = line.split(":")[-1].strip()
                    break

        elif system == "Linux":
            result = subprocess.run(["cat", "/var/lib/dbus/machine-id"],
                                    capture_output=True, text=True, check=True)
            serial = result.stdout.strip()

        else:
            return None

        hash_obj = hashlib.sha256(serial.encode())
        hex_digest = hash_obj.hexdigest()

        short_serial = hex_digest[:8].upper()

        return short_serial

    except Exception as e:
        return e


def get_system_info():
    try:
        cpu = get_cpu_info()
        gpu, processor = get_gpu_info()
        os_info = get_os_info()

        disk = psutil.disk_usage('/')
        total_disk = f"{disk.total / (1024 ** 3):.2f}G"
        used_disk = f"{disk.used / (1024 ** 3):.2f}G"
        disk_info = f"{total_disk}/{used_disk}"

        memory = psutil.virtual_memory()
        memory_info = f"{memory.total / (1024 ** 3):.2f} GB"

        architecture = platform.machine()

        serial = get_serial()

        device_name = get_device_name()

        device_platform = get_device_platform()

        return {
            "cpu": cpu,
            "gpu": gpu,
            "os": os_info,
            "serial": serial,
            "disk": disk_info,
            "memory": memory_info,
            "processor": processor,
            "device_name": device_name,
            "platform": device_platform,
            "architecture": architecture,
        }
    except Exception as e:
        return {
            "error": f"Error getting system info: {str(e)}"
        }

if __name__ == "__main__":
    system_info = get_system_info()