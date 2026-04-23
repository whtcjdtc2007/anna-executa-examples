# 多文件二进制插件示例 — Python + PyInstaller --onedir

本示例演示如何让 Executa 二进制插件**自带**配套文件：捆绑的 `.so` /
`.dylib`、子工具、配置数据等。Anna Agent v2 通过结构化的安装目录
和运行时环境变量注入来支持这一点 —— 你的二进制能找到自己的伙伴文件。

> 想要纯单文件二进制？参考 `../python/`（PyInstaller `--onefile`）。

## 运行时看到的目录布局

Agent 安装你的归档后，磁盘布局如下：

    ~/.anna/executa/
      bin/example-multifile-tool          → tools/{tool_id}/current/bin/example-multifile-tool
      tools/{tool_id}/
        v1.0.0/                            ← 每次安装独立版本目录
          bin/example-multifile-tool       ← 入口
          lib/                              ← .so / .dylib 在这里
          data/greeting.txt                 ← 捆绑数据
          manifest.json
          INSTALL.json                      ← 自动写入的安装元数据
        current  → v1.0.0                  ← 蓝绿原子升级指针

Agent 在调用入口前注入这些环境变量：

| 变量                   | 值                                               |
|------------------------|--------------------------------------------------|
| `EXECUTA_HOME`         | `tools/{tool_id}/current/` 的绝对路径            |
| `EXECUTA_DATA`         | `${EXECUTA_HOME}/data`（存在时）                 |
| `LD_LIBRARY_PATH`      | 前置追加 `${EXECUTA_HOME}/lib`（Linux）          |
| `DYLD_LIBRARY_PATH`    | 前置追加 `${EXECUTA_HOME}/lib`（macOS）          |
| `PATH`                 | 前置追加 `${EXECUTA_HOME}/share/bin`（如有）     |
| 工作目录               | `EXECUTA_HOME`                                   |

## 必须：在 `manifest.json` 声明入口

多文件归档没有显然的"主"文件，必须告诉 Agent 启动哪个：

```json
{
  "runtime": {
    "binary": {
      "entrypoint": "bin/example-multifile-tool",
      "lib_dirs": ["lib"],
      "data_dirs": ["data"]
    }
  }
}
```

若 `manifest.json` 缺失，会回退到 Nexus `binary_urls.{platform}.entrypoint`；
最后兜底是归档里唯一/第一个可执行文件（并打印警告）。

## 构建

```bash
pip install pyinstaller
./build.sh
```

产物为 `dist-anna/example-multifile-tool-${platform}.tar.gz`，并打印
sha256、size，可直接填到 `binary_urls`。

## 在 Nexus 注册

Anna Nexus 编辑 Executa 时，展开"多平台二进制下载 URLs"，点"添加平台"，
填 URL，再点旁边的 **▾** 展开高级字段填 sha256/入口：

```json
{
  "darwin-arm64": {
    "url": "https://your-cdn.example.com/example-multifile-tool-darwin-arm64.tar.gz",
    "sha256": "<build.sh 输出>",
    "size": 12345678,
    "entrypoint": "bin/example-multifile-tool",
    "format": "tar.gz"
  },
  "linux-x86_64": { "url": "https://...", "sha256": "..." }
}
```

## 本地验证

```bash
python plugin.py <<< '{"jsonrpc":"2.0","id":1,"method":"describe"}'
python plugin.py <<< '{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"name":"describe_layout"}}'
```

`describe_layout` 工具会返回运行时视角下的 `EXECUTA_HOME`、
`EXECUTA_DATA`、库路径环境变量、`lib/` 和 `data/` 是否可见 —— 对排查
打包问题很有用。

## 不上传任何地方就能装到 Agent —— `distribution_type: local`

开发自测时不必先 push 到 GitHub Releases。**Local** 分发走与 Binary
**完全相同的 v2 安装管线** —— 解压 → `tools/{tool_id}/v{version}/` →
原子切 `current` 软链 → `bin/{name}` shim ——只是 archive 直接从你
Agent 主机的文件系统读取。

`./build.sh` 之后，在 Anna Nexus UI 这样注册：

| 字段              | 值                                                                          |
|-------------------|-----------------------------------------------------------------------------|
| Distribution Type | `local`                                                                     |
| Local Archive Path | `/abs/path/to/dist-anna/example-multifile-tool-${platform}.tar.gz`         |
| Executable Name   | `example-multifile-tool`（可选；默认从 archive 名推导）                     |
| Version           | `dev`（或任意字符串 — 落到 `tools/{tool_id}/vdev/`）                        |

点 Agent 上的 **Install**，上面描述的多文件 layout 就被原样物化出来 ——
`lib/`、`data/`、`bin/`、env-var 注入、原子升级、GC 全都生效。
**运行时与通过 `binary` 安装的没有任何区别**，区别只在 archive 来源
（本地路径 vs HTTPS URL）。

> [!NOTE]
> `local` **跳过** sha256 / size 校验（archive 在你自己的机器上）。
> 准备发布时切换到 `binary`。

## 原子升级与回滚

每次安装落在独立的 `tools/{tool_id}/v{version}/`。Agent 最后一步才
原子重写 `current` 软链 —— 调用进行中时仍读取旧版本。旧版本按
`EXECUTA_KEEP_VERSIONS`（默认保留 2 个）回收。

v2 安装若失败，Agent 保留原 `current` 指向 —— 重跑 `install` 是
安全幂等的。
