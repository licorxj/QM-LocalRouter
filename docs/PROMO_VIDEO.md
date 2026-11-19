# LocalRouter 宣发视频策划（Remotion 实现）

> 目标：一支以代码动画（Remotion）为主的 90 秒产品宣发视频 + 各平台竖版/短版裁剪，
> 突出两大招牌能力：**🆓 免费模型聚合** 与 **🧩 本地 CLI 反代**。
> 本文档 = 分镜脚本 + 视觉规范 + Remotion 实现规格 + 全平台宣发文案，可直接照做。

---

## 1. 视频概览

| 项 | 规格 |
|---|---|
| 主片 | `PromoMain` — 1920×1080 / 30fps / 约 90s（B站、YouTube、视频号横版） |
| 竖版 | `PromoVertical` — 1080×1920 / 30fps / 约 45s（抖音、快手、小红书、视频号竖版） |
| 语言 | 中文旁白 + 中文字幕（字幕始终烧录，静音刷视频也能看懂） |
| 基调 | 科技感、快节奏、真实 UI 背书 —— "不是 PPT，是能跑的东西" |
| CTA | GitHub 仓库地址 + "Star 支持" |

## 2. 视觉规范（与「暗夜路由」UI 一致）

| Token | 值 | 用途 |
|---|---|---|
| 画布底色 | `#070D18`（深海军蓝） | 全片背景 |
| 主色 | `#F87720`（橙） | 强调、进度、Logo 光效 |
| 点缀 | `#22D3EE`（青）/ `#3B82F6`（蓝） | 数据流、次级强调 |
| 成功/警示 | `#34D399` / `#FBBF24` | 状态点、徽章 |
| 文本 | `#E2E8F0` 主 / `#64748B` 次 | 标题用 600-700 字重 |
| 等宽字体 | JetBrains Mono / Cascadia | 终端、代码、模型 ID |
| 动效语言 | 缓出为主（`Easing.out(Easing.cubic)`），入场 0.3-0.5s，元素逐个 stagger 0.04-0.08s | 与前端 `.animate-in` 一致 |

**画面原则**：真实 UI 截图为主角（截图上做 Ken-Burns 缓推/高亮框），代码动画负责"讲原理"
（路由流动画、终端打字、计数器）。禁止素材网站 stock 视频。

## 3. 资产清单

已有截图（`docs/images/`，Remotion 中用 `staticFile()` 引用或复制到 `remotion/public/`）：

| 资产 | 用途场景 |
|---|---|
| `dashboard.png` / `dashboard-light.png` | 开场产品亮相 / 双主题一闪 |
| `free-models.png` | 场景 C 免费模型聚合 |
| `providers.png` / `providers-light.png` | 场景 C 补充（$0 模型列表） |
| `cli-import.png` / `cli-detect.png` | 场景 D CLI 反代 |
| `chat.png` | 场景 D 收尾（CLI 免费模型真实对话） |
| `sync-models.png` | 场景 C（74 个模型批量导入） |
| `strategies.png` / `logs.png` / `endpoints.png` | 场景 E 智能路由 / 可观测性 |

**建议补录 2 段屏幕录像**（增强真实感，非必需）：
1. 浏览器里点「免费模型合集 → 加入」的完整操作（10s，1440p）
2. 终端里 `curl http://127.0.0.1:12002/v1/chat/completions` 返回流式响应（10s）
   —— 录屏可在 Remotion 里作为 `<OffthreadVideo>` 嵌入并配进度条装饰

## 4. 分镜脚本（主片 90s）

| # | 时间 | 画面 | 动效 | 屏幕字幕（大字） | 旁白 |
|---|---|---|---|---|---|
| S1 钩子 | 0-6s | 黑底，6 个平台图标碎片飞入又散开，中央出现乱麻线条 | 图标 stagger 飞入→抖动散开，线条 stroke-dash 画出 | 65+ 个 AI 平台<br>65+ 套 Key<br>1 个你 | 每家大模型平台一套 SDK、一套 Key、一套故障处理…… |
| S2 亮相 | 6-14s | Logo 从光点聚合（复用图标动效），随后 `dashboard.png` Ken-Burns 推近 | 光点汇聚→Logo scale-in→截图淡入上移 | LocalRouter<br>你的本地 API 路由管家 | LocalRouter——一个端点，接管所有大模型 API。 |
| C1 免费聚合 | 14-30s | `free-models.png` 推近到渠道卡片；右侧代码动画：`+23 models · $0` 计数器滚动；`sync-models.png` 一闪 | 卡片高亮框沿「加入」按钮移动；计数器 CountUp；橙/青粒子 | 免费模型聚合<br>20+ 个 $0 模型 · 一键加入 | 先别花钱——公开免费网关渠道，一键聚合 20 多个 $0 模型，和付费 Key 混在同一个策略池里。 |
| C2 CLI 反代 | 30-48s | 左：`cli-import.png` + `cli-detect.png` 左右交替；右：终端逐字打出 curl 命令→流式 JSON 响应滚动 | TerminalTyper 逐字 + 光标闪烁；响应 JSON 滚动高亮 | 本地 CLI 反代<br>CLI 免费额度 → OpenAI API | 更狠的是：本地 CLI 工具的免费额度，也能被反代成标准 OpenAI 接口。OpenCode、Cline、ZCode……导入凭据自动续期，你的任何 SDK 直接调用。 |
| C3 智能路由 | 48-64s | **纯代码动画（全片高光）**：客户端→路由节点→三条上游光路，中间橙色主路亮起；故障时红光闪→0.3s 内切换备用路 | RequestDot 沿 path 循环流动（`useCurrentFrame` 插值），故障转移红色脉冲 | 5 种负载均衡策略<br>故障自动转移 · <1ms | 五种负载均衡策略，请求按权重轮询分发；某条上游挂了，0.3 秒内自动切到备用——客户端毫无感知。 |
| C4 统一端点 | 64-74s | `endpoints.png` 顶部一角；三个协议徽章 OpenAI/Claude/Gemini 变形互换 | 徽章 morph：同一路径颜色切换 | OpenAI / Claude / Gemini<br>协议自动互转 | OpenAI、Claude、Gemini 协议自动互转——任何 SDK，调任何模型。 |
| C5 本地&LAN | 74-82s | `dashboard-light.png` 一闪（双主题）；局域网拓扑小动画（3 设备→中心节点） | 主题切换闪切；拓扑连线依次点亮 | 纯本地运行 · Key 加密存储<br>局域网共享一个接入点 | 纯本地运行，Key 加密存储；开一个局域网开关，全家设备共享同一个接入点。 |
| S3 CTA | 82-90s | Logo + 仓库名大字 + Star 图标弹跳；底部字幕条 | scale-in + glow 脉冲 | 开源 MIT<br>github.com/licorxj/QM-LocalRouter | 开源免费，MIT 协议。地址在简介，先 Star 再白嫖。 |

**竖版 45s 裁剪**：S1(4s) → S2(6s) → C1(10s) → C2(14s) → C3 压缩(6s) → CTA(5s)。
竖版所有排版用上下结构（大字幕置顶部 1/4 与底部 1/4，中部留给画面），截图卡宽度 92%。

## 5. Remotion 实现规格

### 5.1 环境与命令

```bash
# 在仓库根目录
npx create-video@latest promo --blank   # 或手动搭建，见 5.2
cd promo && npm i
npx remotion studio                     # 本地预览/调参
npx remotion render PromoMain out/promo-main.mp4 --codec=h264 --crf=18
npx remotion render PromoVertical out/promo-vertical.mp4 --scale=1
# 平台二压（抖音/B站推荐）
ffmpeg -i out/promo-main.mp4 -c:v libx264 -crf 20 -preset slow -c:a aac -b:a 192k -movflags +faststart out/promo-main-final.mp4
```

### 5.2 项目结构

```
promo/
├── remotion.config.ts          # imageFormat=jpeg, overwrite
├── public/                     # 从 docs/images/ 拷入的全部截图 + logo.png + bgm.mp3 + vo/*.mp3
├── src/
│   ├── Root.tsx                # <Composition> 注册表
│   ├── theme.ts                # 色板/字体/缓动常量（§2 的表）
│   ├── captions.ts             # 旁白文本 + 时间轴（数据驱动字幕）
│   ├── components/
│   │   ├── ScreenshotCard.tsx  # 截图卡：圆角+描边+KenBurns+高亮框
│   │   ├── TerminalTyper.tsx   # 终端打字机 + 流式 JSON
│   │   ├── RouteFlowDiagram.tsx# 路由流动画（全片高光）
│   │   ├── CountUp.tsx         # 数字滚动
│   │   ├── CaptionBar.tsx      # 底部大字幕（描边+阴影）
│   │   └── LogoReveal.tsx      # 光点聚合 Logo
│   └── scenes/                 # S1..S3/C1..C5 每场一个组件，拼进主 Composition
└── package.json
```

### 5.3 Compositions

| id | 尺寸 | fps | 时长(帧) | 说明 |
|---|---|---|---|---|
| `PromoMain` | 1920×1080 | 30 | 2700 (90s) | 主片 |
| `PromoVertical` | 1080×1920 | 30 | 1350 (45s) | 竖版短片 |
| `PromoCover` | 1280×720 | 30 | 1 | 封面图（B站封面用） |

### 5.4 核心组件示例

**RouteFlowDiagram（全片高光，纯代码动画）**

```tsx
import { AbsoluteFill, useCurrentFrame, interpolate, Easing } from 'remotion';

const MAIN = 'M 200 540 H 760';                    // 入线
const BRANCH = ['M 820 540 C 960 540 950 300 1150 300',
                'M 820 540 H 1240',
                'M 820 540 C 960 540 950 780 1150 780'];
const FAIL_AT = 4 * 30;                            // 第 4s 中路故障

export const RouteFlowDiagram: React.FC<{startFrame: number}> = ({startFrame}) => {
  const f = useCurrentFrame() - startFrame;
  const lit = interpolate(f, [0, 20], [0, 1], {extrapolateRight: 'clamp'});
  const broken = f > FAIL_AT && f < FAIL_AT + 9;   // 故障窗口 0.3s
  const dotT = (f % 40) / 40;                      // 请求点循环
  return (
    <AbsoluteFill style={{justifyContent: 'center'}}>
      <svg viewBox="0 0 1440 1080" style={{width: '100%'}}>
        {/* 入线 */}
        <path d={MAIN} stroke="#F87720" strokeWidth={14} strokeLinecap="round"
              strokeDasharray={560} strokeDashoffset={560 * (1 - lit)} fill="none"/>
        {/* 三条上游：中间为主路，故障时切红并隐藏 */}
        {BRANCH.map((d, i) => {
          const active = i === 1 ? !broken : true;
          const color = i === 1 ? (broken ? '#EF4444' : '#F87720') : '#22D3EE';
          return <path key={i} d={d} stroke={color} strokeWidth={i === 1 ? 12 : 8}
            strokeOpacity={i === 1 ? 1 : 0.45} strokeLinecap="round" fill="none"
            strokeDasharray={600} strokeDashoffset={600 * (1 - interpolate(f, [10 + i * 6, 30 + i * 6], [0, 1], {easing: Easing.out(Easing.cubic), extrapolateRight: 'clamp'}))}/>;
        })}
        {/* 流动请求点：故障后偏移到备用路 */}
        <circle r={13} fill="#fff" cx={200 + 900 * dotT} cy={540 + (broken ? 0 : 0)}/>
        {/* 路由节点 */}
        <circle cx={790} cy={540} r={46} fill="#fff"/>
        <circle cx={790} cy={540} r={70} stroke="#F87720" strokeOpacity={0.4} strokeWidth={10} fill="none"/>
        {/* 上游标签 */}
        {['OpenCode Zen (free)', 'DeepSeek', 'Kilo (free)'].map((t, i) => (
          <text key={t} x={1180} y={[300, 540, 780][i] + 8} fill="#E2E8F0" fontSize={34}
            fontFamily="JetBrains Mono">{t}</text>
        ))}
      </svg>
    </AbsoluteFill>
  );
};
```

**TerminalTyper（CLI 场景）**

```tsx
export const TerminalTyper: React.FC<{cmd: string; startFrame: number; speed?: number}> = ({
  cmd, startFrame, speed = 1.6,
}) => {
  const f = useCurrentFrame() - startFrame;
  const chars = Math.max(0, Math.floor(f * speed));
  const done = chars >= cmd.length;
  return (
    <div style={{background:'#0A101E', border:'1px solid #1E293B', borderRadius:16,
                 padding:28, fontFamily:'JetBrains Mono', fontSize:26, minWidth:720}}>
      <div style={{color:'#64748B', marginBottom:12}}>~/localrouter — curl</div>
      <div style={{color:'#E2E8F0'}}>
        <span style={{color:'#34D399'}}>$ </span>
        {cmd.slice(0, chars)}
        {!done && <span style={{color:'#F87720'}}>▌</span>}
      </div>
      {done && <ResponseBody delay={f - cmd.length / speed}/>}
    </div>
  );
};
```

**数据驱动的字幕/配音对齐**：`captions.ts` 导出
`[{scene:'S1', start:0, end:6, vo:'旁白文本', caption:'屏幕字幕'}]`；
字幕组件按 `useCurrentFrame()` 查表渲染；旁白 MP3 用 `useAudio({src: staticFile('vo/S1.mp3'), startFrom})`
逐场景挂载——重排场景时只需改表。

**配音生成**：TTS（豆包/edge-tts 均可），逐场景导出 `public/vo/S*.mp3`，
语速 1.05-1.1x；BGM 选低韵律电子乐，音量 -18dB 压在旁白下。

### 5.5 渲染与发布

- 主片成片后先自检：静音看一遍（字幕信息是否完整）→ 1.5x 看一遍（节奏）
- B站上传 1080p 大会员码率；抖音/视频号用竖版；小红书用竖版 + 首帧封面
- 封面：`npx remotion still PromoCover out/cover.png`，叠 Logo + "20+ 免费模型"大字

## 6. 全平台宣发文案

### B站（横版 90s 主片）

**标题（三选一）**
1. 「20+ 个 $0 大模型一键聚合，还能把 CLI 免费额度变成 OpenAI API｜开源自部署」
2. 「我写了一个本地 API 路由管家：免费模型白嫖 + CLI 反代 + 自动故障转移」
3. 「一个端点接管 65+ AI 平台｜LocalRouter 开源宣发」

**简介**
```
LocalRouter — 你的本地 API 路由管家（开源 MIT）

🆓 免费模型聚合：公开免费网关渠道一键加入，20+ 个 $0 模型，与付费 Key 混用同一套策略
🧩 本地 CLI 反代：OpenCode / Cline / ZCode 等 CLI 凭据自动导入续期，私有协议翻译成 OpenAI 格式
🔄 5 种负载均衡策略 + 自动故障转移（<1ms 本地转发）
🛡️ OpenAI / Claude / Gemini 协议自动互转，任意 SDK 调任意模型
🌐 局域网共享：一个接入点，全家设备/团队共用

⏱ 00:00 痛点  00:14 免费模型聚合  00:30 CLI 反代  00:48 智能路由  01:04 协议互转

GitHub：https://github.com/licorxj/QM-LocalRouter
纯本地运行，Key 加密存储，Docker/Windows/macOS 安装包齐全。

#开源项目 #AI编程 #大模型 #OpenAI #本地部署
```

### 抖音 / 快手（竖版 45s）

**文案**
```
别再为 AI API 花冤枉钱了💸
这个开源的本地路由管家：
✅ 20+ 个免费大模型，一键聚合进你的策略池
✅ CLI 工具的免费额度，反代成标准 OpenAI 接口
✅ 上游挂了 0.3 秒自动切换，客户端无感
纯本地运行，Key 加密存储，局域网全家共用
开源免费，GitHub 搜「LocalRouter」👇（主页有链接）
#开源项目 #AI工具 #大模型API #程序员 #白嫖
```

### 微信视频号（竖版 45s）

```
一个本地网关，接管你所有的 AI 接口。
免费模型一键聚合、CLI 额度变 API、故障 0.3s 自动切换。
开源项目 LocalRouter，纯本地运行不外传数据。
链接见评论区 ⬇️
#AI #开源 #效率工具
```

### 小红书（竖版 + 图文可复用截图）

**标题**：「发现了宝藏开源项目！免费大模型 API 一键聚合🆓」
**正文**
```
用 AI 的姐妹都知道，各家平台一个 Key 一个 SDK 有多烦😭
最近挖到开源的 LocalRouter，本地部署一次全搞定：

🆓 免费模型聚合｜OpenCode Zen、Kilo 等免费渠道一键加入，20+ 个 $0 模型
🧩 CLI 反代｜Cline/OpenCode 这些 CLI 的免费额度，直接变成 OpenAI API
🔄 负载均衡｜多 Key 轮换 + 挂了自动切换，写代码再也不怕中断
🔒 数据不出本机，Key 全加密

Cursor/ChatBox/CherryStudio 全都即插即用，base_url 指向本地就行✨
GitHub 搜 LocalRouter（MIT 开源），评论区放链接~

#AI工具 #开源项目 #程序员日常 #效率工具 #大模型
```

### YouTube（英文，可后置）

**Title**: "LocalRouter — Free LLM Model Aggregation + CLI Reverse Proxy, All Local (Open Source)"
**Description**
```
One OpenAI-compatible endpoint for 65+ AI providers.
🆓 Aggregate 20+ free models from public gateways in one click
🧩 Turn CLI free tiers (OpenCode/Cline/ZCode…) into standard OpenAI APIs with auto-renewal
🔄 5 load-balancing strategies with <1ms local failover
🛡️ OpenAI / Claude / Gemini protocol auto-conversion
MIT licensed: https://github.com/licorxj/QM-LocalRouter
#opensource #llm #api
```

### X / Twitter

```
LocalRouter v2 — your local LLM API router 🧭

🆓 one-click free-model aggregation (20+ $0 models)
🧩 CLI reverse proxy: OpenCode/Cline free tiers → OpenAI API
🔄 5 LB strategies, 0.3s failover
🛡️ OpenAI/Claude/Gemini protocol conversion

MIT · self-hosted · LAN-ready
https://github.com/licorxj/QM-LocalRouter
```

## 7. 发布检查清单

- [ ] 主片/竖版渲染完成，静音与 1.5x 自检通过
- [ ] 封面图（含 Logo + "20+ 免费模型"大字）
- [ ] 各平台简介中的 GitHub 链接可点、仓库 README 已是新版
- [ ] B站分针权益/抖音小程序链接按需配置
- [ ] 评论区置顶：GitHub 链接 + 免费渠道免责声明（"免费渠道来自公共网关，稳定性以实时检测为准"）
- [ ] 发布后 24h 内回应评论中的部署问题（init_db → manage start 两步）
