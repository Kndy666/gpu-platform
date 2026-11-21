# Standard environment smoke test

`runner-smoke.sh` starts a temporary Docker container from an explicitly supplied standard environment image. It checks SSH, sudo, bubblewrap, binary file transfer, task continuation after SSH disconnect, durable logs, and batch exit status. The script removes its container and temporary files on exit.

Run on an isolated host with Docker, OpenSSH tools, `/dev/net/tun`, and the installed runtime AppArmor profile:

```sh
TEST_FEATURES=docker \
TEST_ENVIRONMENT_IMAGE=your-test-environment:tag \
TEST_APPARMOR_PROFILE=your-installed-profile \
bash modules/runtime/k3s/tests/live/runner-smoke.sh
```

The image must contain the standard environment's UID/GID 1000 account and runner dependencies. The runner assigns that account the test login name `researcher`. The host profile is supplied explicitly so the probe cannot silently select a running deployment.
