# Photonic Cluster Generator

用于光子器件注册、交互式版图装配和 LVS 前置验证的统一工作台。

项目直接处理层级、多层 GDS，不使用 cell 名、端口标签或文字标记判断器件几何。独立器件经过几何端口候选和人工确认后进入版本化器件库，随后可与参数化器件共同装配为具有真实连接关系的集群 GDS。

## 主要功能

- **器件注册**：扫描独立 GDS 的 polygon，分层生成端口候选并由人工确认。
- **版本化器件库**：以源文件 SHA256 标识器件，更新已有模板时保留历史 revision。
- **版图装配**：拖放器件、端口吸附、自动布线、连通器件组整体移动和旋转。
- **多层画布**：按 `(layer/datatype)` 独立显示几何、端口和连接。
- **画布草稿**：自动保存模板、参数、位姿、连接和图层显隐状态，重启后自动恢复。
- **验证与证据**：重新读取落盘 GDS，检查层级、端口、连接、碰撞和网表一致性，并生成标注图。

器件注册和版图装配运行在同一个 Web 服务中，共用 `device_registry`。完成注册后只需在装配页面刷新器件库。

## 环境配置

### 推荐方式：创建独立 Conda 环境

需要 Conda/Miniconda 和 Windows PowerShell。Node.js 仅用于测试网页脚本语法，不影响工作台运行。

```powershell
Set-Location <PhotonicClusterGenerator目录>
conda env create -f environment.yml
conda activate photonic-cluster-generator
```

`environment.yml` 固定为已经通过完整测试的直接依赖版本：

| 组件 | 版本 |
|---|---:|
| Python | 3.10 |
| gdsfactory | 8.32.2 |
| gdstk | 1.0.0 |
| NumPy | 2.2.6 |
| Pillow | 12.0.0 |
| Shapely | 2.1.2 |

### 使用已有 Conda 环境

现有 `gdsfactory` 环境也可以继续使用：

```powershell
conda activate gdsfactory
python -m pip install -e .
```

启动脚本优先使用当前 `CONDA_PREFIX` 中的解释器，不包含本机绝对路径。也可以显式指定解释器：

```powershell
.\run_tests.ps1 -Python "C:\path\to\python.exe"
```

## 快速启动

激活环境后运行：

```powershell
.\run_photonic_workspace.ps1
```

浏览器地址：<http://127.0.0.1:8765/>

直接打开器件注册页：

```powershell
.\run_photonic_workspace.ps1 -StartPage register
```

运行期间需保留 PowerShell 窗口。端口被旧进程占用时，应先关闭旧工作台，不要同时启动多个 8765 服务。

## 器件注册

1. 打开顶部“器件注册”。
2. 输入包含独立器件 GDS 的目录并扫描。
3. 选择器件，按层查看主体几何和端口候选。
4. 选择真实端口并设置名称。
5. 选择现有器件族，或输入新器件族名称。
6. 设置唯一模板 ID 并保存。

端口候选来自各层 polygon 边界。候选生成和注册不读取 cell 名、端口对象或文字标签。人工确认的端口还必须通过边界位置、向内截面覆盖、宽度和 90° 倍数方向校验。

目录扫描按顶层 cell 的实际 polygon 内容处理：不含有效面积的 polygon、文字或零面积占位图形的编辑器上下文顶层会自动忽略；如果一个 GDS 中有多个包含有效几何的顶层，它们会作为独立候选分别显示，并各自保存顶层选择。该过程不通过 cell 名称判断器件主体。

### 器件身份和版本

- `模板 ID` 表示一个具体 GDS 模板版本。
- `器件族` 表示功能类别，也是装配时切换同族真实 GDS 版本的依据。
- 已注册身份以 GDS 内容 SHA256 为准，而不是文件名。
- 更新已有模板需要显式确认；旧记录归档到 `versions`。
- 原始源目录始终只读，不会被注册流程修改。

注册结果位于：

```text
device_registry/
├── catalog.json
└── templates/
    └── <template_id>__<sha256>.gds
```

该目录属于本地项目数据，包含 GDS 副本、端口坐标、哈希和版本历史，默认被 `.gitignore` 排除，不能提交到公开仓库。需要备份时应使用有访问控制的私有存储。

## 版图装配

装配模块加载两类器件：

- 标准参数化器件；
- `device_registry` 中人工注册的器件。

### 基本操作

1. 从左侧拖放器件，或双击添加到画布中心。
2. 使用画布左上角图层栏显示或隐藏 `(layer/datatype)`。
3. 单击两个未占用端口建立连接，或将端口拖近后自动吸附。
4. 拖动已连接器件时，整个连通组保持相对位姿共同移动。
5. “旋转 90°”对整个连通组执行刚体旋转。
6. 右键器件设置位置、旋转和可变参数。
7. 设置版图名，点击“导出并验证 GDS”。

器件名和连接名仅在鼠标悬停相关器件时显示；名称和端口圆点使用固定屏幕尺寸，不随画布缩放变化。

非参数化真实 GDS 在属性面板中显示每个端口的名称、层和宽度。其几何不会被数值拉伸，参数变化通过切换同族真实模板实现。

### 多层器件检查

注册多层器件后，版图装配页会根据本地目录中的真实 polygon 自动生成图层开关。开关同步控制 polygon、端口和相应连接的显示，不需要把器件数据写入源代码。

### 草稿恢复

画布修改后自动保存，也可以在右侧手动保存、恢复或清除。草稿位于：

```text
outputs/web_workspace/workspace_draft.json
```

草稿仅保存模板引用、参数、位姿、连接和图层状态；恢复时从当前器件库重新生成几何预览。写入采用临时文件和原子替换，避免中断留下不完整 JSON。

## 输出与验证

Web 装配结果写入：

```text
outputs/web_workspace/<layout_name>_<timestamp>_<id>/
```

| 文件 | 内容 |
|---|---|
| `*.gds` | 保留器件和布线 cell 引用的层级 GDS |
| `*.cir` | 与版图连接定义同源的结构参考网表 |
| `editor_spec.json` | 可追溯装配输入 |
| `manifest.json` | 器件、端口、网络、层级和模板哈希 |
| `validation.json` | 落盘 GDS 回读验证结果 |
| `*_annotated.png` | 集群、器件、网络和外部端口标注图 |
| `*_device_atlas.png` | 各器件的多层几何与端口图集 |

验证以重新读取磁盘上的 GDS 为准，而不是浏览器预览。检查内容包括：

- 模板 SHA256 和原始层集合；
- 器件与 route 的层级引用；
- 端口中心、方向、层和宽度；
- 端口复用、遗漏及非法跨层直连；
- 器件重叠、同层网络短接和 route 穿越；
- 外部 pin、label、GDS、网表和 manifest 一致性。

只有回读验证通过后，候选文件才会发布为正式输出。未连接端口会自动作为外部端口写入。

## 代码式装配

```powershell
.\run_code_assembly_demo.ps1
```

```python
from photonic_cluster_generator import Assembly, DeviceLibrary

library = DeviceLibrary("device_registry/catalog.json")
layout = Assembly("MY_CLUSTER", library=library)

layout.add_registered("device_a", "<local_template_id>", at=(0, 0))
layout.add_registered("device_b", "<local_template_id>")
layout.snap("device_b", "o1", "device_a", "o2", net_id="N_1")
layout.external("IN", "device_a", "o1")
layout.external("OUT", "device_b", "o2")

result = layout.export("outputs/my_cluster")
```

`snap` 会选择允许的旋转并平移完整器件，使真实端口中心重合。

## 命令行接口

安装为 editable package 后可以使用：

```powershell
photonic-cluster --help
```

也可以直接通过模块调用：

```powershell
python -m photonic_cluster_generator.cli workspace `
  --registry "device_registry/catalog.json" `
  --output-root "outputs/web_workspace" `
  --host 127.0.0.1 `
  --port 8765
```

扫描独立 GDS：

```powershell
python -m photonic_cluster_generator.cli scan-ports `
  "C:\path\to\device_gds_folder" `
  --output "outputs/port_candidates.json"
```

从 JSON 规格生成并验证集群：

```powershell
python -m photonic_cluster_generator.cli generate `
  "examples/lvs_mzi_cluster.json" `
  --output-dir "outputs/lvs_mzi_cluster"
```

## 测试

```powershell
.\run_tests.ps1
```

测试使用运行时生成的可公开合成 GDS，覆盖多层扫描与注册、跨层器件预览、动态器件库、网页脚本、草稿持久化、端口吸附、刚体变换、参数事务、层级导出、网表一致性、可视化输出，以及碰撞、悬空端口、端口复用和非法跨层连接等失败场景。测试不依赖任何私有 PDK 或已注册器件。

当前已验证环境会出现一条来自 gdsfactory 8.32.2 的 `ResourceWarning`，原因是依赖读取 `generic_tech/layer_views.yaml` 后未关闭文件。该告警不改变几何或测试结果。

## 项目结构

```text
PhotonicClusterGenerator/
├── device_registry/                 # 本地版本化器件库；内容不提交 Git
├── examples/                        # JSON 与代码示例
├── outputs/                         # 本地生成结果，不提交 Git
├── schemas/                         # 集群规格 Schema
├── scripts/                         # 环境与启动辅助脚本
├── src/photonic_cluster_generator/
│   ├── onboarding/                  # 几何扫描、端口确认、版本化注册
│   ├── editor/                      # 统一 Web 服务与装配界面
│   ├── assembly.py                  # 代码式装配模型
│   ├── generator.py                 # 层级 GDS 生成
│   ├── netlist.py                   # 参考网表
│   └── validator.py                 # 落盘回读验证
├── tests/
├── environment.yml                  # 可复现 Conda 环境
├── requirements.txt                 # 固定直接依赖版本
└── pyproject.toml                   # Python 包配置
```

## GitHub 发布

项目仓库：<https://github.com/zhouhh2024/photonic-layout-workbench>

`.gitignore` 会排除缓存、虚拟环境、构建目录、生成输出、全部 GDS/OASIS，以及 `device_registry` 和本地 PDK 目录。公开提交中不得包含：

- PDK 或器件模板 GDS；
- 已注册器件副本与 `catalog.json`；
- 端口坐标、模板哈希和注册历史；
- 由非公开器件数据直接派生的专用示例或映射报告。

克隆公开仓库后，首次启动会在本机创建空的 `device_registry`。用户通过“器件注册”导入自己的器件，数据只留在本机。

发布前可检查：

```powershell
git status --short
git ls-files | Select-String -Pattern '\.(gds|oas|oasis)$|device_registry/|/pdk/'
```

第二条命令应无输出。

## 许可证

仓库中实际提交的自研源代码和文档采用 [MIT License](LICENSE)。允许个人研究、修改和再发布，但必须保留原版权声明和许可声明。

该许可不适用于任何未提交的本地数据，包括：

- PDK 和器件模板 GDS/OASIS；
- `device_registry` 中的器件副本、目录、端口坐标、哈希及版本历史；
- 由非公开器件数据直接派生的版图、示例、报告或测试结果。

这些私有数据不随仓库发布，也没有通过本项目获得任何再分发授权。项目依赖的软件包继续适用各自的许可证。

首次推送：

```powershell
git init -b main
git add .
git commit -m "Initial release of Photonic Cluster Generator"
git remote add origin git@github.com:zhouhh2024/photonic-layout-workbench.git
git push -u origin main
```

后续更新：

```powershell
git add .
git commit -m "Describe the change"
git push
```

## 当前边界

- 端口候选属于几何召回，端口语义仍需人工确认。
- Web 工作台尚未提供多步撤销与重做。
- 当前布线不包含任意规模版图的全局 floorplan、拥塞优化和 rip-up/re-route。
- `.cir` 是 LVS 前端结构参考网表，不替代 foundry sign-off DRC/LVS rule deck。
