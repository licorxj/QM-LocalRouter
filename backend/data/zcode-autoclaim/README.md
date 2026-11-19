# ZCode 赠送 token —— 主动触发的一次性领取

针对 `D:\Program files\ZCode\ZCode.exe`（v1.6.0，Electron，服务域 `https://zcode.z.ai`）反向分析后写的脚本。

**它是一次性的**：不常驻、不轮询，什么时候跑由你决定；跑完就退出。
**它不发模拟请求**：脚本自身不访问任何 HTTP 接口、不读 token、不碰验证码，
刷新投放 / 验证码 / `claim` / 上报全部由 ZCode 原生组件完成。

---

## 为什么不能「一个 HTTP 请求直领」

`POST /api/v1/zcode-plan/billing/claim` 强制要求请求头 `X-Aliyun-Captcha-Verify-Param`，
这个参数只能由客户端里的阿里云验证码 SDK 生成：

```js
// styles-BhvI6_DD.js 里点「领取」的原生流程
let t = await wRt(n.codingPlanSubscriptionService, s, () => l('verifying'));
if (!t) return { status: 'cancelled', message: g.formatMessage({ id: 'manualClaimPlan.claim.failure.captcha' }) };
let r = await n.codingPlanSubscriptionService.claimManualPlan({ planId: e.args.plan_id, ...t });
```

`wRt()` 拿不到 `captchaVerifyParam` 就直接返回 `cancelled`；服务端对无效/复用参数返 `3007`，
同一个 `certifyId` 复用还会触发 F008 重复提交检测。而且本地 `credentials.json` 里的 token 是
`enc:v2:os:` 加密的，脚本也解不开。

所以能自动化的只有一件事：**在官方客户端里点那一下**。脚本做的就是这件事——
按下 UI 上真实绑定的那个回调，剩下的交给 ZCode。

---

## 快速开始

要求：Node ≥ 22（内置 `WebSocket` / `fetch`，零 npm 依赖）。

1. **完全退出 ZCode**（含托盘），然后：

   ```
   start-debug.cmd 9222
   ```

   等价于：

   ```
   "D:\Program files\ZCode\ZCode.exe" --remote-debugging-port=9222 --remote-allow-origins=*
   ```

   > 打包版默认不开调试端口；单实例锁会吞掉第二个实例的参数，必须先退干净再带参数启动。

2. **想领的时候跑一次**：

   ```
   node claim-now.mjs
   ```

   其它用法：

   ```
   node claim-now.mjs --wait 30        # 刷新后最多等 30s 让投放出现
   node claim-now.mjs --no-refresh     # 不触发刷新，只处理当前界面上的东西
   node claim-now.mjs --any            # 放宽横幅文案判断（默认只点含“领取”的横幅）
   node claim-now.mjs --dry-run        # 只报告将要点的目标，不点
   node claim-now.mjs --json           # 输出机器可读结果
   ```

3. 打开 ZCode 就能看到结果：票券弹窗（banner 型活动）或额度变化（popup 型活动）。

---

## 一次运行做四件事

1. **让客户端自己刷新投放**
   派发一次 `visibilitychange`。客户端里营销投放的 poller 正是监听这个事件：

   ```js
   document.addEventListener('visibilitychange', () => { poller.visibilityChanged(); controller.showPending(); });
   ```

   → 客户端自己发 `GET /api/v1/marketing/touch`（生产环境默认 10 分钟才拉一次，这一枪只是让它提前）。
   如果窗口最小化，客户端会认为不可见而不拉，所以这一步会**临时**把 `document.visibilityState`
   伪装成 `visible`，脚本结束前还原（只影响本页面，不落盘、不改配置）。

2. **事件驱动地等原生 UI**
   用 `MutationObserver` 等两个稳定标记之一出现，不轮询、不 sleep 轮转：

   | 目标 | 选择器（来自反编译） |
   |---|---|
   | 赠送横幅 | `[data-testid="marketing-banner"]` |
   | 横幅主按钮 | `[data-testid="marketing-banner"] > button[aria-label]` |
   | 赠送/票券弹窗 | `[data-testid="cloud-content-dialog"]` |
   | 弹窗按钮 | `button[data-testid="cloud-dialog-action"][data-action-id="button-N"]` |
   | 领取失败提示 | `[data-testid="marketing-failure-dialog"]` |
   | 阿里云验证码 | `.aliyunCaptcha-mask, .aliyunCaptcha-window` |

3. **调原生 handler**
   优先直接调用该元素 React props 上的 `onClick`（`__reactProps$…`），也就是 UI 里
   那个按钮真正绑定的回调；拿不到 props 才退回 `el.click()`。脚本不会自己拼请求。

4. **认结果**
   - 失败弹窗 / 文案里 `领取成功…` → 直接判定；
   - 票券弹窗（`success_popup`，标题由服务端下发，可能不含“领取成功”）→ 判定成功；
   - 最后用客户端自己的日志兜底：`%USERPROFILE%\.zcode\v2\logs\YYYY-MM-DD.log`
     里的 `[rpc:call] coding-plan-subscription.claimManualPlan OK`。

判定「该不该点」看文案：命中 `领取 / 領取 / claim / 受け取 / получ…` 才点
（`--any` 只放宽横幅这一层）；`开始体验 / 查看套餐 / 复制分享 / 知道了 / 关闭`
一律不点——票券弹窗里就是这些。

---

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--port` | 9222 | CDP 端口 |
| `--wait` | 20 | 刷新后等投放出现的秒数（这一次运行内） |
| `--settle` | 25 | 点击后等结果的秒数 |
| `--captcha-wait` | 120 | 需要人工过验证码时的等待秒数 |
| `--no-refresh` | 关 | 不触发客户端刷新，只处理当前界面 |
| `--any` | 关 | 放宽横幅文案判断（弹窗仍只认“领取”类按钮） |
| `--dry-run` | 关 | 只打印将要点的目标 |
| `--json` | 关 | stdout 输出 JSON 结果（日志走 stderr） |
| `--click` | 关 | 用 `el.click()` 代替原生 `onClick` |
| `--profile` | 空 | 隔离 profile 根目录；只用于定位该实例的 `.zcode\v2\logs`，不读凭据 |
| `--log-dir` | 空 | 显式指定客户端日志目录，优先级高于 `--profile` |

## 账号隔离：每个 profile 一个账号

ZCode 的数据路径不是写死的，静态分析里确认了这些变量：

| 环境变量 | 作用 |
|---|---|
| `ZCODE_DATA_BASE_DIR` | `.zcode` 数据根，决定 `credentials.json` / `setting.json` / `telemetry-state.json` / `logs` |
| `HOME` / `USERPROFILE` | 影响 home、日志目录和部分宿主进程路径 |
| `ZCODE_DESKTOP_HOME_DIR` | Electron `app.setPath('home', …)` |
| `ZCODE_DESKTOP_USER_DATA_DIR` | Electron `app.setPath('userData', …)`，影响单实例锁、Chromium 缓存 |
| `ZCODE_DESKTOP_SESSION_DATA_DIR` | Electron `app.setPath('sessionData', …)` |
| `ZCODE_DESKTOP_APPLICATION_NAME` | 应用名，进一步避免不同 profile 抢同名 userData / 锁 |
| `--remote-debugging-port` | 每个实例必须不同 |

配套脚本：`start-isolated.cmd`。

```
:: 账号 A：全新 profile，端口 9223
start-isolated.cmd acct_a 9223

:: 账号 B：另一个 profile，端口 9224
start-isolated.cmd acct_b 9224

:: 想给哪个账号领，就连哪个端口、用哪个 profile 日志
node claim-now.mjs --port 9223 --profile D:\zcode-profiles\acct_a
node claim-now.mjs --port 9224 --profile D:\zcode-profiles\acct_b
```

`start-isolated.cmd` 会为每个 profile 建立：

```
D:\zcode-profiles\acct_a\
  .zcode\v2\...                 ZCode 自己的凭据/设置/deviceMid/logs
  home\                       隔离 HOME
  user\                       隔离 USERPROFILE
  electron-user-data\         Electron userData / 单实例锁 / 缓存
  electron-session-data\      Electron sessionData
```

**第一次启动每个 profile 都是空账号**：需要在对应窗口里手动登录一次。之后领取仍然完全由该窗口里的 ZCode 原生组件完成；`claim-now.mjs` 只通过 CDP 点这个窗口里的原生 UI，不搬 token、不合成 HTTP 请求。

限制：

- 不同 profile 必须用不同 `--remote-debugging-port`，否则端口冲突 / 连错实例。
- `ZCODE_DATA_BASE_DIR` 只隔离文件路径；`enc:v2:os:` 凭据的密钥仍来自系统 keyring。脚本给 `ZCODE_DESKTOP_APPLICATION_NAME` 设了不同名字，且 keyring 条目按凭据文件路径派生服务/账号，正常会隔离；若系统 keyring 策略特殊，最稳的是分别用不同 Windows 用户或容器。
- 不要并发给同一活动重复提交；也不要拿隔离去规避官方活动资格/频率限制，是否允许取决于活动条款。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 领取成功（弹窗或客户端日志确认） |
| 1 | 当前没有可领投放（活动没在投 / 已领过 / 窗口不可见） |
| 2 | 领取失败（终端错误，如已领过、名额用尽、不符合条件） |
| 3 | 需要人工完成阿里云验证码 |
| 4 | 环境错误（连不上调试端口、找不到页面等） |
| 5 | 结果未确认，请到 设置 → 用量 / 套餐 核对 |

## 常见现象

| 现象 | 含义 | 处理 |
|---|---|---|
| `当前没有可领的赠送投放` | 此刻没在投 / 这段 campaign 已确认过 | 过一会儿再跑一次，或先在 ZCode 里看看有没有横幅 |
| `⚠ 需要你在 ZCode 窗口里完成验证码` | 这次不是静默通过 | 去窗口里点一下，脚本会等你（默认 120s） |
| `✗ 领取失败：该套餐已经领取过` | 终态 | 不用再跑 |
| `✗ 领取失败：…名额已用完 / 活动已结束` | 终态 | 不用再跑 |
| `结果未确认` | popup 型活动没有票券弹窗，日志也没抓到 | 去 设置 → 用量 看 token 是否到账 |

## 注意

- 只对**你自己的账号**用；自动领取是否符合活动条款，需要你自己确认。
- 不要并发多开：客户端对同一 `certifyId` 有重复提交检测。
- 脚本不改配置、不写缓存、不碰 `credentials.json`；唯一副作用是运行期间临时伪装一次
  `visibilityState`，退出前还原。
- 如果 ZCode 升级后选择器变了，用 `--dry-run` 把输出贴出来即可定位（选择器集中在
  `claim-now.mjs` 的 `HELPERS` 里）。
