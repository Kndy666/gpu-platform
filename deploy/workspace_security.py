"""Install workspace security profiles for the execution host architecture."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def seccomp_profile(architecture):
    profile = json.loads((ROOT / 'deploy/profiles/workspace-seccomp.json').read_text())
    profile['architectures'] = {
        'arm64': ['SCMP_ARCH_AARCH64'],
        'amd64': ['SCMP_ARCH_X86_64', 'SCMP_ARCH_X86', 'SCMP_ARCH_X32'],
    }[architecture]
    if architecture == 'amd64':
        # glibc uses ARCH_SET_FS to initialize thread-local storage on x86-64.
        allowed = next(rule for rule in profile['syscalls'] if rule['action'] == 'SCMP_ACT_ALLOW')
        allowed['names'] = sorted(set(allowed['names']) | {'arch_prctl'})
    return profile


def install_security(architecture):
    profiles = ROOT / 'deploy/profiles'
    seccomp = Path('/var/lib/kubelet/seccomp')
    seccomp.mkdir(parents=True, exist_ok=True)
    (seccomp / 'gpu-platform-workspace.json').write_text(json.dumps(seccomp_profile(architecture), indent=2) + '\n')
    apparmor = Path('/etc/apparmor.d/gpu-platform-workspace')
    profile = (profiles / 'workspace-apparmor').read_text()
    if Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns').exists():
        profile = profile.replace('  capability,', '  capability,\n  userns,')
        build_profile = Path('/etc/apparmor.d/gpu-platform-build')
        build_profile.write_text('abi <abi/4.0>,\ninclude <tunables/global>\nprofile gpu-platform-build flags=(unconfined) {\n  userns,\n}\n')
        subprocess.run(['apparmor_parser', '-r', str(build_profile)], check=True)
    apparmor.write_text(profile)
    subprocess.run(['apparmor_parser', '-r', str(apparmor)], check=True)
