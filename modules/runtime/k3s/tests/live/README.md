# Check the standard environment

[Development guide](../../../../../docs/en/development.md) · [中文指南](../../../../../docs/zh/development.md)

On an isolated host with Docker, OpenSSH tools, `/dev/net/tun`, and an installed runtime AppArmor profile, run from the repository root:

```bash
TEST_FEATURES=docker \
TEST_ENVIRONMENT_IMAGE=your-test-environment:tag \
TEST_APPARMOR_PROFILE=your-installed-profile \
bash modules/runtime/k3s/tests/live/runner-smoke.sh
```

The image needs the standard environment's UID/GID 1000 account and runner dependencies. The check assigns it the test login name `researcher` and uses the explicitly supplied profile.

The script starts a temporary container, checks SSH, sudo, bubblewrap, binary file transfer, continued execution after SSH disconnect, durable logs, and batch exit status. It removes its container and temporary files on exit.
