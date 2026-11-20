# 脚本插件本地存储目录

本目录存放**脚本插件**（独立于 `backend/data/plugins/` 下的 echobird 二进制账号插件）。

## 目录结构

```
script_plugins/
  installed.json                       已安装插件记录（由后端自动维护）
  audit.jsonl                          执行/授权审计日志
  packages/<plugin_id>/<version>/      已安装插件包（manifest.json + 入口 .py）
  staging/<token>/                     下载解压临时区
  example-hello/                       内置示例插件（源码，非安装态）
    manifest.json
    main.py
```

## 插件包约定（zip）

- `manifest.json`：id / name / description / version / entry / function / permissions
- 入口脚本：`def run(ctx) -> Any`
- 权限（`permissions` 可选值）：
  - `credentials.read` 读取账号凭证
  - `database.read` 只读数据库
  - `database.write` 写数据库
- 在线目录：`ApiRouteManeger/plugins/index.json`（COS），客户端日常请求该文件即可获知有哪些插件及下载地址。

## 安全说明

插件由用户主动下载、主动执行，属于本地受信代码；权限是能力注入 + 审计，不是 Python 沙箱。请勿运行来源不明的插件。
