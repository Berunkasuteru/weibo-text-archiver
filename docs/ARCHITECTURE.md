# Weibo Text Archiver Architecture

V7 的目标不是“把 V6 拆成很多文件”，而是建立几个可以长期守住的边界。

## Data flow

```text
Weibo HTTPS
    │
    ▼
network.py
    │  bytes / JSON
    ▼
client.py
    │  raw API objects
    ▼
parser.py
    │
    ▼
models.py                ← frozen dataclasses / stable product vocabulary
    │
    ├──────────────► storage.py       normalized cache
    │
    ▼
exporter.py ◄──────── frozen ExportOptions snapshot ◄──────── GUI preset/custom
    │
    ▼
markdown_v5.py           legacy baseline + explicit Alpha3 policy
    │
    └──────────────► selected Full / AI Analysis / Custom Markdown outputs
```

登录单独走：

```text
auth.py → Weibo Passport QR SSO → local credential
```

GUI 只负责：

```text
input/preset → resolve immutable options → task orchestration → progress → result
```

展示选项只进入 exporter/renderer。它们不进入 parser、network、models，
也不裁剪 `storage.py` 保存的 normalized archive。

## Four invariants

### 1. Exporter never knows Weibo's raw JSON

`Post` / `UserProfile` 是 API 与产品之间的防火墙。

禁止：

```python
post.raw["some_field"]
Post.from_json(...)
```

如果微博改字段，只改 `parser.py/client.py`。

### 2. Missing data is not zero

模型层：

```text
0     = API 明确返回 0
None  = API 没提供 / 无法可靠解析
```

渲染规则以后可以变化，但这个语义不允许丢。

### 3. A stale task cannot mutate current UI

所有 worker event 都携带 `generation`。

GUI 在消费事件前必须：

```python
if generation != current_generation:
    ignore
```

取消任务会立即提升 generation，因此 Python 无法强制终止线程也不会导致旧任务踩坏新状态。

### 4. Long text is either proven complete or explicitly incomplete

如果微博标记 `isLongText`，V7 必须取得全文。

Alpha4 只允许一种显式不完整结果：同一 Post 节点的 `extend` 和
`detail` 都被安全分类为当前无查看权限。此时：

```text
text = None
text_preview = timeline 中仍可见的预览
content_state = INCOMPLETE
```

顶层微博与转发原文分别拥有状态。其他网络、认证、schema、ID mismatch
或混合失败仍然 fail closed，绝不能把列表页预览当完整正文。

normalized cache 在 0.5.2 写入 `schema_version: 3`，在版本 2 的 source
timestamp provenance、已知 UTC offset 和 optional author UID 之外，增加抓取时
visibility semantic state 与受控 raw provenance。版本 2 没有 visibility fact，
不得静默当作 visibility-aware Archive。当前应用只写 cache，不从 cache 恢复
Archive；本次不迁移或删除旧文件。无版本旧缓存仍属于 legacy/unversioned，
未来不得静默当作可信 mother archive 读取。

Visibility 的架构边界保持为：

```text
production list response
→ parser normalization
→ frozen Post.visibility
→ one Archive
→ local Full / AI Analysis / Custom policies
```

top-level W 使用当前受控证据映射；缺失、畸形或未来未知值保持 UNKNOWN。
nested RT 只保留结构有效的独立 raw provenance，0.5.2 不解释其 semantic
visibility，也不输出 per-RT semantic label。Full 不按 visibility 过滤；AI 与
Custom 只按 top-level W 本地派生，不增加网络请求或不同快照。

## Runtime dependency policy

`weibo_archive/` 运行时不得：

- import requests
- import playwright
- import selenium
- import subprocess
- pip install
- 下载并执行 GitHub 代码
- 关闭 TLS certificate verification

唯一 vendored 第三方组件是用于生成二维码矩阵的 `python-qrcode`。

## V7 Alpha boundaries

Alpha 1 已完成：

- 独立 HTTPS client
- QR SSO
- normalized models
- frozen V5 renderer baseline
- range model
- task state + generation
- security redaction
- tests

Alpha 1 暂未完成：

- DPAPI credential storage
- resumable initial archive
- incremental update
- final AI token-format revision
- PyInstaller onedir build
- Authenticode signing

Alpha 3 的边界是 UX polish：

- frozen `ExportOptions` 是单个导出任务的展示策略快照；
- 完整归档、AI 分析版、自定义一次只生成一个主 Markdown；
- legacy/default renderer 调用仍保持 Alpha2/V5 golden 不变；
- 完成窗口只在用户点击后调用 Windows 打开能力；
- 不改变抓取、长微博、分页、认证或任务 generation 语义。

Alpha 4 增加不完整档案语义：

- `Post.text == ""` 表示已确认没有文字正文；
- `Post.text is None` 只表示正文无法取得；
- timeline preview 单独存入 `text_preview`；
- `Archive.integrity` 从 frozen Post 树现场计算，不污染分页 `Termination`；
- 任务可成功完成并带 integrity warning，但未知情况仍然失败。
