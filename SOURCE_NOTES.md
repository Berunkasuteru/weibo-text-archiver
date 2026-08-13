# V7 Alpha 4 — 外部接口验证备注

程序运行时不会访问 GitHub。本文件仅记录开发阶段核对结果。

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
