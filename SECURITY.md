# Security Model — V7 Alpha 1

## Network

V7 只使用 Python 标准库 HTTPS stack，并使用系统默认 TLS 验证。

项目代码禁止：

```python
verify=False
ssl._create_unverified_context()
```

如果证书验证失败，宁可任务失败，也不降级到不安全连接。

## Secrets

日志和 `last_error.txt` 会脱敏：

- SUB
- SUBP
- SSOLoginState
- X-CSRF-TOKEN
- alt / token-like query values

**Alpha 1 仍沿用 V6 的 `%LOCALAPPDATA%\WeiboTextExporter\cookie.txt` 明文 Cookie，目的是先验证新抓取核心。**

正式 V7 发行前计划迁移为 Windows DPAPI 用户作用域存储；解密失败视为未登录并重新扫码。

不要发送：

```text
%LOCALAPPDATA%\WeiboTextExporter\cookie.txt
```

## Local archive privacy

`v7_cache/<uid>/last_success.json` 是 normalized archive：
- Alpha4 起显式使用 `schema_version: 1`
- 不含 Cookie
- 不含请求头
- 不保存图片/视频 URL
- 不保存 long-text attempt diagnostics、响应正文或完整 query
- 包含微博正文及明确标记的 timeline preview，因此本身属于用户的个人归档数据

无 `schema_version` 的旧缓存属于 legacy/unversioned。当前缓存仍为 write-only；
未来 Alpha5 不得把旧缓存静默解释为可信 mother archive。

请像对待最终 Markdown 一样对待这个缓存文件。

## Privileges

程序不需要管理员权限，不安装服务，不写系统目录，不修改防火墙。

## Telemetry

V7 Alpha 1 没有遥测、统计上传或自动更新检查。
