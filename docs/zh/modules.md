# 开发模块

模块实现 `gpu_platform_contracts.slots` 中的一个插槽，声明交接物和资源，通过 entry point 注册。外部实现依赖 `gpu-platform-contracts` 及自己的技术库，控制中心和节点在运行时发现它。

## 包和声明

发行包可命名为 `gpu-platform-auth-directory`，注册实现类：

```toml
[project]
name = "gpu-platform-auth-directory"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gpu-platform-contracts==0.4.0"]

[project.entry-points."gpu_platform.slots.auth"]
directory = "directory_auth.module:Module"
```

实现类提供 `ModuleDescriptor`：

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

`verify_with_directory` 是模块自己的身份验证过程。验证成功返回契约定义的身份类型，拒绝时返回 `None`。同一身份多次登录保持稳定 subject。本地认证模块提供完整可执行示例。

描述信息包括交接物种类与形式的 `provides`、`accepts`，以及资源、事件、参数和多语言文字。`requires` 限定其他插槽的兼容实现，`dependencies` 声明安装前置模块。资源定义包含稳定键、单位、标签和汇总范围。`accounting` 为 `task`、`volume` 或 `image`；`volume_kind` 标识个人卷或工作卷容量。`allocation_group`、`allocation_field`、`per_allocation` 描述逐项分配的资源，`minimum`、`maximum`、`step`、`integral` 约束数量。面板按定义生成字段，核心按资源键保存数量和分配组。

十个插槽名称为 `auth`、`transport`、`entry`、`runtime`、`storage`、`registry`、`build`、`gpu`、`metrics`、`notify`。实现所选接口的全部抽象职责，运行模块交付标准开发环境。

使用 `Options(Contract)` 声明参数，并在实现类中设置 `options_model`。`descriptor.parameters` 保存该模型完整的 JSON Schema，包含嵌套字段和默认值。安装器和运行时加载器在注入框架上下文之前调用 `validate_options`；未知字段会被拒绝，省略或为 null 的可选字段继续使用实现按上下文计算的默认值。

## 上下文和生命周期

运行时接收 `ModuleContext(node_id, options, state_directory)`。持久状态放入状态目录下的模块专用子目录。任务、镜像、卷和数据流使用稳定标识，重复操作保持幂等，修改前检查归属。

安装器调用 `check_host`、`install_host`、`install`、`uninstall`、`uninstall_host`、`compose` 类方法。安装器提供 `gpu_platform_contracts.module.HostContext`，包含解析后的主机和集群配置、主机信息和可注入的命令执行器。提供方可在 `context.installation_handoffs` 中发布带版本的字典；接收方按交接顺序取得同一映射。例如，仓库在 `image` 下发布 `RegistryTrust`，提供端点和 CA 路径，运行模块用它配置镜像拉取信任。检查返回带代码和参数的 `CheckResult`。主机基础设施阶段先执行，随后按提供方顺序安装，卸载反序执行。

`gpu_platform_contracts.host.HostOwnership` 为新建文件、外部资源的撤销命令和单独的配置变更提供持久日志。先确认外部资源不存在，再在创建前记录所有权。卸载按反序执行撤销操作，成功后删除对应记录，并保留管理员修改过的文件和配置项。模块卸载应保留用户数据和共享系统软件包。

`compose(context)` 返回 Compose 片段。映射合并，挂载列表扩展，命令替换。模块需要常驻进程时提供持久挂载与健康检查；主机基础设施由安装钩子管理。

加速器模块通过 `GPURuntime` 安装交接物提供 OCI 运行时路径和可选启动钩子，任务运行模块据此配置加速器 RuntimeClass。节点将资源申请交给所选 GPU 插槽评估，也包含按设备数量申请的资源。

内置 `ascend` 模块位于 `gpu` 插槽，支持已安装驱动的 ARM64 昇腾 310P 主机。环境通过 `npu_count` 申请整颗芯片，由一个环境独占。成员和用户组沿用算力的单卡上限，除以 100 后向下取整作为 NPU 配额：100% 和 150% 均可使用 1 颗，200% 可使用 2 颗。模块通过 `quota_rules` 声明换算关系，不单独保存 NPU 配额。持久化分配记录在节点服务重启后继续保持设备归属。模块安装 Ascend Docker Runtime 26.1.0，并通过模块描述提供设备名称。开发镜像需包含与驱动匹配的 CANN 环境。

```yaml
hosts:
  ascend-node:
    architecture: arm64
    slots:
      gpu:
        module: ascend
        options: {}
```

## 可移植契约测试

公共定义包提供十个插槽的可复用测试套件。在 `tests/conftest.py` 中设置 `pytest_plugins = ["gpu_platform_contracts.testing.fixtures"]`，可启用唯一标识、测试账号默认值、有界轮询和 TCP 回显探针。实现侧 fixture 提供模块实例和隔离资源。测试类继承套件，并提供其约定 fixture：

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

fixture 使用独立目录服务或注入的认证客户端。各套件所需 fixture 见 `gpu_platform_contracts/testing/suites.py`。补充资源守恒、并发预留、重试、取消、到期、授权和失败清理的行为测试。临时路径、端口、时钟和测试身份由 fixture 提供，断言面向行为和结构化数据。

```bash
pytest
uv run lint-imports
```

## 安装和选择

将模块安装进派生的控制中心或节点镜像，同时安装到主机安装工具环境，使校验能够发现声明和钩子。在集群文件中选择 entry point 名称：

```yaml
control:
  auth:
    module: directory
    options:
      issuer: https://identity.example.edu
```

将派生镜像标签填入 `cluster.control_image` 或 `cluster.node_image`，安装前校验完整配置。模块标签和事件必须提供英文，可通过 `LocalizedText.zh_hans` 提供中文。协议错误和报告使用代码与参数。
