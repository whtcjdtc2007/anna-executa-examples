English version: [persistent-storage.md](persistent-storage.md)

# Anna 持久化存储（APS）

> 让 Executa 插件以**按用户隔离**的方式拥有一份小型、持久的 Key/Value
> 与对象存储——由 Anna 托管，无需自备云存储账号、cookie 或数据库。配额与
> 访问控制由宿主统一执行。

## 为什么需要它？

很多真实插件需要在多次调用之间保留状态：

- **断点续跑**：上次跑到了哪条数据？
- **派生缓存**：图像 X 的 OCR 结果是 …
- **用户拥有的文件**：助手生成的 PDF、合同、报告 …

如果让每个插件自带后端，就必然要分发凭证、自管加密与生命周期、
重新发明配额体系。APS 把这些下沉到 Anna，并给插件一套稳定的、
按 scope 隔离的 JSON-RPC 接口；插件可以保持部署上的「无状态」，
却仍然能记住有用的东西。

## 前置条件

要打通 APS 必须**同时满足**：

1. **协议 v2 协商。** 宿主 `initialize` 时发 `protocolVersion: "2.0"`，
   插件返回相同版本，并在 `client_capabilities.storage = {}` 声明能力。
   v1 插件无法使用 APS。
2. **Manifest 声明。** 插件 `describe` 必须在 `host_capabilities` 中
   列出 `storage.user`（或 `storage.app` / `storage.tool`，取决于
   想访问的 scope）；缺少则 Nexus 返回 `-32008 not_negotiated`。
3. **用户授权。** 终端用户必须在 Anna 管理面板里为该 Executa 开启
   持久化存储，授权会写入
   `UserExecuta.custom_config.storage_grant.scopes = ["user", …]`，
   同时支持 `quotaBytes` / `objectMaxBytes` 覆盖默认配额。未授权时
   Nexus 返回 `-32001 not_granted`。

## 通信协议

`initialize` 完成后，插件即可在既有 Executa 通道上发起反向 JSON-RPC，
每个调用必须携带 Matrix Agent 签发的 `storage_token`：

```json
{
  "jsonrpc": "2.0",
  "id": 12,
  "method": "storage/kv_set",
  "params": {
    "scope": "user",
    "key": "lastRun/cursor",
    "value": { "page": 7, "ts": "2026-05-01T11:22:33Z" },
    "ttl_seconds": 86400,
    "storage_token": "eyJ…"
  }
}
```

### 方法

| 方法                         | 用途                                       |
|------------------------------|--------------------------------------------|
| `storage/kv_get`             | 按 key 读取一个 JSON 值。                  |
| `storage/kv_set`             | 写入 JSON 值（默认 ≤ 64 KB）。             |
| `storage/kv_delete`          | 软删除，7 天内可恢复。                     |
| `storage/kv_list`            | 按前缀分页列举。                           |
| `storage/files_upload_init`  | 取得 presigned PUT URL。                   |
| `storage/files_finalize`     | PUT 成功后落库。                           |
| `storage/files_download_url` | 签发短期 GET URL。                         |
| `storage/files_list`         | 按 path 前缀列举对象。                     |
| `storage/files_delete`       | 软删除对象。                               |
| `storage/quota_status`       | 查询用户已用 / 剩余字节数。                |

### Scopes

| Scope   | Owner               | 可见性                                    |
|---------|---------------------|-------------------------------------------|
| `user`  | 终端用户            | 用户自己仪表盘 + 所有他授权过的插件。      |
| `app`   | Anna App 包         | 同一用户对该 App 的所有安装实例共享。      |
| `tool`  | Executa 插件本身    | 严格隔离到 (user × executa) 单元。         |

`user` 是最强、也最需要克制的 scope。临时状态请默认 `tool`，
只有在跨工具复用对用户**明显有价值**（例如保存生成的 PDF 到
"My Files"）时才申请 `user`。

## 错误码

APS 的错误统一为 JSON-RPC error，并带稳定的 `errorCode`：

| `errorCode`           | 含义                                              |
|-----------------------|---------------------------------------------------|
| `not_granted`         | 用户尚未为该 Executa 开启存储授权。               |
| `not_negotiated`      | 插件未在 v2 中声明 storage 能力。                 |
| `not_available`       | Anna 已全局关闭 APS（灰度阶段）。                 |
| `forbidden_scope`     | Token 未授权请求中的 scope。                      |
| `not_found`           | key/path 不存在。                                 |
| `precondition_failed` | `if_match` ETag 与当前 generation 不一致。        |
| `value_too_large`     | KV 值超过单次上限。                               |
| `quota_exceeded`      | 用户配额耗尽。                                    |
| `rate_limited`        | 单次 invoke 内写/读次数超限（默认 5 / 20）。      |
| `upstream`            | 与 Nexus 通信失败。                               |

插件在同一次 invoke 内 SHOULD 把 `quota_exceeded` 与
`rate_limited` 视为**不可重试**；`upstream` 可指数退避后重试。

## 最佳实践

1. **优先使用 `tool` scope**，除非数据真的属于用户。
2. **单值要小**：APS 是 JSON 而不是 blob 存储；KB 以上请走
   `files_upload_init`。
3. **覆盖写要带 `if_match`**，避免丢失更新。
4. **缓存型数据要设 TTL**，否则会撑爆配额。
5. **元信息编码进 key**：`notes/{noteId}` 可被 `kv_list` 检索，
   而 JSON 内嵌字段不行。
6. **始终以服务端响应为准**：拿 `kv_set` / `files_finalize` 返回的
   `etag` 作为下一轮 `if_match` 的来源。

## 示例：缓存 OCR 结果

```ts
// 1. 先查缓存
const got = await rpc("storage/kv_get", {
  scope: "tool",
  key: `ocr/${sha}`,
  storage_token,
});

if (got.found) return got.value;

// 2. 调用昂贵的 OCR
const text = await runOCR(imageBytes);

// 3. 缓存 30 天
await rpc("storage/kv_set", {
  scope: "tool",
  key: `ocr/${sha}`,
  value: { text, ranAt: Date.now() },
  ttl_seconds: 30 * 24 * 3600,
  storage_token,
});

return text;
```

## 内置 user_storage_* 工具

对于 LangChain 智能体，Nexus 默认在 `user_storage_*` 命名空间下注册
6 个高层封装：`user_storage_get` / `user_storage_set` /
`user_storage_delete` / `user_storage_list` /
`user_storage_files_save_text` / `user_storage_files_get_url`。
智能体不会接触原始 RPC；该工具会施加 5 写 / 20 读 / 单次 invoke 的
软预算，并返回与上文一致的 JSON 信封。

用户可在账户设置中通过 `UserSettings.disable_user_storage_tools`
关闭这些内置工具。

## 参考

- [matrix-nexus 设计文档](https://github.com/talentai/matrix-nexus/blob/main/docs/design/anna-persistent-storage.md)
- [protocol-spec.zh-CN.md](protocol-spec.zh-CN.md) — JSON-RPC 信封全集。
- [authorization.zh-CN.md](authorization.zh-CN.md) — 运行期授权流程。
- [`examples/python/storage-notebook/`](../examples/python/storage-notebook/) — 可直接运行的 Python 插件示例，演示反向 `storage/*` + `files/*`（带 ETag 乐观重试的 KV 写入，以及使用宿主预签名 URL 的两步式对象上传）。
