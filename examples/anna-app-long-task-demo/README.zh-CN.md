# 长任务 Demo(`anna-app-long-task-demo`)

[English](./README.md)

**异步工具 job 通道**(`anna.tools.invokeAsync`,设计文档:matrix-nexus
`docs/design/anna-app-tools-invoke-async-jobs.md`)的官方示例。演示长任务
App 必备的四件事:

1. **`invokeAsyncAwait` + 实时进度** —— 一次调用启动分钟级工具;捆绑的
   `long-task-engine` Executa 每步发一条 `executa/progress` 通知,UI 经
   `onProgress` 渲染进度条。
2. **取消** —— Cancel 按钮触发 `AbortSignal` → `cancelJob`;取消幂等:
   第二次取消返回 `cancelled: false`。
3. **刷新恢复** —— job 进行中刷新窗口:启动时 App 调用
   `listJobs({clientTag: "long-task-demo", state: ["queued","running"]})`
   重新接管在飞 job,并用 `getJob({sinceSeq})` 续上进度。job 状态在
   宿主侧,不在 iframe 里。
4. **同步对照组** —— 同一工具走普通 `tools.invoke` 给 150s 预算:平台把
   等待钳制到 **90s** 并返回结构化 `tool_timeout`,`details` 携带
   `requested_timeout_ms` / `max_timeout_ms`。超过公网边缘对长挂请求的
   容忍度(约 100s),同步结果物理上送不回浏览器 —— 这就是异步通道
   存在的原因。

## 决策表

| 场景 | 通道 |
| --- | --- |
| 工具 < 90s 完成 | `tools.invoke`(最简单) |
| 可能超 90s,或不想阻塞 UI | `tools.invokeAsync` / `invokeAsyncAwait` |
| 需要进度 UI / 取消 / 刷新后恢复 | 只能用 `tools.invokeAsync` 族 |

## 运行

```bash
cd examples/anna-app-long-task-demo
pnpm install          # 或 npm i —— 只装 @anna-ai/cli
npx anna-app dev      # 真实本地运行:插件 + 进程内 job 表
```

- 设置 `steps` / `step_seconds`,点 **Run async**,观察进度条;
- 运行中点 **Cancel**;再跑一次并在中途刷新页面,观察恢复;
- 点同步对照按钮,阅读结构化超时错误;
- `npx anna-app test` 回放 `fixtures/happy-path.jsonl`(同步快路径)。

本地 harness 在进程内实现了完整 job 生命周期(`anna-app-runtime-local >=
0.2.0a20`);与生产唯一差异:harness 重启后 job 不保留(不持久化)。

## 插件侧进度上报(抄进你自己的 Executa)

```python
from executa_sdk import bind_invoke, emit_progress

def handle_invoke(req_id, params):
    with bind_invoke(params):          # 把事件关联到"本次 invoke"
        for i in range(1, total + 1):
            do_step(i)
            emit_progress("tool_update", {"step": i, "total": total})
```

语义(宿主侧,静默丢弃是设计行为):

- `type` ∈ `progress` | `tool_update`(其他值归一为 `progress`;
  终态不可伪造);
- 事件必须携带父 `invoke_id`(`bind_invoke` 内自动);
- 限频 **每 invoke 50 条/秒**,超限丢弃;宿主每条最多存 8KB、保留
  最近 500 条;
- 进度只在**异步** invoke 中流动 —— 普通同步 `tools.invoke` 期间通知被
  丢弃(工具本身照常工作)。

## 长 job 的 token / 配额要点

- 反向 RPC token(storage/image/upload/credentials/sampling)按
  `min(job 时长 + 5min, 1h)` TTL 签发,更长的 job 由 Agent 自动续期
  —— 插件侧无需处理。
- 调用次数配额随 job 时长线性放大(×小时数,封顶 ×10)。
- 每用户活跃 job 配额:5;每 Agent 长 job 并发:3(超限返回
  `long_job_capacity`,可稍后重试)。

## 目录结构

```
app.json                 应用商店元数据 + bundled_executas 句柄映射
manifest.json            ui.host_api.tools: ["required:bundled:long-task-engine"]
bundle/                  static-spa UI(index.html / app.js / style.css)
executas/long-task-engine-python/
  executa.json           dev tool_id + 发布元数据
  long_task_engine_plugin.py   约 200 行的插件(stdio JSON-RPC)
fixtures/happy-path.jsonl      供 `anna-app test` 的同步快路径 fixture
```
