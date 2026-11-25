import csv
import ctypes
import subprocess
from typing import Literal

from gpu_platform_contracts.base import Contract


class DriverOwnership(Contract):
    daemon: Literal["stopped", "starting", "ready"]
    compute_modes: dict[str, int]


def run(args):
    return subprocess.check_output(args, text=True, timeout=30)


def cuda_inventory():
    lib = ctypes.CDLL("libcuda.so.1")
    if lib.cuInit(0):
        raise RuntimeError("CUDA inventory unavailable")
    devices = []
    for row in csv.reader(
        run(
            [
                "nvidia-smi",
                "--query-gpu=uuid,index,name,memory.total,pci.bus_id",
                "--format=csv,noheader,nounits",
            ]
        ).splitlines()
    ):
        index = int(row[1])
        sms = ctypes.c_int()
        if lib.cuDeviceGetAttribute(ctypes.byref(sms), 16, index):
            raise RuntimeError("Cannot query GPU SM count")
        devices.append(
            {
                "uuid": row[0].strip(),
                "index": index,
                "name": row[2].strip(),
                "memory_mib": int(row[3]),
                "usable_memory_mib": int(row[3]),
                "pci": row[4].strip(),
                "sm_count": sms.value,
                "ready": True,
            }
        )
    return devices
