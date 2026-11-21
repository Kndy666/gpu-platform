"""AppArmor policy for the rootless builder's user namespace."""

from pathlib import Path
import shutil

from gpu_platform_contracts.host import HostOwnership

PROFILE_NAME = "gpu-platform-buildkit"
PROFILE_PATH = Path("/etc/apparmor.d/gpu-platform-buildkit")
USERNS_RESTRICTION = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def profile(userns):
    if userns:
        return f"abi <abi/4.0>,\nprofile {PROFILE_NAME} flags=(unconfined) {{\n  userns,\n}}\n"
    return (
        f"profile {PROFILE_NAME} flags=(attach_disconnected,mediate_deleted) {{\n"
        "  capability,\n  network,\n  file,\n  mount,\n  umount,\n"
        "  pivot_root,\n  ptrace,\n  signal,\n  unix,\n}\n"
    )


def install(context):
    if not shutil.which("apparmor_parser"):
        context.runner(["apt-get", "install", "-y", "apparmor"])
    command = ["apparmor_parser", "--skip-kernel-load", "--skip-read-cache"]
    content = profile(True)
    result = context.runner(command, input=content, text=True, capture_output=True, check=False)
    if result.returncode:
        if USERNS_RESTRICTION.exists() and USERNS_RESTRICTION.read_text().strip() == "1":
            raise RuntimeError("BuildKit requires AppArmor user namespace policy support")
        content = profile(False)
        context.runner(command, input=content, text=True, capture_output=True, check=True)
    ownership = HostOwnership(context, "buildkit")
    key = "file:" + str(PROFILE_PATH)
    owned = any(record["key"] == key for record in ownership.records)
    if PROFILE_PATH.exists() and PROFILE_PATH.read_text() != content and not owned:
        raise FileExistsError(PROFILE_PATH)
    undo = {str(PROFILE_PATH): [["apparmor_parser", "--remove", str(PROFILE_PATH)]]}
    with ownership.files([PROFILE_PATH], before_remove=undo):
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(content)
        PROFILE_PATH.chmod(0o644)
        context.runner(["apparmor_parser", "--replace", str(PROFILE_PATH)])


def uninstall(context):
    HostOwnership(context, "buildkit").cleanup()
