# 移动桥示例（`anna-app-mobile-demo`）

[English](./README.md)

**Anna App 移动原生桥**（`anna.mobile.*`，移动运行时 Phase 3）与双 entry
（`mobile_entry`）机制的参照实现。

> **先读这条**：响应式**单 entry** 是推荐路径（见 `finder` / `inbox-app`）。
> `mobile_entry` 是重 UI App 的逃生舱；本示例刻意使用它来演示机制本身，
> 不构成背书。

## 演示内容

| 能力面 | 位置 |
| --- | --- |
| 声明桥（`ui.host_api.mobile`）+ `ui.form_factors` | `manifest.json` |
| `camera_capture` → 预览 → `share({data_url})`，关键交互配 `haptics` | `bundle/mobile.js` |
| `window.open_view` 子视图仍加载**移动** entry（`mobile_entry` 继承锚点） | `bundle/detail-mobile.html` 与 `bundle/detail.html` 的徽标对比 |
| 桌面端 `unsupported_container` 干净降级 | `bundle/desktop.js` |
| 本地开发 `dev.mocks["mobile.*"]` | `manifest.json` `dev` 块 |

## 错误码分层（务必记住）

| 错误码 | 含义 | App 应对 |
| --- | --- | --- |
| `unsupported_container` | grant 正确但容器不是 anna-mobile shell | 渲染降级提示，绝不重试 |
| `permission_denied` | manifest 未声明该方法 | 修 manifest |
| `permission_denied_by_user` | 系统相机权限被拒 | 引导系统设置 |
| `cancelled` | 用户取消拍摄 | 正常结果 |
| `too_large` | 重压缩一次后仍超 `max_bytes` | 降低 quality |
| `mobile_bridge_timeout` | 原生层 30 秒未响应 | 提供重试 |

`share` 用户取消**不是错误** —— 解析为 `{shared: false}`。

## 本地运行

```bash
anna-app validate   # 需 CLI ≥ 0.1.51（schema bundle 0.22.0）
anna-app dev        # dev.mocks 让全流程无真机可跑
```

mock 形状逐字节抄自真实 wire 契约。编辑 `manifest.json → dev.mocks` 可演练
`{shared: false}` / `cancelled` / `too_large` 路径。

## 真机验收走查

1. anna-mobile Launcher 安装并打开 → 必须显示 **MOBILE ENTRY** 徽标（双 entry 生效）。
2. 拍照 → 预览渲染 `width×height · bytes`，EXIF 已剥离。
3. 打开拍摄详情 → 子窗口必须显示 **MOBILE ENTRY**
   （mobile_entry 继承锚点，nexus ≥ 1.1.0-beta.144）。
4. 分享照片 → 系统分享面板；取消面板显示 `shared: false`（非错误）。
5. 触觉实验室 → 七个按钮体感可区分。
6. 桌面 dashboard 打开同一 App → 三个能力按钮均渲染
   `unsupported_container` 降级提示。

## 刻意不演示

`push_token`（通知管线未设计，递延）、主题传播（SDK 协议未定）、
App 内购买（IAP 设计前禁止）。
