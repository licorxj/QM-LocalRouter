#!/usr/bin/env node
/**
 * ZCode 赠送 token —— 你主动触发的一次性领取
 *
 * 设计约束：
 *   · 不轮询：跑一次就退出，什么时候跑由你决定。
 *   · 不模拟请求：脚本自身不访问任何 HTTP 接口，也不读写 token。
 *     刷新投放、验证码、claim、上报全部由 ZCode 原生组件完成。
 *   · 不伪造事件语义：优先直接调用 React 元素上绑定的原生 onClick
 *     （__reactProps$…），也就是 UI 里那个按钮真正绑定的回调。
 *
 * 它做三件事：
 *   1) 让客户端自己刷新一次投放（派发 visibilitychange，原生 poller 会发
 *      GET /api/v1/marketing/touch —— 这一枪是客户端打的）。
 *   2) 事件驱动地等原生 UI 出现「领取」入口（MutationObserver，不轮询）。
 *   3) 调该入口的原生 handler，然后等结果（票券弹窗 / 失败弹窗 / 验证码），
 *      并用客户端自己的日志 coding-plan-subscription.claimManualPlan 核对。
 *
 * 用法：
 *   node claim-now.mjs                  # 立刻领一次
 *   node claim-now.mjs --wait 30        # 刷新后最多等 30s 让投放出现
 *   node claim-now.mjs --no-refresh     # 不触发刷新，只处理当前界面
 *   node claim-now.mjs --any            # 放宽横幅文案（默认只点含“领取”的横幅）
 *   node claim-now.mjs --dry-run        # 只报告将要点的目标
 *   node claim-now.mjs --json           # 输出机器可读结果
 *
 * 账号隔离：
 *   node claim-now.mjs --profile D:\zcode-profiles\acct1 --port 9223
 *   node claim-now.mjs --log-dir D:\zcode-profiles\acct1\.zcode\v2\logs --port 9223
 *   --profile 只用于定位该实例的客户端日志，不读取 token。
 *
 * 退出码：0 成功 / 1 当前没有可领投放 / 2 领取失败 / 3 需人工过验证码
 *        4 环境错误 / 5 结果未确认（去 设置 → 用量 核对）
 */

import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs';

// ------------------------------------------------------------------ 参数
const argv = process.argv.slice(2);
const has = (f) => argv.includes(f);
const val = (f, d) => {
  const i = argv.indexOf(f);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : d;
};

// --profile 让日志核对也跟着隔离 profile 走；也可用 --log-dir 显式指定。
const profileArg = val('--profile', process.env.ZCODE_PROFILE_DIR || '');
const profileDir = profileArg ? path.resolve(profileArg) : '';
const logDirArg = val('--log-dir', '');

const CFG = {
  port: Number(val('--port', 9222)),
  wait: Number(val('--wait', 20)) * 1000, // 刷新后等投放出现
  settle: Number(val('--settle', 25)) * 1000, // 点击后等结果
  captcha: Number(val('--captcha-wait', 120)) * 1000, // 需要人工验证码时的等待
  refresh: !has('--no-refresh'),
  any: has('--any'),
  domClick: has('--click'),
  dryRun: has('--dry-run'),
  json: has('--json'),
  profileDir,
  logDir: logDirArg
    ? path.resolve(logDirArg)
    : profileDir
      ? path.join(profileDir, '.zcode', 'v2', 'logs')
      : path.join(os.homedir(), '.zcode', 'v2', 'logs'),
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ts = () => new Date().toTimeString().slice(0, 8);
const log = (...a) => (CFG.json ? console.error : console.log)(`[${ts()}]`, ...a);
const out = (obj) => console.log(JSON.stringify(obj, null, 2));

// ------------------------------------------------------------------ CDP
class Cdp {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.closed = false;
  }

  static async discover(port) {
    let res;
    try {
      res = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(4000) });
    } catch {
      throw new Error(
        `连不上 127.0.0.1:${port}：请先完全退出 ZCode，用 start-debug.cmd 带 --remote-debugging-port 启动。`
      );
    }
    const pages = (await res.json()).filter((t) => t.type === 'page');
    return (
      pages.find((t) => /renderer[\\/]index\.html/.test(t.url)) ||
      pages.find((t) => t.url.startsWith('file://')) ||
      pages[0] ||
      null
    );
  }

  open() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      const timer = setTimeout(() => reject(new Error('CDP 连接超时')), 8000);
      ws.addEventListener('open', () => {
        clearTimeout(timer);
        resolve();
      });
      ws.addEventListener('error', (e) => {
        clearTimeout(timer);
        reject(new Error(`CDP 错误: ${e?.message || 'unknown'}`));
      });
      ws.addEventListener('close', () => {
        this.closed = true;
        for (const { reject: rj } of this.pending.values()) rj(new Error('CDP 断开'));
        this.pending.clear();
      });
      ws.addEventListener('message', (ev) => {
        let m;
        try {
          m = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
        } catch {
          return;
        }
        const p = this.pending.get(m.id);
        if (!p) return;
        this.pending.delete(m.id);
        m.error ? p.reject(new Error(m.error.message || 'CDP error')) : p.resolve(m.result);
      });
    });
  }

  send(method, params = {}, timeoutMs = 30000) {
    if (this.closed) return Promise.reject(new Error('CDP 断开'));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP ${method} 超时`));
      }, timeoutMs).unref?.();
    });
  }

  async eval(expression, timeoutMs = 30000) {
    const r = await this.send(
      'Runtime.evaluate',
      { expression, returnByValue: true, awaitPromise: true, userGesture: true },
      timeoutMs
    );
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text || '页面脚本异常');
    }
    return r.result?.value;
  }

  close() {
    try {
      this.ws?.close();
    } catch {}
  }
}

// ------------------------------------------------------------------ 页面侧原生桥
// 全部逻辑挂在 page 的 globalThis.__zc 上：只读 DOM + 调 React 原生 props。
const HELPERS = String.raw`
globalThis.__zc = globalThis.__zc || (() => {
  const CLAIM = /(领取|領取|立即领取|claim|claim now|受け取|受取|получ|받기)/i;
  const NOT_CLAIM = /(开始体验|start using|查看套餐|view plan|复制|copy|分享|share|知道了|got it|关闭|close|取消|cancel|仍然关闭|close anyway|重播|replay)/i;
  const SUCCESS = /(领取成功|you now have access|利用できるようになりました|사용할 수 있습니다|теперь доступен)/i;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const q = (s) => document.querySelector(s);

  const bannerCard = () => q('[data-testid="marketing-banner"]');
  const bannerBtn = () => q('[data-testid="marketing-banner"] > button[aria-label]');
  const dialogEl = () => q('[data-testid="cloud-content-dialog"]');
  const failureEl = () => q('[data-testid="marketing-failure-dialog"]');
  const captchaEl = () => q('.aliyunCaptcha-mask, .aliyunCaptcha-window');

  const dialogTitle = () => {
    const d = dialogEl();
    if (!d) return '';
    return clean((d.querySelector('[data-testid="cloud-dialog-status"]') || {}).textContent);
  };

  const dialogActions = () => {
    const d = dialogEl();
    if (!d) return [];
    return [...d.querySelectorAll('button[data-testid="cloud-dialog-action"]')].map((a) => ({
      id: a.getAttribute('data-action-id') || '',
      label: clean(a.textContent),
      disabled: !!a.disabled,
    }));
  };

  const claimOf = (acts) =>
    acts.find((a) => !a.disabled && !NOT_CLAIM.test(a.label) && CLAIM.test(a.label)) || null;

  // 找“可领”的原生入口；force=true 时放宽横幅文案判断（对应 --any），
  // 弹窗仍只认“领取”类按钮，避免点到「开始体验 / 查看套餐」。
  function find(force) {
    const b = bannerBtn();
    if (b) {
      const card = bannerCard();
      const label = clean(b.getAttribute('aria-label'));
      const busy = card ? card.getAttribute('aria-busy') === 'true' : false;
      if (!b.disabled && !busy && (force ? !!label : CLAIM.test(label))) {
        return { kind: 'banner', label, sel: '[data-testid="marketing-banner"] > button[aria-label]' };
      }
    }
    const acts = dialogActions();
    const hit = claimOf(acts);
    if (hit && !SUCCESS.test(dialogTitle())) {
      return {
        kind: 'dialog',
        label: hit.label,
        title: dialogTitle(),
        actionId: hit.id,
        sel:
          '[data-testid="cloud-content-dialog"] button[data-testid="cloud-dialog-action"]' +
          '[data-action-id="' + hit.id + '"]',
      };
    }
    return null;
  }

  function probe() {
    const b = bannerBtn();
    const card = bannerCard();
    const d = dialogEl();
    return {
      url: location.href,
      visibility: document.visibilityState,
      banner: b
        ? {
            label: clean(b.getAttribute('aria-label')),
            disabled: !!b.disabled,
            busy: card ? card.getAttribute('aria-busy') : null,
          }
        : null,
      dialog: d ? { title: dialogTitle(), actions: dialogActions() } : null,
      failure: failureEl() ? clean(failureEl().textContent).slice(0, 300) : null,
      captcha: !!captchaEl(),
    };
  }

  function snapshot() {
    const b = bannerBtn();
    const card = bannerCard();
    const d = dialogEl();
    const title = dialogTitle();
    const acts = d ? dialogActions() : [];
    return {
      banner: b
        ? { label: clean(b.getAttribute('aria-label')), disabled: !!b.disabled,
            pending: card ? card.getAttribute('aria-busy') === 'true' : false }
        : null,
      dialog: d ? { title, actions: acts, hasClaim: !!claimOf(acts) } : null,
      successTitle: d && SUCCESS.test(title) ? title : '',
      failure: failureEl() ? clean(failureEl().textContent).slice(0, 300) : '',
      captcha: !!captchaEl(),
    };
  }

  // 直接调用 React 元素上绑定的原生 onClick；拿不到才退回 el.click()
  function invoke(sel) {
    const el = q(sel);
    if (!el) return 'missing';
    const key = Object.keys(el).find((k) => k.startsWith('__reactProps$'));
    const props = key ? el[key] : null;
    if (props && typeof props.onClick === 'function') {
      const ev = {
        type: 'click', target: el, currentTarget: el,
        preventDefault() {}, stopPropagation() {}, isPropagationStopped: () => false,
      };
      props.onClick(ev);
      return 'native-onClick';
    }
    el.click();
    return 'dom-click';
  }

  function waitFor(fn, ms) {
    return new Promise((resolve) => {
      const hit = fn();
      if (hit) return resolve(hit);
      let done = false;
      const finish = (v) => {
        if (done) return;
        done = true;
        obs.disconnect();
        clearTimeout(t);
        resolve(v);
      };
      const obs = new MutationObserver(() => {
        const v = fn();
        if (v) finish(v);
      });
      obs.observe(document.body, {
        childList: true, subtree: true, attributes: true,
        attributeFilter: ['aria-label', 'disabled', 'aria-busy', 'data-testid'],
      });
      const t = setTimeout(() => finish(null), ms);
    });
  }

  // 客户端 poller 用 visibilityState 判断要不要拉投放，这里临时伪装成可见，
  // 结束后必须还原（只影响本页面，不落盘）。
  function forceVisible(on) {
    if (on) {
      if (document.__zcVis) return 'already';
      document.__zcVis = {
        v: Object.getOwnPropertyDescriptor(document, 'visibilityState'),
        h: Object.getOwnPropertyDescriptor(document, 'hidden'),
      };
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      return 'forced';
    }
    const b = document.__zcVis;
    if (!b) return 'not-forced';
    if (b.v) Object.defineProperty(document, 'visibilityState', b.v);
    else delete document.visibilityState;
    if (b.h) Object.defineProperty(document, 'hidden', b.h);
    else delete document.hidden;
    delete document.__zcVis;
    return 'restored';
  }

  return { find, probe, invoke, waitFor, snapshot, forceVisible };
})();
`;

// 等价于你切回窗口：原生 poller 的 visibilitychange 监听会立刻拉一次投放
const NUDGE = String.raw`(() => {
  document.dispatchEvent(new Event('visibilitychange'));
  return document.visibilityState;
})()`;

// ------------------------------------------------------------------ 客户端日志核对（本地文件，无网络）
function tail(file, bytes) {
  const size = fs.statSync(file).size;
  const start = Math.max(0, size - bytes);
  const fd = fs.openSync(file, 'r');
  const buf = Buffer.alloc(size - start);
  fs.readSync(fd, buf, 0, buf.length, start);
  fs.closeSync(fd);
  return buf.toString('utf8').split(/\r?\n/);
}

function scanClaimLog(sinceMs) {
  const out = [];
  let files = [];
  try {
    files = fs
      .readdirSync(CFG.logDir)
      .filter((n) => /^\d{4}-\d{2}-\d{2}\.log$/.test(n))
      .sort()
      .slice(-2);
  } catch {
    return out;
  }
  for (const name of files) {
    let lines = [];
    try {
      lines = tail(path.join(CFG.logDir, name), 256 * 1024);
    } catch {
      continue;
    }
    for (const line of lines) {
      if (!/coding-plan-subscription\.claim/i.test(line)) continue;
      const m = /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]/.exec(line);
      if (!m) continue;
      const at = Date.parse(m[1].replace(' ', 'T'));
      if (Number.isFinite(at) && at >= sinceMs - 5000) out.push(line);
    }
  }
  return out;
}

// 只读本地日志等客户端的 rpc 结果，最多 ~8s（不是网络轮询）
async function awaitClaimLog(sinceMs) {
  for (let i = 0; i < 10; i++) {
    const hits = scanClaimLog(sinceMs);
    if (hits.length) return hits;
    await sleep(800);
  }
  return [];
}

// ------------------------------------------------------------------ 主流程（一次）
async function main() {
  const startedAt = Date.now();
  const started = new Date(startedAt).toISOString();

  const target = await Cdp.discover(CFG.port);
  if (!target) throw new Error('没找到 ZCode 页面（窗口可能还在加载）');
  const cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.open();
  log('已连接:', target.url);

  let forced = false;
  const restore = async () => {
    if (!forced) return;
    try {
      await cdp.eval('__zc.forceVisible(false)');
    } catch {}
    forced = false;
  };

  try {
    await cdp.eval(HELPERS);

    let st = await cdp.eval('__zc.probe()');
    log(
      `当前界面: banner=${st.banner ? `「${st.banner.label}」` : '无'} dialog=${
        st.dialog ? `「${st.dialog.title}」` : '无'
      }${st.failure ? ` failure=「${st.failure}」` : ''}`
    );

    // 1) 没有可领入口 → 让 ZCode 自己刷新一次投放
    let hit = await cdp.eval(`__zc.find(${CFG.any})`);
    if (!hit && CFG.refresh) {
      const visMode = await cdp.eval('__zc.forceVisible(true)');
      forced = true; // 即使是 'already' 也要在结束时还原，避免上次异常退出遗留
      log(`当前没有可领投放，触发一次客户端原生刷新…${visMode === 'already' ? '（沿用上次的可见性伪装）' : ''}`);
      const vis = await cdp.eval(NUDGE);
      log(`  visibilitychange 已派发（visibilityState=${vis}）`);
    } else if (hit) {
      log('已有可领入口，直接处理（不触发刷新）');
    }

    // 2) 事件驱动地等原生 UI 出现领取入口
    if (!hit) {
      hit = await cdp.eval(
        `__zc.waitFor(() => __zc.find(${CFG.any}), ${CFG.wait})`,
        CFG.wait + 15000
      );
    }

    if (!hit) {
      const s2 = await cdp.eval('__zc.probe()');
      log('当前没有可领的赠送投放（活动没在投、或已领过）。');
      if (s2.dialog) log(`  界面上有弹窗：「${s2.dialog.title}」`);
      if (s2.failure) log(`  失败提示：${s2.failure}`);
      await restore();
      if (CFG.json) out({ ok: false, status: 'no-delivery', probe: s2, at: started });
      cdp.close();
      process.exitCode = 1;
      return;
    }

    log(
      `发现领取入口：${hit.kind === 'banner' ? '横幅' : '弹窗'}「${hit.label}」${
        hit.title ? ` / ${hit.title}` : ''
      }`
    );
    if (CFG.dryRun) {
      log('[dry-run] 不执行');
      await restore();
      if (CFG.json) out({ ok: false, status: 'dry-run', target: hit, at: started });
      cdp.close();
      return;
    }

    // 3) 调原生 handler（React props 里的 onClick）
    const how = CFG.domClick
      ? await cdp.eval(
          `(() => { const el = document.querySelector(${JSON.stringify(
            hit.sel
          )}); if (!el) return 'missing'; el.click(); return 'dom-click'; })()`
        )
      : await cdp.eval(`__zc.invoke(${JSON.stringify(hit.sel)})`);
    log(`已触发领取（${how}），等客户端验证码 + claim…`);

    // 4) 事件驱动地等结果
    const outcome = await cdp.eval(
      `__zc.waitFor(() => {
         const s = __zc.snapshot();
         if (s.captcha) return { kind: 'captcha', title: s.dialog ? s.dialog.title : '' };
         if (s.failure) return { kind: 'failure', message: s.failure };
         if (s.successTitle) return { kind: 'success', message: s.successTitle };
         return null;
       }, ${CFG.settle})`,
      CFG.settle + 15000
    );

    let status = 'unknown';
    let message = '';
    let ticket = '';
    if (outcome?.kind === 'success') {
      status = 'success';
      message = outcome.message;
    } else if (outcome?.kind === 'failure') {
      status = 'failure';
      message = outcome.message;
    } else if (outcome?.kind === 'captcha') {
      status = 'captcha';
      message = '需要人工完成阿里云验证码';
      process.stdout.write('\x07');
      log('⚠ 需要你在 ZCode 窗口里完成验证码，脚本等待中…');
      const after = await cdp.eval(
        `__zc.waitFor(() => {
           const s = __zc.snapshot();
           if (s.failure) return { kind: 'failure', message: s.failure };
           if (s.successTitle) return { kind: 'success', message: s.successTitle };
           if (!s.captcha && s.dialog && !s.dialog.hasClaim) return { kind: 'ticket', message: s.dialog.title };
           return null;
         }, ${CFG.captcha})`,
        CFG.captcha + 15000
      );
      if (after?.kind === 'success') (status = 'success'), (message = after.message);
      else if (after?.kind === 'failure') (status = 'failure'), (message = after.message);
      else if (after?.kind === 'ticket') (status = 'success'), (ticket = after.message);
      else (status = 'captcha'), (message = '验证码仍未完成');
    } else {
      // 5) 没抓到弹窗：票券弹窗（success_popup 标题由服务端给，未必含“领取成功”）
      const s = await cdp.eval('__zc.snapshot()');
      if (s.dialog && !s.dialog.hasClaim) {
        ticket = s.dialog.title;
      } else if (s.dialog?.hasClaim) {
        status = 'failure';
        message = `点击后领取入口仍在（${s.dialog.title || '弹窗'}），可能被取消`;
      }
    }

    // 6) 用客户端自己的 rpc 日志做最终核对（只有“未确认”才多等几秒）
    const lines = status === 'unknown' ? await awaitClaimLog(startedAt) : scanClaimLog(startedAt);
    const okLine = lines.find((l) => /\.claimManualPlan\s+OK/i.test(l));
    const failLine = lines.find((l) => /\.claimManualPlan\s+(FAIL|ERROR)/i.test(l));
    if (status !== 'failure' && okLine) {
      status = 'success';
      if (!message) message = '客户端日志确认 claimManualPlan OK';
    }
    if (status !== 'success' && failLine) {
      status = 'failure';
      message = message || failLine.replace(/^.*\[rpc:call\]\s*/, '').slice(0, 160);
    }
    if (status === 'unknown' && ticket) {
      status = 'success';
      message = `票券弹窗：${ticket}`;
    }

    const result = {
      ok: status === 'success',
      status,
      message,
      ticket,
      target: hit,
      invoked: how,
      nativeLog: lines.slice(-3).map((l) => l.slice(-200)),
      at: started,
    };

    if (status === 'success') {
      process.stdout.write('\x07');
      log('✓ 领取成功：', message || '（客户端已确认）');
      for (const l of lines.slice(-2)) log('   log:', l.slice(-170));
    } else if (status === 'failure') {
      log('✗ 领取失败：', message || '（原因见 ZCode 弹窗）');
      if (failLine) log('   log:', failLine.slice(-170));
    } else {
      log('结果未确认：可能是 popup 型活动（无票券弹窗）。去 设置 → 用量 / 套餐 核对。');
    }

    await restore();
    cdp.close();
    if (CFG.json) out(result);
    process.exitCode = status === 'success' ? 0 : status === 'failure' ? 2 : status === 'captcha' ? 3 : 5;
  } catch (e) {
    await restore();
    cdp.close();
    throw e;
  }
}

main().catch((e) => {
  console.error('失败:', e.message);
  process.exit(4);
});
