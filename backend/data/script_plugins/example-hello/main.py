"""示例脚本插件入口。

入口函数签名：run(ctx) -> Any
ctx 仅暴露 manifest.permissions 声明过的能力，越权调用会抛 PermissionError。
ctx.settings 读取用户在设置页保存的配置（无需额外权限）。
"""


def run(ctx):
    ctx.log("示例插件开始执行")

    # 读取用户在设置页保存的设置
    greeting = ctx.settings.get("greeting", "（未设置问候语）")
    mode = ctx.settings.get("mode", "brief")
    limit = ctx.settings.get("table_limit", 20)
    verbose = ctx.settings.get("verbose", False)
    ctx.log("设置: greeting=%s, mode=%s, table_limit=%s, verbose=%s"
            % (greeting, mode, limit, verbose))
    ctx.log(greeting)

    result = {
        "plugin": ctx.meta["plugin_id"],
        "version": ctx.meta["version"],
        "params": ctx.params,
        "settings": ctx.settings.all(),
    }

    # database.read 权限：只读查询
    if ctx.has_permission("database.read"):
        try:
            rows = ctx.database.query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT ?",
                (int(limit),),
            )
            tables = [r["name"] for r in rows]
            result["tables"] = tables
            ctx.log("读取到 %d 张数据表" % len(tables))
            if verbose:
                for t in tables:
                    ctx.log("  - " + t)
        except Exception as e:
            result["query_error"] = str(e)

    ctx.log("示例插件执行结束")
    return result
