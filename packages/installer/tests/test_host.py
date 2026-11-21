import json
from types import SimpleNamespace

from gpu_platform_installer.host.system import HostContext, configure_firewall


def test_firewall_auto_interface_is_persisted_and_reconfiguration_cleans_owned_rules(tmp_path):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps([{"dev": "eth1", "metric": 100}, {"dev": "eth0", "metric": 10}]))
    context = HostContext("fixture", {}, tmp_path, {}, {"parameters": {"network": {
        "public_interface": "auto", "private_ports": [6443, 10250], "private_udp_ports": [8472]}}}, run)
    directory = tmp_path / "etc"
    configure_firewall(context, directory)
    rules = json.loads((directory / "firewall-rules.json").read_text())
    assert len(rules) == 6
    assert {(rule[5], rule[7]) for rule in rules} == {("tcp", "6443"), ("tcp", "10250"), ("udp", "8472")}
    assert {rule[3] for rule in rules} == {"eth0"}
    assert {rule[0] for rule in rules} == {"iptables", "ip6tables"}
    assert ["systemctl", "enable", "gpu-platform-firewall.service"] in calls
    context.host["parameters"]["network"]["private_ports"] = []
    context.host["parameters"]["network"]["private_udp_ports"] = []
    configure_firewall(context, directory)
    assert json.loads((directory / "firewall-rules.json").read_text()) == []
    # Execute the generated owned-rule cleanup against an injected iptables shim.
    commands = tmp_path / "commands"
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    for name in ("iptables", "ip6tables"):
        shim = bin_directory / name
        shim.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\n[ "$1" != -C ]\n')
        shim.chmod(0o755)
    import os
    import subprocess
    subprocess.run([str(directory / "firewall.sh"), "apply"], check=True,
                   env={**os.environ, "PATH": str(bin_directory) + os.pathsep + os.environ["PATH"], "COMMANDS": str(commands)})
    checks = [line.split() for line in commands.read_text().splitlines()]
    assert len(checks) == 6 and all(row[0] == "-C" for row in checks)


def test_firewall_teardown_removes_only_owned_artifacts(tmp_path):
    from gpu_platform_installer.host.system import remove_firewall

    calls = []
    context = HostContext("fixture", {}, tmp_path, {}, {}, lambda argv, **kwargs: calls.append(argv))
    directory = tmp_path / "etc"
    directory.mkdir()
    (directory / "firewall-rules.json").write_text("[]")
    (directory / "firewall.sh").write_text("#!/bin/sh\nexit 0\n")
    (directory / "gpu-platform-firewall.service").touch()
    unrelated = directory / "operator-notes"
    unrelated.write_text("keep")
    remove_firewall(context, directory)
    assert list(directory.iterdir()) == [unrelated]
    assert [str(directory / "firewall.sh"), "stop"] in calls
    first_calls = list(calls)
    remove_firewall(context, directory)
    assert calls == first_calls
