import os
import psutil
import GPUtil
import platform
import subprocess


def get_cpu_info():
    """
    Returns detailed CPU model name across different operating systems
    """
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
    """
    Returns detailed OS information including distribution version for Linux
    """
    system = platform.system()
    if system == "Linux":
        try:
            # Linux dağıtımı ve sürüm bilgisini al
            distro_info = {}
            if os.path.exists('/etc/os-release'):
                with open('/etc/os-release') as f:
                    for line in f:
                        if '=' in line:
                            key, value = line.rstrip().split('=', 1)
                            distro_info[key] = value.strip('"')

            if 'VERSION_ID' in distro_info:
                return f"Ubuntu {distro_info['VERSION_ID']}"  # veya distro_info['PRETTY_NAME']
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

def get_system_info():
    """
    Returns system information including CPU, GPU, OS, disk, memory and architecture
    in a platform-independent way.
    """
    try:
        # CPU bilgisi
        cpu = get_cpu_info()

        # GPU bilgisi
        try:
            gpus = GPUtil.getGPUs()
            gpu = gpus[0].name if gpus else "GPU not found"
        except:
            gpu = "GPU information unavailable"

        # OS bilgisi
        os_info = get_os_info()

        # Disk bilgisi
        disk = psutil.disk_usage('/')
        total_disk = f"{disk.total / (1024 ** 3):.2f}G"
        used_disk = f"{disk.used / (1024 ** 3):.2f}G"
        disk_info = f"{total_disk}/{used_disk}"

        # RAM bilgisi
        memory = psutil.virtual_memory()
        memory_info = f"{memory.total / (1024 ** 3):.2f} GB"

        # Mimari bilgisi
        architecture = platform.machine()

        return {
            "cpu": cpu,
            "gpu": gpu,
            "os": os_info,
            "disk": disk_info,
            "memory": memory_info,
            "architecture": architecture
        }
    except Exception as e:
        return {
            "error": f"Error getting system info: {str(e)}"
        }

if __name__ == "__main__":
    system_info = get_system_info()