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
    $("#vModels").innerHTML = d.models.map((x) => `<tr><td class="num">${esc(x.model)}</td><td>${x.verdict ? `<span class="pill ${x.verdict === "GOOD ENOUGH" ? "good" : "crit"}">${esc(x.verdict)}</span>` : '<span class="muted">Chat only</span>'}</td><td class="r num">${x.tests ? `${x.passed}/${x.tests}` : "–"}</td><td class="r num">${pct(x.field_accuracy, 1)}</td><td class="r num">${x.ended_properly}/${x.calls}</td><td class="r num">${secs(x.seconds_per_reply)}</td><td class="r num">${secs(x.seconds_p95)}</td><td class="r num">${x.tool_rejected}/${x.tool_calls}</td><td class="r num">${x.guards + x.post_call_fills}</td><td class="r num">${x.errors}</td></tr>`).join("");
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
    $("#dTitle").textContent = `${c.scenario} · ${c.model}`;
    $("#dMeta").innerHTML = [c.mode, c.lead, when(c.ts), c.tests].filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
    const res = c.passed === true ? "Passed" : c.passed === false ? "Failed" : "Chat";
    let b = `<div class="d-sum"><div><span>Result</span><b>${res}</b></div><div><span>Ended by</span><b>${esc(c.ended_by)}</b></div><div><span>Replies</span><b>${c.agent_turns}</b></div><div><span>Sec / reply</span><b>${secs(c.seconds_per_reply)}</b></div></div>`;
    if (c.wrong_fields.length) b += `<div class="note"><b>Wrong fields:</b> ${esc(c.wrong_fields.join(", "))}</div>`;
    if (admin && c.events) {
      b += `<div class="block"><h4>Conversation</h4><div class="tx">` + c.events.map((e) => {
        if (e.type === "lead") return `<div class="line lead"><small>Lead · ${esc((e.time || "").slice(11))}</small>${esc(e.text)}</div>`;
        if (e.type === "agent") return `<div class="line agent"><small>Agent · ${esc((e.time || "").slice(11))} · ${secs(e.model_seconds)}</small>${esc(e.text)}</div>`;
        if (e.type === "tool") { const bad = /^(Not saved|Unknown tool|Not ended)/.test(e.result || ""); return `<div class="line tool ${bad ? "bad" : ""}">${esc(e.tool)}(${esc(JSON.stringify(e.args || {}))}) → ${esc(e.result)}</div>`; }
        if (e.type === "note") { const t = e.text || ""; const k = t.startsWith("guard:") ? "guard" : t.startsWith("ERROR") ? "error" : t.startsWith("post-call check:") && t.includes("=") ? "post_call_fill" : ""; return `<div class="line notel ${k}">${esc(t)}</div>`; }
        return "";
      }).join("") + `</div></div>`;
      if (c.final_row) b += `<div class="block"><h4>CRM row after the call</h4><div class="code">${esc(Object.entries(c.final_row).filter(([, v]) => v !== "" && v != null).map(([k, v]) => `${k}: ${v}`).join("\n"))}</div></div>`;
    } else {
      b += `<div class="note">The conversation is visible to admins only.</div>`;
    }
    b += `<div class="note"><b>Not saved yet:</b> tokens and model speed per reply, and the prompt version. Phase 1 adds them to the test logs.</div>`;
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
  const renderers = { overview: renderOverview, whatsapp: renderWhatsApp, voice: renderVoice, logins: renderLogins };
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
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = e.target.closest && e.target.closest("tr[data-q]"); if (q) openQuestion(q.dataset.q).catch(showError);
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
