import pytest
from pydantic import ValidationError

from gpu_platform_transport_reverse_ssh.module import Module


@pytest.mark.parametrize("options", [
    {"ssh_port": 0},
    {"ssh_port": 65536},
    {"host": "-oProxyCommand=bad"},
    {"ssh_user": "user with spaces"},
    {"identity_file": "unrecognized"},
])
def test_invalid_ssh_options_are_rejected_before_host_operations(options):
    with pytest.raises(ValidationError):
        Module.validate_options(options)


def test_omitted_paths_keep_context_dependent_defaults():
    assert Module.validate_options({"ssh_port": "2201"}) == {"ssh_port": 2201}
