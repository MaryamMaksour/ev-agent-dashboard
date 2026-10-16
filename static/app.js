(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  // everything that came from the agents is user text: always escape before it goes into HTML
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 0) => (v == null ? "–" : (v * 100).toFixed(d) + "%");
  const aed = (v, d = 2) => (v == null ? "–" : "AED " + num(v, d));
  const secs = (v) => (v == null ? "–" : Number(v).toFixed(1) + " s");
  const when = (iso) => {
    if (!iso) return "–";
    const d = new Date(iso);
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Asia/Dubai" }).formatToParts(d).map((x) => [x.type, x.value]));
    return `${p.day} ${p.month} ${p.hour}:${p.minute}`;
  };
  const ago = (iso) => {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  };

  const state = { me: null, page: "overview", range: "30", heat: "whatsapp", data: {}, wa: { outcome: "", flag: "", q: "", offset: 0 }, run: null };

  async function api(path) {
    const r = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || "Request failed (" + r.status + ")");
    return body;
  }
  function showError(e) { const b = $("#error"); b.textContent = e ? String(e.message || e) : ""; b.hidden = !e; }

  // ---------- tooltip ----------
  const tip = $("#tip");
  function showTip(html, e) {
    tip.innerHTML = html; tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + 14, y = e.clientY - h - 14;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
    if (y < 8) y = e.clientY + 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  const hideTip = () => { tip.hidden = true; };

  // ---------- charts ----------
  // round axis steps (1, 2, 2.5, 5 x 10^n), 4-5 gridlines
  function axis(v) {
    if (!v || v <= 0) return { max: 1, step: 0.25 };
    const raw = v / 4, p = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / p;
    const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
    return { max: step * Math.ceil(v / step), step };
  }
  function dayBars(el, series, opts) {
    if (!series || !series.length || series.every((d) => !d.value)) { el.innerHTML = `<div class="empty">${esc(opts.empty || "Nothing in this period.")}</div>`; return; }
    const W = el.clientWidth || 600, H = 200, m = { t: 10, r: 6, b: 24, l: 44 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const ax = axis(Math.max(...series.map((d) => d.value))), max = ax.max;
    const bw = iw / series.length, gap = Math.min(Math.max(1, bw * 0.28), 8);
    const y = (v) => m.t + ih - (v / max) * ih;
    const col = css(opts.color || "--s1");
    const every = Math.max(1, Math.ceil(56 / bw));  // at least ~56px between day labels
    const dec = ax.step < 1 ? (ax.step % 0.5 ? 2 : 1) : 0;
    let s = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(opts.label)}">`;
    for (let v = 0; v <= max + 1e-9; v += ax.step) {
      const yy = y(v);
      s += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}"/><text x="${m.l - 6}" y="${yy + 4}" text-anchor="end">${esc(num(v, dec))}</text>`;
    }
    series.forEach((d, i) => {
      const x = m.l + i * bw + gap / 2, w = Math.max(1, bw - gap), top = y(d.value), base = y(0);
      if (d.value > 0) {
        const r = Math.min(4, w / 2, base - top);
        s += `<path d="M${x},${base} V${top + r} q0,-${r} ${r},-${r} H${x + w - r} q${r},0 ${r},${r} V${base} Z" fill="${col}"/>`;
      }
      s += `<rect x="${m.l + i * bw}" y="${m.t}" width="${bw}" height="${ih}" fill="transparent" data-i="${i}"/>`;
      const fromEnd = series.length - 1 - i;  // label the latest day, then every Nth day back from it
      if (fromEnd % every === 0) s += `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle">${esc(d.label)}</text>`;
    });
    el.innerHTML = s + "</svg>";
    $$("rect[data-i]", el).forEach((r) => {
      r.addEventListener("mousemove", (e) => { const d = series[+r.dataset.i]; showTip(`<b>${esc(d.weekday)} ${esc(d.label)}</b><div class="row"><span>${esc(opts.name)}</span><span>${esc(opts.fmt ? opts.fmt(d.value) : num(d.value))}</span></div>`, e); });
      r.addEventListener("mouseleave", hideTip);
    });
  }
  function hbars(el, items, opts = {}) {
    if (!items.length) { el.innerHTML = `<div class="empty">${esc(opts.empty || "Nothing yet.")}</div>`; return; }
    const max = opts.max || Math.max(1, ...items.map((i) => i.v));
    el.innerHTML = items.map((it) => `<div class="bar-row"><span class="name" title="${esc(it.l)}">${esc(it.l)}</span><div class="bar-track"><div class="bar-fill" style="width:${(it.v / max) * 100}%;background:var(${it.c || opts.c || "--s1"})"></div></div><span class="v">${esc(it.t != null ? it.t : num(it.v))}</span></div>`).join("");
  }
  function heatmap(hm, label) {
    const el = $("#heat");
    const steps = ["--h0", "--h1", "--h2", "--h3", "--h4", "--h5", "--h6"];
    const max = Math.max(0, ...hm.grid.flat());
    let s = `<span></span>` + Array.from({ length: 24 }, (_, h) => `<span class="cl">${h % 3 === 0 ? h : ""}</span>`).join("");
    hm.grid.forEach((row, d) => {
      s += `<span class="rl">${esc(hm.weekdays[d])}</span>`;
      row.forEach((v, h) => {
        const k = v === 0 ? 0 : Math.min(6, 1 + Math.floor((v / max) * 5.999));
        s += `<div class="cell" style="background:var(${steps[k]})" data-v="${v}" data-d="${esc(hm.weekdays[d])}" data-h="${h}" aria-label="${esc(hm.weekdays[d])} ${h}:00, ${v}"></div>`;
      });
    });
    el.innerHTML = s;
    $$(".cell", el).forEach((c) => {
      c.addEventListener("mousemove", (e) => showTip(`<b>${esc(c.dataset.d)} ${esc(c.dataset.h)}:00–${esc(c.dataset.h)}:59</b><div class="row"><span>${esc(label)}</span><span>${esc(c.dataset.v)}</span></div>`, e));
      c.addEventListener("mouseleave", hideTip);
    });
    $("#rampSw").innerHTML = steps.slice(1).map((v) => `<span class="sw" style="background:var(${v})"></span>`).join(" ");
    $("#heatHint").textContent = max ? `${label} by weekday and hour (UAE time). Busiest: ${hm.peak.weekday} ${hm.peak.hour}:00 with ${hm.peak.count}.` : "Nothing in this period.";
  }
  const kpi = (lab, val, delta) => `<div class="kpi"><span class="lab">${esc(lab)}</span><span class="val">${val}</span><span class="delta">${delta || ""}</span></div>`;

  // ---------- pages ----------
  const titles = {
    overview: ["Overview", "Both agents, from what they already save"],
    whatsapp: ["WhatsApp agent", "Data assistant (ev-proto) · Zoho CRM, Zoho Books, MenaMe HR"],
    calls: ["Voice agent calls", "LiveKit voice agent (ev-voice-agent) · real calls, browser now, 3CX next"],
    voice: ["Voice agent tests", "Qwen3 model tests (voice-agent-llm-test) · text calls, no audio yet"],
    logins: ["Logins", "Sign-in attempts on the WhatsApp agent"],
  };

  async function renderOverview() {
    const d = await api(`/api/overview?range=${state.range}`);
    state.data.overview = d;
    const wa = d.whatsapp, vo = d.voice, t = wa.totals, vt = vo.totals;
    const ws = wa.status, vs = vo.status;
    $("#ovHealth").innerHTML = [
      [ws.audit_db, "WhatsApp agent data", ws.audit_db ? `Last question ${ago(ws.last_question)}` : `audit.db not found in ${ws.logs_dir}`],
      [vs.found, "Voice test data", vs.found ? `${vs.runs} runs · last ${ago(vs.last_run)}` : `runs folder not found: ${vs.runs_dir}`],
      [d.voice_calls.found, "Voice agent calls", d.voice_calls.found ? `${d.voice_calls.calls} calls · last ${ago(d.voice_calls.last_call)}` : `no calls folder in ${d.voice_calls.data_dir}`],
    ].map(([ok, title, sub]) => `<div class="hcell"><span class="dot" style="background:var(${ok ? "--good" : "--crit"})"></span><div><b>${esc(title)}</b><span>${esc(sub)}</span></div></div>`).join("");
    const latest = vo.latest_run;
    const best = latest && latest.models.find((x) => x.verdict === "GOOD ENOUGH");
    $("#ovKpis").innerHTML = [
      kpi("WhatsApp questions", num(t.questions), `${num(t.people)} people asked`),
      kpi("WhatsApp cost", aed(t.cost_aed), t.cost_aed_per_question != null ? `${aed(t.cost_aed_per_question, 3)} per question` : ""),
      kpi("Typical answer time", secs(t.seconds_p50), `Slowest 5%: ${secs(t.seconds_p95)}`),
      kpi("Answered with sources", pct(t.questions ? (wa.outcomes.find((o) => o.key === "answered").count / t.questions) : null), "of all questions"),
      kpi("Voice test calls", num(vt.calls), `${num(vt.passed)} of ${num(vt.tests)} scenarios passed`),
      kpi("Latest model verdict", latest ? `<span class="val-sm">${best ? esc(best.model) : "None good enough"}</span>` : "–", latest ? esc(latest.run.label) : "No test runs yet"),
    ].join("");
    dayBars($("#ovWaDay"), wa.per_day, { name: "Questions", label: "WhatsApp questions per day" });
    dayBars($("#ovWaCost"), wa.cost_per_day, { name: "Cost", label: "WhatsApp cost per day", fmt: (v) => aed(v) });
    drawHeat();
  }
  function drawHeat() {
    const d = state.data.overview;
    if (!d) return;
    if (state.heat === "voice") heatmap(d.voice.heatmap, "Voice test calls"); else heatmap(d.whatsapp.heatmap, "WhatsApp questions");
  }

  async function renderWhatsApp() {
    const d = await api(`/api/whatsapp/summary?range=${state.range}`);
    state.data.wa = d;
    const t = d.totals, n = t.questions;
    const answered = d.outcomes.find((o) => o.key === "answered").count;
    $("#waKpis").innerHTML = [
      kpi("Questions", num(n), `${num(t.people)} people · ${pct(t.arabic_share)} in Arabic`),
      kpi("Answered with sources", pct(n ? answered / n : null), `${num(answered)} questions`),
      kpi("Typical answer time", secs(t.seconds_p50), `Slowest 5%: ${secs(t.seconds_p95)} · ${num(t.slow)} over 10 s`),
      kpi("Cost", aed(t.cost_aed), `${aed(t.cost_aed_per_question, 3)} per question`),
      kpi("Asked again", num(t.asked_again), `Same question reworded within 3 min`),
      kpi("Doubted", num(t.doubted), `Followed by "are you sure?"`),
    ].join("");
    dayBars($("#waDay"), d.per_day, { name: "Questions", label: "Questions per day" });
    const tone = { answered: "--good", no_answer: "--warn", failed: "--crit", off_topic: "--ink-3", other: "--s1" };
    hbars($("#waOutcomes"), d.outcomes.map((o) => ({ l: o.label, v: o.count, t: `${num(o.count)} · ${pct(n ? o.count / n : null)}`, c: tone[o.key] })), { max: Math.max(1, n) });
    $("#waUsers").innerHTML = d.by_user.length ? d.by_user.map((u) => `<tr><td>${esc(u.name)}</td><td class="muted">${esc(u.role)}</td><td class="r num">${num(u.questions)}</td><td class="r num">${pct(u.answered_share)}</td><td class="r num">${num(u.cost_aed, 2)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty">No questions in this period.</td></tr>`;
    $("#waModels").innerHTML = d.by_model.length ? d.by_model.map((x) => `<tr><td class="num">${esc(x.model)}</td><td class="r num">${num(x.questions)}</td><td class="r num">${num(x.cost_aed, 2)}</td><td class="r num">${secs(x.seconds_p50)}</td></tr>`).join("") : `<tr><td colspan="4" class="empty">No questions in this period.</td></tr>`;
    hbars($("#waModules"), d.top_modules.map((x) => ({ l: x.name, v: x.count })), { empty: "No sources saved in this period." });
    renderFilters();
    await renderQuestions();
  }
  function renderFilters() {
    const outs = Object.entries(state.me.outcomes);
    const chips = [["", "", "All"], ...outs.map(([k, l]) => ["outcome", k, l]), ["flag", "slow", "Slow"], ["flag", "asked_again", "Asked again"], ["flag", "doubted", "Doubted"]];
    $("#waFilters").innerHTML = chips.map(([kind, key, label]) => {
      const on = kind === "" ? !state.wa.outcome && !state.wa.flag : state.wa[kind] === key;
      return `<button type="button" data-kind="${kind}" data-key="${esc(key)}" aria-pressed="${on}">${esc(label)}</button>`;
    }).join("");
  }
  async function renderQuestions() {
    const w = state.wa, admin = state.me.role === "admin";
    const qs = new URLSearchParams({ range: state.range, outcome: w.outcome, flag: w.flag, q: w.q, offset: w.offset, limit: 25 });
    const d = await api(`/api/whatsapp/questions?${qs}`);
    const flagLabel = { slow: "Slow", asked_again: "Asked again", doubted: "Doubted" };
    const tone = { answered: "good", no_answer: "warn", failed: "crit", off_topic: "", other: "" };
    $("#waRows").innerHTML = d.items.length ? d.items.map((r) => `<tr class="click" tabindex="0" data-q="${r.id}"><td class="num muted">${esc(when(r.ts))}</td><td>${esc(r.name)}</td><td class="q">${admin ? esc(r.question) : '<span class="muted">Hidden for viewers</span>'}</td><td><span class="pill ${tone[r.outcome]}">${esc(r.outcome_label)}</span></td><td><div class="tags">${r.flags.map((f) => `<span class="pill serious">${esc(flagLabel[f])}</span>`).join("")}</div></td><td class="r num">${secs(r.seconds)}</td><td class="r num">${num(r.cost_aed, 3)}</td></tr>`).join("") : `<tr><td colspan="7" class="empty">No questions match.</td></tr>`;
    const from = d.total ? w.offset + 1 : 0, to = Math.min(w.offset + 25, d.total);
    $("#waPager").innerHTML = `<span>${from}–${to} of ${num(d.total)}</span><button type="button" data-p="-1" ${w.offset === 0 ? "disabled" : ""}>Newer</button><button type="button" data-p="1" ${to >= d.total ? "disabled" : ""}>Older</button>`;
  }
  async function openQuestion(id) {
    const r = await api(`/api/whatsapp/question?id=${encodeURIComponent(id)}`);
    const admin = state.me.role === "admin";
    $("#dTitle").textContent = admin ? r.question : `Question #${r.id}`;
    $("#dMeta").innerHTML = [r.name, r.role, when(r.ts), r.model].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    let b = `<div class="d-sum"><div><span>Result</span><b>${esc(r.outcome_label)}</b></div><div><span>Time</span><b>${secs(r.seconds)}</b></div><div><span>Cost</span><b>${aed(r.cost_aed, 3)}</b></div><div><span>Sources</span><b>${num(r.source_count)}</b></div></div>`;
    if (r.flags.length) b += `<div class="note">Signals: ${r.flags.map((f) => ({ slow: "slower than 10 s", asked_again: "the person asked again in other words", doubted: 'the person replied "are you sure?"' }[f])).map(esc).join("; ")}.</div>`;
    if (admin) {
      b += `<div class="block"><h4>Question</h4><div class="text">${esc(r.question)}</div></div>`;
      b += `<div class="block"><h4>Answer</h4><div class="text answer">${esc(r.answer)}</div></div>`;
      b += `<div class="block"><h4>Sources</h4>${r.sources && r.sources.length ? `<div class="code">${esc(r.sources.map((s) => typeof s === "object" ? `${s.module || "?"} · ${s.id || ""}` : String(s)).join("\n"))}</div>` : '<div class="muted">No sources saved.</div>'}</div>`;
      b += `<div class="block"><h4>Language · IP</h4><div class="muted">${esc(r.language === "ar" ? "Arabic" : "English")} · ${esc(r.ip || "–")}</div></div>`;
    } else {
      b += `<div class="note">Question and answer text are visible to admins only.</div>`;
    }
    b += `<div class="note"><b>Not saved yet:</b> which tools the agent ran, what each returned, token counts, and whether it was confident. Phase 2 adds these steps.</div>`;
    openDrawer(b);
  }

  async function renderVoice() {
    const { runs } = await api("/api/voice/runs");
    const sel = $("#runSelect");
    if (!runs.length) {
      sel.innerHTML = "<option>No runs found</option>";
      $("#runHint").textContent = "No test logs found. Check VOICE_RUNS_DIR in .env.";
      ["#vModels", "#vMatrix"].forEach((s) => ($(s).innerHTML = ""));
      return;
    }
    if (!state.run || !runs.some((r) => r.id === state.run)) state.run = (runs.find((r) => r.kind === "test") || runs[0]).id;
    sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}" ${r.id === state.run ? "selected" : ""}>${esc(r.label)} · ${r.calls} calls</option>`).join("");
    const d = await api(`/api/voice/run?id=${encodeURIComponent(state.run)}`);
    const run = d.run;
    $("#runHint").textContent = `${run.calls} calls · models: ${run.models.join(", ")} · modes: ${run.modes.join(", ")}`;
    $("#targetsHint").textContent = `Good enough = field accuracy ≥ ${pct(d.targets.field_accuracy)} and ≥ ${pct(d.targets.pass_rate)} of scenarios passed (same rule as the test report). "Code stepped in" = price guard + post-call fills.`;
    const dur = (v) => (v == null ? "–" : v >= 60 ? `${Math.floor(v / 60)} m ${Math.round(v % 60)} s` : `${Number(v).toFixed(1)} s`);
    $("#vModels").innerHTML = d.models.map((x) => `<tr><td class="num">${esc(x.model)}</td><td>${x.verdict ? `<span class="pill ${x.verdict === "GOOD ENOUGH" ? "good" : "crit"}">${esc(x.verdict)}</span>` : '<span class="muted">Chat only</span>'}</td><td class="r num">${x.tests ? `${x.passed}/${x.tests}` : "–"}</td><td class="r num">${pct(x.field_accuracy, 1)}</td><td class="r num">${x.agent_closed}/${x.calls}</td><td class="r num">${dur(x.duration_median)}</td><td class="r num">${x.tool_rejected}/${x.tool_calls}</td><td class="r num">${x.guards + x.post_call_fills}</td><td class="r num">${x.errors}</td></tr>`).join("");
    $("#vWaits").innerHTML = d.models.map((x) => `<tr><td class="num">${esc(x.model)}</td><td class="r num">${secs(x.wait_min)}</td><td class="r num">${secs(x.wait_median)}</td><td class="r num">${secs(x.wait_max)}</td><td>${x.slowest ? `<button type="button" class="linkish" data-call="${esc(x.slowest.call)}">${esc(x.slowest.scenario)}</button> <span class="muted">· ${esc(x.slowest.reason)}</span>` : "–"}</td><td class="r num">${x.model_calls_per_reply == null ? "–" : num(x.model_calls_per_reply, 1)}</td><td class="r num">${x.thinking_replies}</td><td class="r num">${x.tool_as_text}</td></tr>`).join("");
    const models = d.models.map((x) => x.model);
    $("#vClasses").innerHTML = `<thead><tr><th>Classification</th>${models.map((mo) => `<th class="r">${esc(mo)}</th>`).join("")}<th class="r">Total</th></tr></thead><tbody>` +
      (d.classification.map((c) => `<tr><td>${esc(c.label)}</td>${models.map((mo) => `<td class="r num">${c.by_model[mo] || 0}</td>`).join("")}<td class="r num"><b>${c.total}</b></td></tr>`).join("") || `<tr><td class="empty">No calls.</td></tr>`) + "</tbody>";
    $("#slowHint").textContent = `Main reason for each reply that took ${d.slow_reply_seconds} s or more. On a real phone call, people start talking over the agent after about that long.`;
    hbars($("#vSlow"), d.slow_reasons.map((r) => ({ l: r.label, v: r.count, c: "--s2" })), { empty: `No reply took ${d.slow_reply_seconds} s or more.` });
    const head = `<thead><tr><th>Scenario</th>${d.columns.map((c) => `<th class="cell">${esc(c.model)}<br><span class="muted">${esc(c.mode)}</span></th>`).join("")}</tr></thead>`;
    const body = d.matrix.map((row) => `<tr><td><b>${esc(row.scenario)}</b><br><span class="muted">${esc(row.tests)}</span></td>${row.cells.map((c) => {
      if (!c) return `<td class="cell"><span class="muted">–</span></td>`;
      const cls = c.passed === true ? "pass" : c.passed === false ? "fail" : "na";
      const label = c.passed === true ? "PASS" : c.passed === false ? (c.wrong_fields.length ? `${c.wrong_fields.length} wrong` : "FAIL") : "open";
      const title = c.wrong_fields.length ? `Wrong: ${c.wrong_fields.join(", ")}` : `Ended by ${c.ended_by}`;
      return `<td class="cell"><button type="button" class="${cls}" data-call="${esc(c.id)}" title="${esc(title)}">${esc(label)}</button></td>`;
    }).join("")}</tr>`).join("");
    $("#vMatrix").innerHTML = head + "<tbody>" + body + "</tbody>";
    hbars($("#vWrong"), d.wrong_fields.map((w) => ({ l: w.field, v: w.total, c: "--crit" })), { empty: "No wrong fields in this run." });
    hbars($("#vEnded"), d.ended_by.map((e) => ({ l: e.ended_by, v: e.total, c: e.ended_by === "end_call" ? "--good" : "--s2" })));
  }
  async function openCall(id) {
    const c = await api(`/api/voice/call?id=${encodeURIComponent(id)}`);
    const admin = state.me.role === "admin";
    const dur = (v) => (v == null ? "–" : v >= 60 ? `${Math.floor(v / 60)} m ${Math.round(v % 60)} s` : `${Number(v).toFixed(1)} s`);
    $("#dTitle").textContent = `${c.scenario} · ${c.model}`;
    $("#dMeta").innerHTML = [c.mode, c.lead, when(c.ts), c.tests].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    const res = c.passed === true ? "Passed" : c.passed === false ? "Failed" : "Chat";
    const box = (k, v) => `<div><span>${esc(k)}</span><b>${v}</b></div>`;
    let b = `<div class="d-sum">` + [
      box("Test", res), box("Classification", esc(c.classification)),
      box("Agent closed it", c.agent_closed ? "Yes" : `No · ${esc(c.ended_by)}`), box("Call length", dur(c.duration_seconds)),
      box("Wait: shortest", secs(c.wait_min)), box("Wait: typical", secs(c.wait_median)), box("Wait: longest", secs(c.wait_max)),
      box("Model calls", c.model_calls == null ? "–" : num(c.model_calls)),
    ].join("") + `</div>`;
    if (c.slowest_reason) b += `<div class="note"><b>Longest wait:</b> ${secs(c.wait_max)} · ${esc(c.slowest_reason)}</div>`;
    if (c.wrong_fields.length) b += `<div class="note"><b>Wrong fields:</b> ${esc(c.wrong_fields.join(", "))}</div>`;
    const waits = c.waits || [];
    if (admin && c.events) {
      let i = 0;
      b += `<div class="block"><h4>Conversation, tools and thinking</h4><div class="tx">` + c.events.map((e) => {
        if (e.type === "lead") return `<div class="line lead"><small>Lead · ${esc((e.time || "").slice(11, 19))}</small>${esc(e.text)}</div>`;
        if (e.type === "agent") {
          const w = waits[i++] || {};
          const calls = w.model_calls > 1 ? ` · ${w.model_calls} model calls` : "";
          const think = (e.thinking || []).map((t) => `<details class="think"><summary>Thinking (${t.length} characters, not spoken)</summary><div class="code">${esc(t)}</div></details>`).join("");
          return `${think}<div class="line agent"><small>Agent · ${esc((e.time || "").slice(11, 19))} · waited ${secs(w.seconds)} · ${esc(w.label || "")}${calls}</small>${esc(e.text)}</div>`;
        }
        if (e.type === "tool") {
          const bad = e.ok === false || /^(Not saved|Unknown tool|Not ended)/.test(e.result || "");
          return `<div class="line tool ${bad ? "bad" : ""}">${bad ? "✗" : "✓"} ${esc(e.tool)}(${esc(JSON.stringify(e.args || {}))}) → ${esc(e.result)}${e.seconds != null ? ` <span class="muted">· ${secs(e.seconds)}</span>` : ""}</div>`;
        }
        if (e.type === "note") { const t = e.text || ""; const k = t.startsWith("guard:") ? "guard" : t.startsWith("ERROR") ? "error" : t.startsWith("post-call check:") && t.includes("=") ? "post_call_fill" : ""; return `<div class="line notel ${k}">${esc(t)}</div>`; }
        return "";
      }).join("") + `</div></div>`;
      if (c.final_row) b += `<div class="block"><h4>Result saved in the CRM</h4><div class="code">${esc(Object.entries(c.final_row).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}: ${v}`).join("\n"))}</div></div>`;
    } else {
      b += `<div class="block"><h4>Each reply</h4><div class="tbl-wrap"><table><thead><tr><th>#</th><th class="r">Waited</th><th>Why</th><th class="r">Model calls</th></tr></thead><tbody>${waits.map((w, n) => `<tr><td class="num">${n + 1}</td><td class="r num">${secs(w.seconds)}</td><td>${esc(w.label)}</td><td class="r num">${w.model_calls == null ? "–" : w.model_calls}</td></tr>`).join("")}</tbody></table></div></div>`;
      b += `<div class="note">The conversation, tool calls and thinking are visible to admins only.</div>`;
    }
    if (waits.length && waits.every((w) => w.reason === "not_recorded")) b += `<div class="note">This call was logged before the voice test recorded timings, so the reason for each wait isn't known. New test runs record it.</div>`;
    if (c.prompt_version || c.settings) b += `<div class="block"><h4>Version and settings</h4><div class="code">${esc(`prompt ${c.prompt_version || "?"} · code ${c.code_version || "?"}\n` + Object.entries(c.settings || {}).map(([k, v]) => `${k}: ${v}`).join("\n"))}</div></div>`;
    openDrawer(b);
  }

  const dur = (v) => (v == null ? "–" : v >= 60 ? `${Math.floor(v / 60)} m ${Math.round(v % 60)} s` : `${Number(v).toFixed(1)} s`);
  async function renderVoiceCalls() {
    const d = await api(`/api/voicecalls/summary?range=${state.range}`);
    const t = d.totals;
    if (!d.status.found) {
      $("#vcKpis").innerHTML = `<div class="note">No call records found in ${esc(d.status.data_dir)}/calls. Check VOICE_AGENT_DATA_DIR (python server.py --setup), or make a call with the voice agent.</div>`;
    } else {
      $("#vcKpis").innerHTML = [
        kpi("Calls", num(t.calls), `${num(t.reached)} reached the lead`),
        kpi("Agent closed the call", pct(t.reached ? t.agent_closed / t.reached : null), `${num(t.agent_closed)} of ${num(t.reached)} reached`),
        kpi("Typical call length", dur(t.duration_median), "calls that reached the lead"),
        kpi("Tokens per call", num(t.tokens_per_call), `${num(t.tokens_per_second, 1)} tokens/s · ${num(t.tokens_total)} in total`),
        kpi("Asked for a person", num(t.transfer_asked), `${num(t.transferred)} transferred`),
        kpi("Recorded", num(t.recorded), `Agent asked the caller to repeat: ${num(t.repeat_requests)}`),
      ].join("");
    }
    $("#vcStages").innerHTML = d.stages.map((s, i) => `<tr><td>${i === 0 ? "<b>" : ""}${esc(s.label)}${i === 0 ? "</b>" : ""}</td><td class="r num">${secs(s.min)}</td><td class="r num">${secs(s.median)}</td><td class="r num">${secs(s.p95)}</td><td class="r num">${secs(s.max)}</td><td class="r num">${num(s.turns)}</td></tr>`).join("");
    const lineTone = { answered: "--good", busy: "--warn", no_answer: "--serious", browser: "--ink-3" };
    hbars($("#vcLine"), d.line_results.map((x) => ({ l: x.label, v: x.count, c: lineTone[x.key] || "--crit" })), { empty: "No calls in this period." });
    hbars($("#vcClass"), d.classification.map((x) => ({ l: x.label, v: x.count, c: "--s2" })), { empty: "No calls in this period." });
    hbars($("#vcEnded"), d.ended_by.map((x) => ({ l: x.label, v: x.count, c: x.label.startsWith("Agent") ? "--good" : "--s1" })), { empty: "No calls." });
    $("#vcSlowHint").textContent = `Turns where the caller waited ${d.slow_turn_seconds} s or more`;
    hbars($("#vcSlow"), d.slowest_stage.map((x) => ({ l: x.label, v: x.count, c: "--crit" })), { empty: `No turn took ${d.slow_turn_seconds} s or more.` });
    const sp = d.speech;
    const runCard = (r, title) => {
      if (!r) return `<div class="li"><div>${esc(title)}<small>Not run yet</small></div><span class="pill">–</span></div>`;
      const s = r.summary || {};
      return `<div class="li"><div><button type="button" class="linkish" data-speech="${esc(r.id)}">${esc(title)}</button><small>${esc(when(r.run_at))} · STT ${esc(r.models.stt || "?")}${r.models.tts ? ` · TTS ${esc(r.models.tts)} (${esc(r.models.voice || "")})` : ""} · ${num(s.items)} items</small><small>Accuracy ${pct(s.accuracy, 1)} · key terms ${pct(s.key_term_accuracy, 1)} · STT ${secs(s.stt_seconds_avg)} per item${s.tts_seconds_avg != null ? ` · TTS ${secs(s.tts_seconds_avg)}` : ""}</small></div><span class="pill ${s.verdict === "GOOD ENOUGH" ? "good" : "crit"}">${esc(s.verdict || "–")}</span></div>`;
    };
    $("#vcSpeech").innerHTML = `<div class="list">${runCard(sp.recordings, "Real recordings (speech-to-text)")}${runCard(sp.round_trip, "Round trip (text-to-speech → speech-to-text)")}</div><div class="note">Targets: accuracy ≥ 90%, key terms (names, areas, amounts) ≥ 95%. Real recordings are the result to trust.</div>`;
    $("#vcModels").innerHTML = d.by_model.map((x) => `<tr><td class="num">${esc(x.model)}</td><td class="r num">${x.calls}</td><td class="r num">${secs(x.ttft_median)}</td><td class="r num">${secs(x.e2e_median)}</td><td class="r num">${num(x.tokens_per_call)}</td><td class="r num">${x.qualified}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">No answered calls.</td></tr>`;
    const lineCls = { answered: "good", busy: "warn", no_answer: "serious", browser: "" };
    $("#vcRows").innerHTML = d.calls.map((c) => `<tr class="click" tabindex="0" data-vcall="${esc(c.id)}"><td class="num muted">${esc(when(c.ts))}</td><td>${esc(c.lead_id)}</td><td><span class="pill ${lineCls[c.line_result] ?? "crit"}">${esc(c.line_label)}</span></td><td>${esc(c.classification_label)}</td><td class="muted">${esc(c.ended_by_label)}</td><td class="r num">${dur(c.duration_seconds)}</td><td class="r num">${secs(c.ttft_median)}</td><td class="r num">${c.tokens_total ? num(c.tokens_total) : "–"}</td><td>${c.recorded ? '<span class="pill good">Yes</span>' : c.recording ? '<span class="pill crit">Failed</span>' : '<span class="muted">–</span>'}</td><td>${c.transfer_asked ? `<span class="pill ${c.transferred ? "good" : "warn"}">${c.transferred ? "Transferred" : "Asked"}</span>` : '<span class="muted">–</span>'}</td></tr>`).join("") || `<tr><td colspan="10" class="empty">No calls in this period.</td></tr>`;
  }
  async function openVoiceCall(id) {
    const c = await api(`/api/voicecalls/call?id=${encodeURIComponent(id)}`);
    const admin = state.me.role === "admin";
    const box = (k, v) => `<div><span>${esc(k)}</span><b>${v}</b></div>`;
    $("#dTitle").textContent = `${c.lead_id} · ${c.classification_label}`;
    $("#dMeta").innerHTML = [when(c.ts), c.room, c.models.llm, c.models.stt, c.models.tts].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    let b = `<div class="d-sum">` + [
      box("Phone line", esc(c.line_label)), box("Ended", esc(c.ended_by_label)), box("Call length", dur(c.duration_seconds)),
      box("Tokens", !c.tokens_total ? "–" : `${num(c.tokens_in)} in · ${num(c.tokens_out)} out`),
      box("First token: shortest", secs(c.ttft_min)), box("First token: typical", secs(c.ttft_median)),
      box("First token: longest", secs(c.ttft_max)), box("Caller waits: typical", secs(c.e2e_median)),
    ].join("") + `</div>`;
    if (c.line) b += `<div class="note"><b>Phone line:</b> ${esc(c.line.label || c.line_label)} · SIP ${esc(c.line.sip_status_code ?? "–")} ${esc(c.line.sip_status || "")} · rang ${secs(c.line.ring_seconds)} · ${esc(c.line.number || "")}</div>`;
    if (c.recording) b += `<div class="note"><b>Recording:</b> ${c.recording.ok ? `${esc(c.recording.file)} (${esc(c.recording.storage || "")})` : `not recorded: ${esc(c.recording.error || "")}`}</div>`;
    if (c.transfer) b += `<div class="note"><b>Asked for a person:</b> ${c.transfer.ok ? `transferred to ${esc(c.transfer.to)}` : `not transferred (${esc(c.transfer.error || "")})`}${c.transfer.reason ? ` · reason: ${esc(c.transfer.reason)}` : ""}</div>`;
    if (c.error) b += `<div class="note"><b>Post-call check failed:</b> ${esc(c.error)}</div>`;
    if (c.turns && c.turns.length) {
      b += `<div class="block"><h4>Every turn</h4><div class="tbl-wrap"><table><thead><tr><th>#</th><th class="r">Caller waits</th><th class="r">STT</th><th class="r">First token</th><th class="r">TTS</th><th class="r">Tokens in / out</th><th class="r">Tok/s</th><th>Slowest</th></tr></thead><tbody>` +
        c.turns.map((t) => `<tr><td class="num">${t.turn}</td><td class="r num">${secs(t.end_to_end)}</td><td class="r num">${secs(t.eou)}</td><td class="r num">${secs(t.llm_ttft)}${t.llm_calls > 1 ? ` <span class="muted">×${t.llm_calls}</span>` : ""}</td><td class="r num">${secs(t.tts_ttfb)}</td><td class="r num">${num(t.prompt_tokens)} / ${num(t.completion_tokens)}</td><td class="r num">${num(t.tokens_per_second, 1)}</td><td class="muted">${esc(t.slowest_stage || "–")}</td></tr>`).join("") + `</tbody></table></div></div>`;
    } else {
      b += `<div class="note">No per-turn timings in this record (made before the agent recorded them, or text mode without audio).</div>`;
    }
    if (admin && c.transcript) {
      if (c.summary) b += `<div class="block"><h4>Summary for the sales team</h4><div class="text answer">${esc(c.summary)}</div></div>`;
      b += `<div class="block"><h4>Tool calls</h4>${c.tool_calls.length ? `<div class="tx">${c.tool_calls.map((t) => `<div class="line tool ${t.is_error ? "bad" : ""}">${t.is_error ? "✗" : "✓"} ${esc(t.name)}(${esc(t.arguments || "")}) → ${esc(t.output || "")} <span class="muted">· ${esc((t.at || "").slice(11, 19))}</span></div>`).join("")}</div>` : '<div class="muted">No tool calls recorded.</div>'}</div>`;
      b += `<div class="block"><h4>Conversation</h4><div class="tx">${c.transcript.map((t) => `<div class="line ${t.role === "agent" ? "agent" : "lead"}"><small>${t.role === "agent" ? "Agent" : "Lead"}</small>${esc(t.text)}</div>`).join("") || '<div class="muted">No conversation.</div>'}</div></div>`;
      if (Object.keys(c.filled_after_call || {}).length) b += `<div class="block"><h4>Filled in after the call</h4><div class="code">${esc(Object.entries(c.filled_after_call).map(([k, v]) => `${k}: ${v}`).join("\n"))}</div></div>`;
      if ((c.disagreements || []).length) b += `<div class="block"><h4>Agent and post-call check disagreed</h4><div class="code">${esc(c.disagreements.map((x) => JSON.stringify(x)).join("\n"))}</div></div>`;
    } else {
      b += `<div class="note">The conversation, tool calls and summary are visible to admins only.</div>`;
    }
    openDrawer(b);
  }
  async function openSpeechRun(id) {
    const r = await api(`/api/speech/run?id=${encodeURIComponent(id)}`);
    const admin = state.me.role === "admin";
    const s = r.summary || {};
    $("#dTitle").textContent = r.mode === "recordings" ? "Speech test: real recordings" : "Speech test: round trip";
    $("#dMeta").innerHTML = [when(r.run_at), `STT ${r.models.stt || "?"}`, r.models.tts && `TTS ${r.models.tts}`, r.language].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    let b = `<div class="d-sum"><div><span>Accuracy</span><b>${pct(s.accuracy, 1)}</b></div><div><span>Key terms</span><b>${pct(s.key_term_accuracy, 1)}</b></div><div><span>Word error rate</span><b>${pct(s.wer, 1)}</b></div><div><span>Verdict</span><b>${esc(s.verdict || "–")}</b></div></div>`;
    b += `<div class="block"><h4>Worst first</h4><div class="tbl-wrap"><table><thead><tr><th>Item</th><th class="r">WER</th>${admin ? "<th>Said</th><th>Heard</th><th>Missed</th>" : ""}</tr></thead><tbody>` +
      r.items.map((i) => `<tr><td class="num">${esc(i.id)}</td><td class="r num">${pct(i.wer)}</td>${admin ? `<td class="q">${esc(i.reference)}</td><td class="q">${esc(i.transcript)}</td><td>${esc((i.missed_key_terms || []).join(", "))}</td>` : ""}</tr>`).join("") + `</tbody></table></div></div>`;
    openDrawer(b);
  }

  async function renderLogins() {
    const d = await api(`/api/whatsapp/logins?range=${state.range}`);
    $("#lgKpis").innerHTML = [
      kpi("Login attempts", num(d.total), `${num(d.people)} people signed in`),
      kpi("Failed", num(d.failed), d.total ? `${pct(d.failed / d.total)} of attempts` : ""),
    ].join("");
    dayBars($("#lgFails"), d.fails_per_day, { name: "Failed", label: "Failed logins per day", color: "--crit", empty: "No failed logins in this period." });
    hbars($("#lgIds"), d.top_failed_identities.map((x) => ({ l: x.name, v: x.count, c: "--crit" })), { empty: "No failed logins." });
    hbars($("#lgIps"), d.top_failed_ips.map((x) => ({ l: x.name, v: x.count, c: "--serious" })), { empty: "" });
    $("#lgRows").innerHTML = d.recent.length ? d.recent.map((r) => `<tr><td class="num muted">${esc(when(r.ts))}</td><td>${esc(r.identity)}</td><td><span class="pill ${r.success ? "good" : "crit"}">${r.success ? "Success" : "Failed"}</span></td><td class="muted">${esc(r.reason)}</td><td class="num">${esc(r.ip)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty">No login attempts in this period.</td></tr>`;
  }

  // ---------- drawer ----------
  let lastFocus = null;
  function openDrawer(html) {
    lastFocus = document.activeElement;
    $("#dBody").innerHTML = html;
    $("#scrim").hidden = false; $("#drawer").hidden = false; $("#dClose").focus();
  }
  function closeDrawer() { $("#scrim").hidden = true; $("#drawer").hidden = true; if (lastFocus) lastFocus.focus(); }
  $("#dClose").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });

  // ---------- navigation ----------
  const renderers = { overview: renderOverview, whatsapp: renderWhatsApp, calls: renderVoiceCalls, voice: renderVoice, logins: renderLogins };
  async function go(page) {
    if (page === "logins" && state.me.role !== "admin") page = "overview";
    state.page = page;
    $$("[data-view]").forEach((v) => (v.hidden = v.dataset.view !== page));
    $$(".nav button").forEach((b) => (b.dataset.page === page ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    $("#pageTitle").textContent = titles[page][0];
    $("#pageSub").textContent = titles[page][1];
    $("#range").hidden = page === "voice";
    try { localStorage.setItem("evdash-page", page); } catch (e) { /* storage blocked */ }
    showError(null);
    try { await renderers[page](); } catch (e) { showError(e); }
  }
  $$(".nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  $("#range").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.range = b.dataset.range; state.wa.offset = 0;
    $$("#range button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    go(state.page);
  });
  $("#heatSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.heat = b.dataset.k;
    $$("#heatSeg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    drawHeat();
  });
  $("#waFilters").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.wa.outcome = b.dataset.kind === "outcome" ? b.dataset.key : "";
    state.wa.flag = b.dataset.kind === "flag" ? b.dataset.key : "";
    state.wa.offset = 0;
    renderFilters(); renderQuestions().catch(showError);
  });
  let searchT;
  $("#waSearch").addEventListener("input", (e) => {
    clearTimeout(searchT);
    searchT = setTimeout(() => { state.wa.q = e.target.value.trim(); state.wa.offset = 0; renderQuestions().catch(showError); }, 250);
  });
  $("#waPager").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-p]"); if (!b || b.disabled) return;
    state.wa.offset = Math.max(0, state.wa.offset + Number(b.dataset.p) * 25);
    renderQuestions().catch(showError);
  });
  $("#runSelect").addEventListener("change", (e) => { state.run = e.target.value; renderVoice().catch(showError); });
  document.addEventListener("click", (e) => {
    const q = e.target.closest("tr[data-q]"); if (q) openQuestion(q.dataset.q).catch(showError);
    const c = e.target.closest("button[data-call]"); if (c) openCall(c.dataset.call).catch(showError);
    const vc = e.target.closest("tr[data-vcall]"); if (vc) openVoiceCall(vc.dataset.vcall).catch(showError);
    const sr = e.target.closest("button[data-speech]"); if (sr) openSpeechRun(sr.dataset.speech).catch(showError);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = e.target.closest && e.target.closest("tr[data-q]"); if (q) openQuestion(q.dataset.q).catch(showError);
    const vc = e.target.closest && e.target.closest("tr[data-vcall]"); if (vc) openVoiceCall(vc.dataset.vcall).catch(showError);
  });
  let resizeT;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => go(state.page), 200); });

  // ---------- start ----------
  (async function start() {
    try {
      state.me = await api("/api/me");
    } catch (e) { showError(e); return; }
    $("#whoami").textContent = `${state.me.name} · ${state.me.role}`;
    if (state.me.role !== "admin") $$("[data-admin]").forEach((b) => (b.hidden = true));
    let page = "overview";
    try { const p = localStorage.getItem("evdash-page"); if (p && renderers[p]) page = p; } catch (e) { /* storage blocked */ }
    go(page);
  })();
})();
