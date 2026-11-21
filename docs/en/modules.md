# Develop a module

A module implements one slot from `gpu_platform_contracts.slots`, declares its handoffs and resources, and registers an entry point. External implementations depend on `gpu-platform-contracts` plus their own implementation libraries. The control service and node discover them at runtime.

## Package and descriptor

Use a distribution name such as `gpu-platform-auth-directory` and register the implementation class:

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

The module's `descriptor` is a `ModuleDescriptor`:

```python
from gpu_platform_contracts.base import Contract
from gpu_platform_contracts.module import ModuleDescriptor
from gpu_platform_contracts.slots.auth import Auth

class Options(Contract):
    issuer: str

class Module(Auth):
    options_model = Options
    descriptor = ModuleDescriptor(
        name="directory", slot="auth", side="control",
        version="0.1.0", api_version="1.0",
        parameters=Options.model_json_schema(),
    )

    def authenticate(self, credentials):
        identity = self.verify_with_directory(credentials)
        return identity
```

`verify_with_directory` is the implementation's verified authentication exchange. Return the contract's identity type, or `None` for rejected credentials. Identity subjects must stay stable across successful sign-ins. The local auth module provides a complete executable example.

Descriptors declare `provides` and `accepts` mappings from handoff kind to supported forms, `resources`, `events`, parameter metadata, and localized text. `requires` constrains compatible implementations in another slot; `dependencies` declares installation prerequisites. A resource definition includes a stable key, unit, label, and aggregation scope. Set `accounting` to `task`, `volume`, or `image`; `volume_kind` identifies home or work capacity. `allocation_group`, `allocation_field`, and `per_allocation` describe resources allocated in repeated groups. `minimum`, `maximum`, `step`, and `integral` constrain amounts. The panel generates fields from these definitions, and the core stores quantities and allocation groups by resource key.

Declare accepted options in an `Options(Contract)` model and assign `options_model` on the implementation. `descriptor.parameters` contains its complete JSON Schema, including nested fields and defaults. The installer and runtime loaders call `validate_options` before adding framework context; unknown values are rejected, and omitted or null optional fields keep their implementation-derived defaults.

The ten slot names are `auth`, `transport`, `entry`, `runtime`, `storage`, `registry`, `build`, `gpu`, `metrics`, and `notify`. Implement every abstract responsibility in the selected interface. Runtime implementations must supply the standard development environment contract.

## Context and lifecycle

Runtime construction receives `ModuleContext(node_id, options, state_directory)`. Keep durable module state below a module-specific child directory. Commands and retries reuse stable task, image, volume, and stream identifiers; make repeatable operations idempotent and check ownership before mutation.

Installer hooks are class methods: `check_host`, `install_host`, `install`, `uninstall`, `uninstall_host`, and `compose`. The installer supplies `gpu_platform_contracts.module.HostContext` with resolved host and cluster configuration, host facts, and an injected command runner. Provider hooks can publish versioned dictionaries in `context.installation_handoffs`; consumer hooks receive the same mapping in provider order. For example, a registry publishes an `image` installation handoff using `RegistryTrust` with an endpoint and CA path, and an accepting runtime configures image pull trust. Return `CheckResult` values containing message codes and parameters. Host prerequisite hooks run before provider-ordered installation; uninstall runs in reverse order.

Accelerator modules publish a `GPURuntime` installation handoff with their OCI runtime binary and optional startup hook. The runtime module consumes this handoff to configure its accelerator RuntimeClass. Node assessment delegates resource intent to the selected GPU slot, including scalar device counts.

The built-in `ascend` implementation occupies the `gpu` slot. It supports ARM64 Ascend 310P hosts with preinstalled drivers and assigns whole chips exclusively through `npu_count`. Members and groups configure the existing per-device compute percentage; the NPU quota is that percentage divided by 100, rounded down. For example, both 100% and 150% permit one NPU, and 200% permits two. The module declares this conversion through `quota_rules`, so no separate NPU quota is stored. Its durable leases preserve physical chip identity across node service restarts. The module installs Ascend Docker Runtime 26.1.0 and provides device labels through descriptor text metadata. Development images must include a compatible CANN stack.

```yaml
hosts:
  ascend-node:
    architecture: arm64
    slots:
      gpu:
        module: ascend
        options: {}
```

`gpu_platform_contracts.host.HostOwnership` provides a durable journal for created files, external resource undo commands, and individual configuration changes. Record external-resource ownership only after confirming the resource is absent, before creating it. Teardown consumes successful undo records in reverse order and preserves operator-modified files and configuration entries. Keep user data and shared package removal outside module teardown.

`compose(context)` returns a Compose fragment. Mapping values merge, mount lists extend, and commands replace previous commands. Supply persistent mounts and service health checks where the module needs a daemon. Host-only infrastructure belongs in installation hooks.

## Portable contract tests

The contracts package exports reusable suites for all ten slots. Register the optional fixture plugin in `tests/conftest.py` with `pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]`. It supplies unique identifiers, test account defaults, bounded polling helpers, and a TCP echo probe. Implementation fixtures supply module instances and isolated resources. A test class inherits a suite and supplies its fixtures:

```python
import pytest
from gpu_platform_contracts.testing import AuthContract
from gpu_platform_contracts.module import ModuleContext

@pytest.fixture
def module_context(tmp_path):
    return ModuleContext(node_id="fixture", state_directory=tmp_path)

class TestDirectoryAuthentication(AuthContract):
    pass

@pytest.fixture
def valid_credentials(directory_fixture):
    return directory_fixture.valid_credentials

@pytest.fixture
def invalid_credentials(directory_fixture):
    return directory_fixture.invalid_credentials

@pytest.fixture
def auth(directory_fixture, module_context):
    return directory_fixture.create_module(module_context)
```

The fixture represents an isolated directory service or injected authentication client. Each suite names the fixtures it consumes; read `gpu_platform_contracts/testing/suites.py` for runtime, storage, registry, and other slot harnesses. Add tests for resource conservation, concurrent reservations, retries, cancellation, expiry, authorization, and failure cleanup. Supply temporary paths, allocated ports, clocks, and test identities through fixtures. Test assertions target behavior and structured data.

Run the suite in the external package and the workspace layer checker after integrating a module:

```bash
pytest
uv run lint-imports
```

## Install and select

Install the module distribution into a derived control or node image and into the host installer environment so validation can discover its descriptor and hooks. Select its entry-point name in the cluster file:

```yaml
control:
  auth:
    module: directory
    options:
      issuer: https://identity.example.edu
```

Set the custom image tag in `cluster.control_image`, `cluster.node_image`. Validate the complete configuration before installation. English text is required for module-facing labels and events; `LocalizedText.zh_hans` supplies optional Chinese translations. Protocol errors and reports carry codes and parameters.
