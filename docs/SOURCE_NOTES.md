# V7 Alpha 4 — 外部接口验证备注

程序每次启动会在后台执行一次受限的、未认证的 GitHub latest-release 检查，用于比较稳定版本号；不发送微博账号、凭据或归档内容，失败时静默忽略，不自动下载代码或安装更新。本文件其余内容记录开发阶段的接口核对证据。

## 2026-09-17 图片接口边界

图片元数据仍来自 `m.weibo.cn` 时间线。普通图片、GIF 和 Live Photo 静态候选使用响应明确提供的地址；实际资源由独立客户端向 `wxN.sinaimg.cn` 发起 HTTPS GET，使用正常固定微博 Referer，不携带微博 Cookie。

返回槽位可能少于声明数量，必须报告枚举缺口。此证据不证明全部历史图片、上传原始质量或地址永久有效；不推导 CDN URL，也不下载视频部分。

## 2026-08-13 核对

当前 dataabc/weibo-crawler master 仍使用：
- 用户资料：`m.weibo.cn/api/container/getIndex` + `containerid=100505<uid>`
- 扩展资料：`containerid=230283<uid>_-_INFO`
- 微博列表：`containerid=230413<uid>` + `page` + `count`

当前 weibo-cli 的二维码登录仍采用：
`passport.weibo.com/sso/signin`
→ `/sso/v2/qrcode/image`
→ `/sso/v2/qrcode/check`
→ cross-domain session cookies。

V7 长微博策略：
1. 优先 `m.weibo.cn/statuses/extend?id=<id>`
2. 回退 `m.weibo.cn/detail/<id>` 的嵌入 status JSON
3. 两条都严格分类为 `no_view_permission_html`：当前节点显式标记为
   `INCOMPLETE`，timeline preview 只作为未经验证的预览保存。
4. 其他混合、未知、网络、认证、结构或 ID mismatch 结果：明确失败，
   不保存截断正文。

这些属于微博网页内部接口，并非承诺稳定的正式开放 API。
因此 V7 默认 fail closed：未知结构、验证页、限制响应都不会被当作“自然抓完”。
