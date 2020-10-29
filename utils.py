import sys, os, time, socket
import psutil
import logging


def get_app_path():
    return os.path.dirname(os.path.realpath(__file__)) + os.sep


def get_file_size(filePath):
    try:
        return os.path.getsize(filePath)
    except:
        return 0

def get_file_path(filePath):
    path, file = os.path.split(filePath)
    return path


def get_file_name(filePath):
    path, file = os.path.split(filePath)
    return file


def is_file_exist(filePath):
    return os.path.isfile(filePath)


def get_disk_space():
    mount_pt = "."  # if getBoardVersion() == 1 else "/media/sd"
    st = psutil.disk_usage(mount_pt)
    return st.free, st.total


def is_raspberry_pi():
    return os.name != "nt" and os.uname()[0] == "Linux" and 'arm' in os.uname()[4].lower()


def is_windows():
    return os.name == "nt"


def get_ip_address():
    try:
        if os.name == "nt":
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except:
        return "-"

def get_cpu_load():
    try:
        return int(psutil.cpu_percent())
    except:
        return 0

def get_ram_usage():
    try:
        return int(psutil.virtual_memory().percent)
    except:
        return 0
