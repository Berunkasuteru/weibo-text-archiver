# 微博文字导出器 pre-1.0 开发路线图

Last updated: 2026-08-13

## 当前基线

- 当前公开版本：0.4.0 completed / accepted
- Git tag：`v0.4.0`
- 当前公开提交：`Initial public release`
- 版本策略：pre-1.0 语义化版本
- 项目状态 / 决策文档已建立：
  - `docs/PROJECT_STATE.md`
  - `docs/DECISIONS.md`

当前已经解决的核心问题包括：

- 独立 `WeiboClient`
- `raw JSON -> parser -> frozen dataclass -> exporter`
- 长微博 fail-closed
- `extend` / `detail` 双路径安全诊断
- 不再把截断正文静默当完整正文
- 置顶旧微博不污染时间线进度
- `试抓50条` 达标后停止额外翻页
- Tkinter 运行详情展开 / 收起
- 默认紧凑窗口显示修复
- 离线测试、golden、安全脱敏和零运行时依赖审计

---

# 总体产品路线

一句话版本：

> **V7：可靠地把微博拿回来。**
> **V8：把微博保存成可信、可更新、可搜索的个人档案。**
> **V9：让 AI 在这个本地个人档案上工作。**

不要急着堆功能。优先顺序始终是：

1. 数据完整性
2. 可验证失败
3. 可恢复性
4. 普通用户体验
5. 本地长期保存
6. 搜索
7. AI

---

# V7 Alpha3 · UX Polish

目标：

> 把“开发者可用”推进到“普通用户拿到就会用”。

本轮不进行 API / network / long-text / pagination / state machine 核心重构。

## 功能范围

### 1. 导出后的快捷操作

导出成功后提供：

- 打开导出文件
- 打开导出文件夹

原则：

- 使用 Windows 系统能力
- 不引入新运行时依赖
- 不要导出成功后强制自动打开

### 2. 导出内容预设

建议界面：

- 完整归档
- AI 精简
- 自定义

普通用户优先使用预设。

### 3. 自定义输出字段

第一批只考虑：

- 微博来源 / 设备
- 发布位置
- 转发 / 评论 / 点赞数量

不要一下做成十几个底层 checkbox。

### 4. 日期时间

日期时间属于核心归档信息，不建议允许完全关闭。

可选格式：

- `YYYY-MM-DD HH:mm`
- `YYYY-MM-DD`

暂不引入复杂时区系统。

### 5. UI polish

继续保持：

- Tkinter
- Precision Utility / restrained Windows utility
- 日志默认收起
- 状态信息清楚
- 不引入大型 UI 框架

## Alpha3 完成标准

- 默认预设不破坏现有 Markdown golden 语义
- 自定义字段只影响 exporter / rendering 层
- GUI 设置不能渗透到 network / parser 层
- 打开文件 / 文件夹行为在 Windows 真机可用
- 全测试通过
- Windows 真机验收通过

---

# V7 Alpha4 · 不完整档案语义

这是后续非常重要的一轮。

当前状态：implemented / pending Windows acceptance。

Alpha3 accepted baseline 的严格模式中：

> 一条历史转发原微博不可访问，整个任务可能停止。

这保证了不静默造假，但对真正的几万条历史归档并不理想。Alpha4 已按下述
窄范围语义完成实现，仍待 Windows 验收。

## 目标

建立明确的档案完整性状态，例如：

- `COMPLETE`
- `INCOMPLETE`

对于不可访问的历史微博：

- 不把截断正文当完整正文
- 明确记录微博 ID
- 明确记录“全文无法验证 / 当前不可访问”
- 可以保留已知的安全元数据
- 如保留截断预览，必须明确标注“非完整正文”
- 允许整个归档继续
- 最终输出完整性报告

例如：

```text
总计：10382 条
完整：10379 条
不可验证：3 条
```

## 必须保留

仍然坚持：

> 不知道完整，就绝不声称完整。

不要把“继续归档”变成“恢复 V6 的静默截断”。

---

# V7 Alpha5 · 本地母档案

这是整个项目未来最关键的一次结构升级。

## 当前模型

```text
微博 API
   ↓
Markdown
```

## 目标模型

```text
              ┌→ 完整 Markdown
              ├→ AI Compact
微博 API → 本地母档案 ─→ JSONL
              ├→ 搜索
              ├→ 增量更新
              ├→ 完整性报告
              └→ AI / MCP
```

## 推荐技术

优先考虑：

- SQLite
- Python 标准库 `sqlite3`

暂时不要引入服务器数据库。

## 核心原则

**采集和展示彻底分开。**

能抓到的数据，母档案尽量完整保存。

用户是否选择：

- 显示设备
- 显示位置
- 显示转评赞

只决定导出的 Markdown 怎么显示，不应该决定是否保存这些原始可用信息。

这样以后用户改变格式偏好，不需要重新爬微博。

## 母档案应考虑保存

至少包括：

- 微博 ID
- 用户 ID
- 发布时间
- 正文
- 长微博完整性状态
- 转发关系
- 来源 / 设备
- 位置
- 转评赞
- 媒体类型摘要
- 抓取时间
- schema version
- 数据完整性状态

---

# V7 Alpha6 · 增量更新与断点恢复

有了本地母档案后再做这一轮最合理。

## 增量更新

目标：

已有 2010–2026 全量档案后，下个月再次运行：

- 只抓最新一段
- 与已有 ID 去重
- 自动合并
- 不重新抓十几年

## 断点恢复

长任务中断后：

- 不从第一页重新开始
- 使用 checkpoint
- 使用微博 ID / 时间 / overlap window
- 去重合并

明确不要：

> 盲目依赖远端 page number 作为永久断点。

## 完成标准

- 中途中断不损坏已有母档案
- resume 可重复执行
- overlap 不产生重复微博
- 未证明完整的结束不能被标为完整

---

# V7 Beta · Windows 正式测试版

当功能和数据模型稳定以后，再集中解决发行问题。

## 打包方向

建议：

- PyInstaller `onedir`
- no UPX
- no obfuscating packer
- no runtime pip
- no runtime GitHub source download
- no browser automation
- no admin/UAC

## Windows 兼容性测试

Beta 阶段集中测试：

- Windows 10 / 11
- 无 Python 环境
- 普通用户账户
- 中文用户名 / 中文路径
- 100% / 125% / 150% DPI
- 多显示器
- 不同屏幕工作区
- Windows Defender / 常见杀毒软件
- 干净机器首次运行
- 扫码登录
- 长时间全量抓取

## 发布前工程项

- README
- LICENSE
- SECURITY
- THIRD_PARTY_LICENSES
- Release manifest
- SHA256
- secret scan
- GitHub Releases

---

# V7 1.0 · 可信个人档案

1.0 不追求“功能最多”，而追求：

> 用户多年以后仍然知道这个档案是什么、是否完整、是否被改过。

## 每次归档建议生成 manifest

内容例如：

- App version
- Archive schema version
- UID
- Export time
- Requested range
- Pages fetched
- Natural termination status
- Complete count
- Incomplete count
- Earliest / latest verified timeline date
- SHA256

## 发行原则

- 一个版本对应一个固定 build
- 不静默重建同版本二进制
- 条件合适时做 Authenticode 签名
- 不要求管理员权限
- 不建议用户关闭杀毒软件

---

# V8 · 本地可搜索个人微博档案

在母档案成熟后增加本地全文搜索。

## 第一阶段

优先尝试：

- SQLite FTS5
- 不引入向量数据库
- 不引入 embedding 服务

目标功能：

- 关键词搜索
- 时间范围过滤
- 按年份查看
- 按来源 / 地点 /互动量过滤
- 查找转发原文
- 搜索历史观点

## 中文搜索

需要真实微博数据 benchmark。

不要拍脑袋决定 tokenizer。

重点测试：

- 中文连续词组
- 2 字词
- 3+ 字词
- 英文
- URL
- hashtag
- 用户名

如果 FTS5 足够，就不要引入更重的搜索技术。

---

# V9 · AI Personal Archive

只有本地母档案、完整性、增量、搜索都稳定后，再进入这一层。

## 目标

AI 不再每次读取整个 Markdown。

而是：

```text
用户问题
   ↓
本地搜索 / 查询
   ↓
只选相关几十 / 几百条
   ↓
AI 分析
```

例如：

- 我 2012–2026 对房地产观点怎么变化？
- 我什么时候开始频繁讨论 AI？
- 哪几年我的兴趣变化最大？
- 找出我以前反复提到但后来不再提的主题。
- 我的写作风格十年间有什么变化？
- 找出我已经忘记的旧观点。

## 未来接口

可以考虑只读 MCP 工具，例如：

- `search_posts`
- `get_post`
- `posts_between`
- `posts_by_year`
- `timeline_stats`
- `related_posts`

原则：

- 默认只读
- 本地优先
- 不把整库自动上传云端
- 返回带微博 ID / 时间等证据
- AI 结论尽可能能回溯到原始微博

---

# 暂时不要做的事情

这些东西很容易让项目膨胀，现阶段明确排除：

- 大型 UI 框架迁移
- Electron
- 浏览器自动化
- Playwright / Selenium / CDP
- 代理池
- 高并发批量抓取
- 多 UID 批处理平台
- 验证码绕过
- 复杂反爬对抗
- 提前实现多个 API provider
- 向量数据库
- 大型 RAG 框架
- 云端账号系统
- 自动上传微博数据
- 自动更新系统
- arbitrary historical date interval
- 为单个不可访问微博不断增加 fallback

---

# 持续工程原则

## 1. 正确性优先

`unknown -> fail closed`

任何未知：

- schema
- challenge
- HTML
- permission page
- rate-limit
- API behavior

都不能假装成功。

## 2. Git 是历史事实

推荐流程：

```text
baseline
↓
agent implementation
↓
tests
↓
diff review
↓
Windows real-world acceptance
↓
commit
↓
tag milestone
```

Coding agent 不自动 commit。

## 3. 仓库是 AI 之间的共享记忆

长期事实写进：

- `AGENTS.md`
- `docs/PROJECT_STATE.md`
- `docs/DECISIONS.md`
- investigation docs
- Git commits / tags

不要依赖聊天窗口长期保存项目事实。

## 4. 一个迭代只解决一类问题

例如：

- Alpha3 = UX
- Alpha4 = incomplete semantics
- Alpha5 = local archive
- Alpha6 = incremental / resume

不要每轮同时改 GUI + network + exporter + storage。

---

# 推荐优先级

如果资源有限，按以下顺序：

## 必做

1. Alpha3 UX
2. Alpha4 incomplete archive semantics
3. Alpha5 SQLite mother archive
4. Alpha6 incremental / resume
5. Beta packaging / Windows compatibility

## 很值得做

6. Archive manifest / SHA256
7. Local search
8. AI retrieval layer

## 可以很晚再做

9. MCP
10. Semantic embeddings
11. Advanced statistics
12. Multi-provider API abstraction

---

# 最终愿景

项目不应停留在：

> “把微博导出成 Markdown。”

而应该逐步变成：

> **一个可信、本地、长期可保存、可重新导出、可增量更新、可搜索，并最终能让 AI 安全分析个人历史的微博档案系统。**
