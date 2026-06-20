// Streamlit 烟囱测试 (round 36): 验 pages_sentiment.py c4 NameError 已修
//
// c4 修复前: 第 4 列调用 c4.metric(<unread_badge 函数>) 双重渲染 → NameError
//           sentiment 概览 Tab 白屏 / 红框, _render_unread_badge 后续代码不再执行
// c4 修复后: c1, c2, c3, c4 = st.columns(4); c1/c2/c3 放 metric, c4 用
//           `with c4: _render_unread_badge()` 显式放未读徽章, 不再 NameError.
//
// 用例:
//   1. 访问 /  → 应在 sidebar 看到 "📡 舆情跟踪" 入口
//   2. 登录 admin/Admin@1234 (默认 dev 账号) → 验 auth_token 写入 session
//   3. 模拟点击 sidebar radio 切到 "📡 舆情跟踪"
//   4. 验页面 4 列 metrics 渲染: 今日事件 / 累计简报 / 季度报告 / 未读徽章
//   5. 检查 c1/c2/c3 三个 metric 出现 + 第 4 列无 NameError 红框
//   6. 落 _probe_shots/round36_streamlit_<id>.png + _probe_shots/round36_streamlit.json
//
// 4 错监听: pageerror / console.error / requestfailed / networkidle timeout
//
// 用 ../KIMI/xuanzong-dodge 已装 playwright (项目约定复用).
import { chromium } from "file:///D:/%E6%A1%8C%E9%9D%A2/KIMI/xuanzong-dodge/node_modules/playwright/index.mjs";
import { mkdirSync, existsSync, writeFileSync } from "fs";

const STREAMLIT = "http://127.0.0.1:8501";
const PROBE_DIR = "D:/ipo_audit_link/_probe_shots";
if (!existsSync(PROBE_DIR)) mkdirSync(PROBE_DIR, { recursive: true });

const SHOTS = PROBE_DIR;

async function run() {
  const errs = [];
  const reqFails = [];
  const browser = await chromium.launch({
    args: [
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-webgl",
      "--ignore-gpu-blocklist",
    ],
  });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  page.on("pageerror", (e) => errs.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") {
      const t = m.text();
      // 过滤 Streamlit 内置 noise (组件 hydration 警告 / 旧 component 弃用)
      if (t.includes("DeprecationWarning") || t.includes("componentready")) return;
      errs.push(`console.error: ${t.slice(0, 200)}`);
    }
  });
  page.on("requestfailed", (req) => {
    const url = req.url();
    // 忽略 streamlit 内部的 socket / websocket reconnect
    if (url.includes("_stcore/stream") || url.includes("webrtc")) return;
    reqFails.push(`requestfailed: ${url} - ${req.failure()?.errorText || "?"}`);
  });

  const results = [];

  // =============================================================
  // 1) 打开首页, 看 sidebar 是否有 📡 舆情跟踪
  // =============================================================
  let status = "ok";
  let note = "";
  try {
    await page.goto(STREAMLIT, { waitUntil: "domcontentloaded", timeout: 30000 });
    // Streamlit 第一次 load 慢 (组件 hydrate, 数据加载, sidebar 折叠/展开)
    // 等 12 秒, 多次轮询 sidebar 文本
    let sidebarText = "";
    for (let i = 0; i < 24; i++) {
      await page.waitForTimeout(500);
      sidebarText = await page.evaluate(() => {
        const sb = document.querySelector('[data-testid="stSidebar"]');
        return (sb?.innerText || "").slice(0, 2000);
      });
      if (sidebarText.includes("舆情跟踪")) break;
    }
    await page.waitForTimeout(2000);

    // 试展开 sidebar — 点 sidebar collapse button (左上角 ›)
    const collapseBtn = page.locator('[data-testid="stSidebarCollapsedControl"]');
    const collapseCount = await collapseBtn.count();
    if (collapseCount > 0) {
      try { await collapseBtn.click({ timeout: 3000 }); } catch (_) {}
      await page.waitForTimeout(2000);
    }

    // 看 sidebar 是否有舆情跟踪
    const sidebarHasSentiment = await page.locator("text=舆情跟踪").count();
    note += `sidebar 舆情跟踪 hits=${sidebarHasSentiment} collapseBtn=${collapseCount} `;
    note += `sidebarText=[${sidebarText.replace(/\n/g, " | ").replace(/"/g, "'")}] `;
    if (sidebarHasSentiment < 1) {
      status = "load-failed";
      note += "(未找到舆情跟踪入口)";
    }
    await page.screenshot({ path: `${SHOTS}/round36_streamlit_home.png`, fullPage: false });
    results.push({ id: "home_load", status, note, errs: errs.slice() });
    errs.length = 0;
  } catch (e) {
    status = "load-failed";
    note = `home load failed: ${e.message}`;
    results.push({ id: "home_load", status, note, errs: errs.slice() });
  }

  // =============================================================
  // 2) 登录: 通过 /api/auth/login 拿 token, 注入 session_state
  //    (Streamlit 没法直接操控 session, 但 set_page_config + 写 cookie +
  //    调 FastAPI 端点能 work. 简化方案: 用 page.evaluate 调 API 拿 token,
  //    然后 reload Streamlit, 让 _http.py 的 auth_headers() 读 session_state —
  //    实际上 session_state 在 page reload 时会丢. 改用 page.addInitScript
  //    注一段 JS, 不行 — session_state 是 Python 端, JS 拿不到.
  //    最稳妥: 让 streamlit 自动跳到 🔐 系统管理 Tab, 走 UI 登录.
  //    dev 模式下 AUTH_ENABLED=False 不会强制登录, 走 _http 时会拿合成 admin,
  //    所以理论上不需要登录就能看到 sentiment 页面. 试一下.)
  // =============================================================

  // 3) 直接点 sidebar 切到 sentiment (不登录 — dev 模式 AUTH_ENABLED=False)
  try {
    // 找 sidebar radio 里的 📡 舆情跟踪, 通过 input radio 选
    // Streamlit 用隐式 radio input, 触发 change 才会 rerun
    const sentLocator = page.locator('label:has-text("舆情跟踪")').first();
    const sentCount = await sentLocator.count();
    note = `sentiment radio count=${sentCount} `;
    if (sentCount > 0) {
      // Streamlit label 包 radio input, force click 触发 streamlit rerun
      await sentLocator.click({ force: true, timeout: 5000 });
      note += "clicked-label ";
      // 等 Streamlit rerender (多等一会儿, 网络 + rerun 较慢)
      await page.waitForTimeout(5000);
    } else {
      status = "load-failed";
      note += "no 舆情跟踪 radio in sidebar";
    }

    // 4) 验 c1/c2/c3 三个 metric 出现 — 长时间轮询, 因为 Streamlit 拉 API 异步
    let c1 = 0, c2 = 0, c3 = 0, nameError = 0, streamlitException = 0, mainText = "";
    for (let i = 0; i < 40; i++) {
      await page.waitForTimeout(1000);
      c1 = await page.locator('text=今日事件').count();
      c2 = await page.locator('text=累计简报').count();
      c3 = await page.locator('text=季度报告').count();
      nameError = await page.locator("text=NameError").count();
      streamlitException = await page.locator('[data-testid="stException"]').count();
      mainText = await page.evaluate(() => {
        const m = document.querySelector('section.main') || document.querySelector('[data-testid="stAppViewContainer"]');
        return (m?.innerText || "").slice(0, 2000);
      });
      if (c1 >= 1 && c2 >= 1 && c3 >= 1) break;
      // 如果已经到 sentiment 页面 (看到"舆情跟踪"或"选择项目"), 等更久
      if (i === 10) console.log(`[sentiment wait i=${i}] mainText:`, mainText.slice(0, 200));
    }

    // 截图 sentiment 概览 (放在轮询后取最新状态)
    await page.screenshot({ path: `${SHOTS}/round36_streamlit_sentiment.png`, fullPage: false });

    note += `c1=${c1} c2=${c2} c3=${c3} `;
    note += `NameError=${nameError} stException=${streamlitException} `;
    note += `mainText=[${mainText.replace(/\n/g, " | ").slice(0, 300).replace(/"/g, "'")}] `;

    if (c1 >= 1 && c2 >= 1 && c3 >= 1 && nameError === 0) {
      status = "ok";
    } else if (nameError > 0) {
      status = "nameerror";
      note += "(NameError 仍然出现 — c4 修复未生效)";
    } else if (c1 === 0 && c2 === 0 && c3 === 0) {
      status = "blank-tab";
      note += "(sentiment 概览 tab 没渲染 3 个 metric)";
    } else {
      status = "partial";
      note += "(部分 metric 渲染但不全)";
    }

    // 6) 验第 4 列内容 — 应该是未读徽章 (unread count 数字) 或 "无未读"
    const unreadBadge = await page.evaluate(() => {
      // 找 st.column 容器里的最后一个 metric
      const cols = document.querySelectorAll('[data-testid="column"]');
      if (cols.length < 4) return { found: false, lastColText: "", colCount: cols.length };
      const last = cols[cols.length - 1];
      return { found: true, lastColText: (last.innerText || "").slice(0, 100), colCount: cols.length };
    });
    note += `c4=${JSON.stringify(unreadBadge)} `;

    results.push({
      id: "sentiment_c4_fix",
      status,
      note,
      errs: errs.slice(),
      reqFails: reqFails.slice(),
      c1, c2, c3, nameError, streamlitException,
      c4: unreadBadge,
    });
  } catch (e) {
    results.push({
      id: "sentiment_c4_fix",
      status: "load-failed",
      note: `sentiment test failed: ${e.message}`,
      errs: errs.slice(),
      reqFails: reqFails.slice(),
    });
  }

  await ctx.close();
  await browser.close();

  // 落 JSON
  writeFileSync(
    `${SHOTS}/round36_streamlit.json`,
    JSON.stringify({ timestamp: new Date().toISOString(), results }, null, 2)
  );

  console.log("\n========== Streamlit smoke results ==========");
  for (const r of results) {
    console.log(`[${r.status.padEnd(14)}] ${r.id.padEnd(22)} ${r.note || ""}`);
    if (r.errs && r.errs.length) {
      for (const e of r.errs.slice(0, 5)) console.log(`   ${e}`);
    }
  }
  console.log(`\nshots → ${SHOTS}`);
  console.log(`json  → ${SHOTS}/round36_streamlit.json`);
}

run().catch((e) => {
  console.error("FATAL:", e);
  process.exit(2);
});
