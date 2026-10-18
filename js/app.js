/* Логика чата:
   слева — ввод + история, справа — область ответа и «ход запросов».

   Особенности версии:
   - История диалога хранится на сервере в папке session/ (JSON) и
     подхватывается при открытии страницы (непрерывность беседы).
   - Вверху выбраны модели (кнопки, несколько) и температура для каждой.
   - Запрос уходит на сервер вместе с выбором моделей и температур.
*/
/* ---- Подключение модулей (рефакторинг на ES-модули) ------------- */
import { initSections, autoFitSide } from "./features/sections.js";
import { initSessionGuard } from "./features/sessionGuard.js";
import { bus, registry } from "./core/bus.js";
// Общие DOM-утилиты берём из зависимого модуля core/dom.js
// (одна реализация на всё приложение, без дублирования).
import { esc, fmt } from "./core/dom.js";
// Сетевой транспорт — общий модуль core/api.js.
import { apiGet, apiPost } from "./core/api.js";

(function () {
    "use strict";

    var qEl = document.getElementById("question");
    var submit = document.getElementById("submit");
    var statusEl = document.getElementById("status");
    var streamEl = document.getElementById("stream");
    var traceEl = document.getElementById("trace");
    var modelBox = document.getElementById("model-buttons");

    // Автоматическое сжатие истории (без кнопки — включается само).
    var compactKeep = document.getElementById("compact-keep");
    // Стратегия управления контекстом (Sliding / Facts / Branch).
    var strategyGroup = document.getElementById("strategy-group");
    var strategyWindow = document.getElementById("strategy-window");
    var factsBox = document.getElementById("facts-box");
    var branchesBox = document.getElementById("branches-box");
    // ИНВАРИАНТЫ: панель правил-ограничений и автотест.
    var invariantsBox = document.getElementById("invariants-box");
    var invariantCategorySel = document.getElementById("invariant-category");
    var invariantTextInp = document.getElementById("invariant-text");
    var invariantAddBtn = document.getElementById("invariant-add");
    var invariantClearBtn = document.getElementById("invariant-clear");
    var tsTotal = document.getElementById("ts-total");
    var tsIn = document.getElementById("ts-in");
    var tsOut = document.getElementById("ts-out");
    var tsHist = document.getElementById("ts-hist");
    var tsHistPct = document.getElementById("ts-hist-pct");
    var tsModelsTitle = document.getElementById("ts-models-title");
    var tsModels = document.getElementById("ts-models");
    var tsCompacted = document.getElementById("ts-compacted");
    var tsFromSummary = document.getElementById("ts-from-summary");
    var segIn = document.getElementById("seg-in");
    var segOut = document.getElementById("seg-out");

    var busy = false;

    // Текущий диалог в окне: [{role:'user'|'assistant', content, html, answers}]
    var items = [];

    // Состояние выбранных моделей: [{label, cls, on:bool, temp, topP,
    // repPen, maxTokens, ctxWin}] — у каждой модели СВОИ параметры генерации.
    var modelState = [];
    // Значение температуры по умолчанию (минимальные/шаг берём из сервера).
    var DEFAULT_TEMP = 0.7;
    var TEMP_MIN = 0.0, TEMP_MAX = 1.0, TEMP_STEP = 0.05;
    // top-p (nucleus sampling): 1.0 — выключено.
    var DEFAULT_TOP_P = 1.0;
    var TOP_P_MIN = 0.0, TOP_P_MAX = 1.0, TOP_P_STEP = 0.05;
    // repetition penalty (штраф за повторы): 1.0 — без штрафа.
    var DEFAULT_REP_PENALTY = 1.0;
    var REP_PENALTY_MIN = 0.0, REP_PENALTY_MAX = 2.0, REP_PENALTY_STEP = 0.05;
    // max_tokens: пустое поле = «авто» (число или null).
    var MAX_TOKENS_MIN = 1, MAX_TOKENS_MAX = 100000, MAX_TOKENS_STEP = 50;
    // Размер контекстного окна (токенов): пустое поле/0 = «авто».
    var CTX_WINDOW_MIN = 0, CTX_WINDOW_MAX = 131072, CTX_WINDOW_STEP = 512;
    // Ссылка на серверные границы (перезапишем из /api/model, если придут).
    var serverParamLimits = null;

    // Состояние НАСТРОЙКИ сжатия: сколько последних сообщений хранить
    // полностью. Само сжатие (генерация summary) происходит автоматически
    // на сервере после накопления порога несжатых сообщений.
    var compactState = {
        keep: 10,
    };

    // ------------------------------------------------------------------
    // Хелперы
    // ------------------------------------------------------------------
    // --- Делегатор к модулю секций ------------------------------------
    // Сама логика сворачивания/автоподбора ширины живёт в
    // features/sections.js. Раньше refreshSections() была локальной
    // функцией; теперь это тонкая обёртка над функцией из модуля,
    // сохранённой в sectionsRefresh (см. вызов initSections() ниже).
    // Объявлена как function declaration — всплывает, поэтому доступна
    // из всех функций файла ещё до выполнения нижнего блока.
    var sectionsRefresh = null;
    function refreshSections() {
        if (sectionsRefresh) sectionsRefresh();
    }


    function setStatus(message, kind) {
        statusEl.hidden = !message;
        statusEl.textContent = message || "";
        statusEl.className = "side-status" + (kind === "error" ? " error" : (kind === "ok" ? " ok" : ""));
    }

    // Полная перерисовка потока ответов из items.
    function render() {
        streamEl.innerHTML = "";

        items.forEach(function (item) {
            if (item.role === "reminder") {
                // Напоминание — отдельное сообщение на КРАСНОМ фоне.
                var rMsg = document.createElement("div");
                rMsg.className = "msg reminder";
                var rb = document.createElement("div");
                rb.className = "bubble";
                rb.textContent = item.content;
                rMsg.appendChild(rb);
                streamEl.appendChild(rMsg);
            } else if (item.role === "assistant") {
                var msg = document.createElement("div");
                msg.className = "msg a";
                var b = document.createElement("div");
                b.className = "bubble";
                b.innerHTML = item.html || esc(item.content);
                msg.appendChild(b);
                streamEl.appendChild(msg);
            } else {
                var qMsg = document.createElement("div");
                qMsg.className = "msg q";
                var qb = document.createElement("div");
                qb.className = "bubble";
                qb.textContent = item.content;
                qMsg.appendChild(qb);
                streamEl.appendChild(qMsg);
            }
        });

        streamEl.scrollTop = streamEl.scrollHeight;
    }

    var TRACE_KIND = {
        enter: "агент", act: "работа", branch: "запрос",
        llm: "LLM", exit: "агент"
    };

    // Компактная цепочка «Ход запросов · агент»: короткие метки шагов,
    // выстроенные слева направо с переносом по строкам (без прокрутки).
    function renderTrace(trace, meta) {
        if (!traceEl) return;
        if (!trace || !trace.length) {
            traceEl.innerHTML = '<div class="trace-empty">ход: агент → работа → LLM → ответ</div>';
            return;
        }
        var wrap = document.createElement("div");
        wrap.className = "ts-chain";
        trace.forEach(function (s) {
            var label = TRACE_KIND[s.kind] || s.kind || "?";
            if (s.kind === "llm" && s.model) label = s.model;
            var span = document.createElement("span");
            var cls = "ts-chip " + (s.kind || "act");
            if (s.kind === "llm" && s.ok === false) cls += " fail";
            span.className = cls;
            span.textContent = label;
            if (s.ok === false) span.title = "ошибка";
            wrap.appendChild(span);
        });
        traceEl.innerHTML = "";
        traceEl.appendChild(wrap);
    }

    // ------------------------------------------------------------------
    // Панель выбора моделей (кнопки-переключатели + параметры генерации)
    // ------------------------------------------------------------------
    // Строит блок числового параметра (label + input number) для модели.
    // def — значение по умолчанию (null — «авто»: поле пустое), min/max/step —
    // границы, roundTo — округление (1 — целые), title — подсказка.
    function buildNumParam(text, st, key, def, min, max, step, roundTo, title) {
        var box = document.createElement("div");
        box.className = "model-param";
        var lab = document.createElement("label");
        lab.textContent = text;
        var inp = document.createElement("input");
        inp.type = "number";
        inp.min = min;
        inp.max = max;
        inp.step = step;
        inp.value = (def === null || def === undefined) ? "" : def;
        inp.title = title || "";
        inp.addEventListener("change", function () {
            var raw = inp.value.trim();
            if (raw === "") {
                // Пустое поле = «авто» (не применять параметр).
                inp.value = "";
                st[key] = null;
                return;
            }
            var v = parseFloat(raw);
            if (isNaN(v)) { st[key] = null; inp.value = ""; return; }
            if (v < min) v = min;
            if (v > max) v = max;
            if (roundTo === 1) v = Math.round(v);
            else v = Math.round(v / roundTo) * roundTo;
            inp.value = v;
            st[key] = v;
        });
        box.appendChild(lab);
        box.appendChild(inp);
        return box;
    }

    function buildModelControls(available) {
        if (!modelBox) return;
        modelBox.innerHTML = "";
        // По умолчанию модели ВЫКЛЮЧЕНЫ; локальная модель (если её сервер
        // запущен) включается автоматически — она используется по умолчанию.
        modelState = available.map(function (m) {
            return {
                label: m.label, cls: m.cls || "", local: !!m.local,
                on: false,
                temp: DEFAULT_TEMP, topP: DEFAULT_TOP_P,
                repPen: DEFAULT_REP_PENALTY,
                maxTokens: null,        // null = «авто» (пустое поле)
                ctxWin: null            // null = «авто» (пустое поле)
            };
        });
        var localState0 = modelState.find(function (m) { return m.local; });
        if (localState0) {
            localState0.on = true;
        }
        modelState.forEach(function (st) {
            var wrap = document.createElement("div");
            wrap.className = "model-ctrl " + (st.on ? "active" : "inactive");
            wrap.dataset.label = st.label;

            // кнопка с названием модели
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "m-btn";
            btn.innerHTML = '<span class="m-dot"></span>' + esc(st.label);
            btn.addEventListener("click", function () {
                st.on = !st.on;
                syncModelUI(st.label);
                updateModelTag();
            });
            wrap.appendChild(btn);

            // Параметры генерации для этой модели (одна строка полей):
            // temperature, top-p, repetition penalty, max_tokens, контекст.
            var params = document.createElement("div");
            params.className = "model-params";
            params.appendChild(buildNumParam("temp", st, "temp", DEFAULT_TEMP,
                TEMP_MIN, TEMP_MAX, TEMP_STEP, 0.01,
                "Температура генерации (0 – строго, выше – разнообразнее)"));
            params.appendChild(buildNumParam("top-p", st, "topP", DEFAULT_TOP_P,
                TOP_P_MIN, TOP_P_MAX, TOP_P_STEP, 0.01,
                "Top-p (nucleus): 1.0 — выключено; 0.9–0.95 — умеренное разнообразие"));
            params.appendChild(buildNumParam("rep", st, "repPen", DEFAULT_REP_PENALTY,
                REP_PENALTY_MIN, REP_PENALTY_MAX, REP_PENALTY_STEP, 0.01,
                "Repetition penalty (штраф за повторы): 1.0 — без штрафа"));
            params.appendChild(buildNumParam("max", st, "maxTokens", null,
                MAX_TOKENS_MIN, MAX_TOKENS_MAX, MAX_TOKENS_STEP, 1,
                "Максимум новых токенов ответа (пусто – авто)"));
            params.appendChild(buildNumParam("ctx", st, "ctxWin", null,
                CTX_WINDOW_MIN, CTX_WINDOW_MAX, CTX_WINDOW_STEP, 1,
                "Размер контекстного окна в токенах (пусто/0 – авто)"));
            wrap.appendChild(params);

            modelBox.appendChild(wrap);
        });
        modelState.forEach(function (st) { syncModelUI(st.label); });
        updateModelTag();
    }

    // Изменяет внешний вид карточки модели по текущему состоянию вкл/выкл.
    function syncModelUI(label) {
        var st = modelState.find(function (m) { return m.label === label; });
        if (!modelBox || !st) return;
        var cards = modelBox.querySelectorAll('.model-ctrl');
        for (var i = 0; i < cards.length; i++) {
            if (cards[i].dataset.label === label) {
                cards[i].classList.toggle("active", st.on);
                cards[i].classList.toggle("inactive", !st.on);
            }
        }
    }

    function updateModelTag() {
        var on = modelState.filter(function (m) { return m.on; }).map(function (m) { return m.label; });
        renderModelsTitle(on);
    }

    // Заголовок блока статистики («Токены по моделям») приводим в
    // соответствие с реально используемыми моделями.
    function renderModelsTitle(labels) {
        if (!tsModelsTitle) return;
        var on = labels || modelState
            .filter(function (m) { return m.on; })
            .map(function (m) { return m.label; });
        if (!on.length) {
            tsModelsTitle.textContent = "Токены по моделям";
            return;
        }
        tsModelsTitle.innerHTML = 'Токены по моделям <span class="tstat-head-models">' +
            esc(on.join(" · ")) + "</span>";
    }

    // Собирает список выбранных моделей для отправки на сервер.
    // У каждой модели — СВОИ параметры генерации (temp/top_p/rep/max/ctx).
    // Пустые (null) поля означают «не применять параметр» — сервер сам
    // подставит значение по умолчанию модели.
    function selectedModelsPayload() {
        return modelState
            .filter(function (m) { return m.on; })
            .map(function (m) {
                return {
                    label: m.label,
                    temperature: m.temp,
                    top_p: m.topP,
                    repetition_penalty: m.repPen,
                    max_tokens: m.maxTokens,
                    context_window: m.ctxWin
                };
            });
    }

    // ------------------------------------------------------------------
    // Управление контекстом (автоматическое сжатие истории)
    // ------------------------------------------------------------------
    // Настройка: сколько последних сообщений сохранять полностью.
    // Ранняя история автоматически заменяется на summary на сервере —
    // отдельной кнопки «Сжать» нет.

    function getCompactPayload() {
        // Сжатие всегда включено (автоматическое). От клиента передаём
        // только настройку keep — сколько последних сообщений оставлять.
        // keep = 0 → сжатие НЕ применяется (ни подстановка summary, ни автосжатие).
        var keep = parseInt(compactKeep ? compactKeep.value : compactState.keep, 10);
        if (isNaN(keep) || keep < 0) keep = compactState.keep;
        if (keep < 0) keep = 0;
        return { enabled: true, keep: keep };
    }

    function applyCompactFromServer(compact) {
        if (!compact) return;
        var keep = parseInt(compact.keep, 10);
        if (isNaN(keep) || keep < 0) keep = compactState.keep;
        compactState.keep = keep;
        if (compactKeep) compactKeep.value = keep;
        refreshSections();
    }

    if (compactKeep) {
        compactState.keep = parseInt(compactKeep.value, 10);
        if (isNaN(compactState.keep)) compactState.keep = 10;
        compactKeep.addEventListener("change", function () {
            var v = parseInt(compactKeep.value, 10);
            if (isNaN(v) || v < 0) v = 0;     // 0 = сжатие выключено
            if (v > 100) v = 100;
            compactKeep.value = v;
            compactState.keep = v;
            saveCompactSettings();
            refreshSections();
        });
    }

    function saveCompactSettings() {
        // Сохраняем настройку keep на сервер (сжатие включено всегда).
        fetch("/api/compact", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(getCompactPayload()),
        }).catch(function () {});
    }

    // ------------------------------------------------------------------
    // Стратегия управления контекстом: Sliding / Facts / Branch
    // ------------------------------------------------------------------
    var strategyState = { strategy: "none", window: 10 };

    function getStrategyPayload() {
        var w = parseInt(strategyWindow ? strategyWindow.value : strategyState.window, 10);
        if (isNaN(w) || w < 0) w = strategyState.window;
        return { strategy: strategyState.strategy, window: w };
    }

    function saveStrategySettings() {
        fetch("/api/strategy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(getStrategyPayload()),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (!d || !d.ok) return;
            strategyState.strategy = d.strategy.strategy;
            strategyState.window = d.strategy.window;
            syncStrategyUI();
            renderFacts(d.facts);
            renderBranches(d.branches);
            applyContextStats(d.context);
        })
        .catch(function () {});
    }

    function syncStrategyUI() {
        if (!strategyGroup) return;
        var btns = strategyGroup.querySelectorAll(".strat-btn");
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle("active",
                btns[i].dataset.strategy === strategyState.strategy);
        }
        if (strategyWindow) strategyWindow.value = strategyState.window;
        // Панели facts/веток показываем только в соответствующих стратегиях.
        var factsTitle = document.getElementById("facts-title");
        var brTitle = document.getElementById("branches-title");
        if (factsTitle) factsTitle.style.display =
            strategyState.strategy === "facts" ? "" : "none";
        if (factsBox) factsBox.style.display =
            strategyState.strategy === "facts" ? "" : "none";
        if (brTitle) brTitle.style.display =
            strategyState.strategy === "branch" ? "" : "none";
        if (branchesBox) branchesBox.style.display =
            strategyState.strategy === "branch" ? "" : "none";
        refreshSections();
    }

    function applyStrategyFromServer(strategy) {
        if (!strategy) return;
        if (strategy.strategy) strategyState.strategy = strategy.strategy;
        var w = parseInt(strategy.window, 10);
        if (!isNaN(w)) strategyState.window = w;
        syncStrategyUI();
    }

    if (strategyGroup) {
        strategyGroup.addEventListener("click", function (e) {
            var btn = e.target.closest(".strat-btn");
            if (!btn) return;
            strategyState.strategy = btn.dataset.strategy;
            syncStrategyUI();
            saveStrategySettings();
        });
    }
    if (strategyWindow) {
        strategyWindow.addEventListener("change", function () {
            var v = parseInt(strategyWindow.value, 10);
            if (isNaN(v) || v < 0) v = 0;
            if (v > 200) v = 200;
            strategyWindow.value = v;
            strategyState.window = v;
            saveStrategySettings();
        });
    }

    // ---- Факты (key-value), стратегия Facts ----
    // Каждый факт хранится в одном из слоёв ПАМЯТИ агента, выбор — у значения:
    //   "working"  — Рабочая память (данные текущей задачи);
    //   "longterm" — Долговременная память (профиль/решения/знания).
    // Память по фактам приходит из снимка памяти сервера (mem.working /
    // mem.longterm); ключ, найденный в слое, показывает этот слой в select.
    var factsMemory = {};   // {ключ: "working"|"longterm"} — текущий выбор

    function renderFacts(facts) {
        if (!factsBox) return;
        factsBox.innerHTML = "";
        var keys = facts ? Object.keys(facts) : [];
        if (!keys.length) {
            var empty = document.createElement("div");
            empty.className = "facts-empty";
            empty.textContent = "фактов пока нет (обновляются после каждого запроса)";
            factsBox.appendChild(empty);
        } else {
            keys.forEach(function (k) {
                factsBox.appendChild(factRow(k, facts[k], factsMemory[k] || "working"));
            });
        }
        var actions = document.createElement("div");
        actions.className = "facts-actions";
        var add = document.createElement("button");
        add.type = "button";
        add.textContent = "+ факт";
        add.addEventListener("click", function () {
            factsBox.insertBefore(factRow("", "", "working"), actions);
        });
        var save = document.createElement("button");
        save.type = "button";
        save.className = "primary";
        save.textContent = "Сохранить";
        save.addEventListener("click", saveFacts);
        actions.appendChild(add);
        actions.appendChild(save);
        factsBox.appendChild(actions);
    }

    function factRow(key, val, memType) {
        var row = document.createElement("div");
        row.className = "fact-row";
        var k = document.createElement("input");
        k.type = "text"; k.className = "fact-key"; k.value = key || "";
        k.placeholder = "ключ";
        var v = document.createElement("input");
        v.type = "text"; v.className = "fact-val"; v.value = val || "";
        v.placeholder = "значение";
        // Выбор памяти, в которую попадёт значение: Рабочая / Долгосрочная.
        var mem = document.createElement("select");
        mem.className = "fact-mem";
        mem.title = "В какую память попадёт факт";
        [
            { value: "working", text: "Рабочая" },
            { value: "longterm", text: "Долговременная" }
        ].forEach(function (opt) {
            var o = document.createElement("option");
            o.value = opt.value;
            o.textContent = opt.text;
            mem.appendChild(o);
        });
        mem.value = (memType === "longterm") ? "longterm" : "working";
        var del = document.createElement("button");
        del.type = "button"; del.className = "fact-del"; del.textContent = "\u00d7";
        del.title = "Удалить факт";
        del.addEventListener("click", function () { row.remove(); });
        row.appendChild(k); row.appendChild(v); row.appendChild(mem); row.appendChild(del);
        return row;
    }

    function saveFacts() {
        if (!factsBox) return;
        var out = {};
        var memMap = {};
        var rows = factsBox.querySelectorAll(".fact-row");
        for (var i = 0; i < rows.length; i++) {
            var k = rows[i].querySelector(".fact-key").value.trim();
            var v = rows[i].querySelector(".fact-val").value.trim();
            var memSel = rows[i].querySelector(".fact-mem");
            if (k) {
                out[k] = v;
                memMap[k] = memSel && memSel.value === "longterm" ? "longterm" : "working";
            }
        }
        fetch("/api/facts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ facts: out, mem_map: memMap }),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (!d || !d.ok) return;
            factsMemory = memMap;
            renderFacts(d.facts);
            if (d.memory) renderMemory(d.memory);
        })
        .catch(function () {});
    }

    // ---- Ветки диалога, стратегия Branch ----
    function renderBranches(state) {
        if (!branchesBox) return;
        branchesBox.innerHTML = "";
        if (!state || !state.branches) return;
        var active = state.active_branch || 0;
        state.branches.forEach(function (b, i) {
            var row = document.createElement("div");
            row.className = "branch-row" + (i === active ? " active" : "");
            row.title = "Клик — переключиться; двойной клик по имени — переименовать";

            var name = document.createElement("span");
            name.className = "b-name";
            name.textContent = b.name;
            name.title = "Двойной клик — переименовать ветку";
            // Двойной клик по имени — редактирование «на месте».
            name.addEventListener("dblclick", function (e) {
                e.stopPropagation();
                startRenameBranch(row, name, i, b.name);
            });
            row.appendChild(name);

            var size = document.createElement("span");
            size.className = "b-size";
            size.textContent = b.size + " сообщ.";
            row.appendChild(size);

            // Кнопка переименования (карандаш).
            var ren = document.createElement("button");
            ren.type = "button"; ren.className = "b-ren"; ren.textContent = "\u270e";
            ren.title = "Переименовать ветку";
            ren.addEventListener("click", function (e) {
                e.stopPropagation();
                startRenameBranch(row, name, i, b.name);
            });
            row.appendChild(ren);

            if (state.branches.length > 1) {
                var del = document.createElement("button");
                del.type = "button"; del.className = "b-del"; del.textContent = "\u00d7";
                del.title = "Удалить ветку";
                del.addEventListener("click", function (e) {
                    e.stopPropagation();
                    branchAction({ action: "delete", index: i });
                });
                row.appendChild(del);
            }
            row.addEventListener("click", function () {
                branchAction({ action: "switch", index: i });
            });
            branchesBox.appendChild(row);
        });

        var actions = document.createElement("div");
        actions.className = "branches-actions";
        var countInp = document.createElement("input");
        countInp.type = "number"; countInp.min = "1"; countInp.max = "20";
        countInp.value = "2"; countInp.title = "Сколько веток создать";
        var create = document.createElement("button");
        create.type = "button"; create.className = "primary";
        create.textContent = "Создать ветки от текущего";
        create.addEventListener("click", function () {
            branchAction({ action: "create", count: parseInt(countInp.value, 10) || 2 });
        });
        actions.appendChild(countInp);
        actions.appendChild(create);
        branchesBox.appendChild(actions);
    }

    // Запускает inline-редактирование имени ветки прямо в строке.
    function startRenameBranch(row, nameEl, index, currentName) {
        if (row.querySelector(".b-name-edit")) return;   // уже редактируется
        nameEl.style.display = "none";

        var inp = document.createElement("input");
        inp.type = "text";
        inp.className = "b-name-edit";
        inp.value = currentName || "";
        inp.maxLength = 80;
        row.insertBefore(inp, nameEl);
        inp.focus();
        inp.select();

        var finished = false;
        function commit() {
            if (finished) return;
            finished = true;
            var newName = inp.value.trim();
            inp.remove();
            nameEl.style.display = "";
            if (newName && newName !== currentName) {
                branchAction({ action: "rename", index: index, name: newName });
            } else {
                nameEl.textContent = currentName;   // без изменений
            }
        }
        function cancel() {
            if (finished) return;
            finished = true;
            inp.remove();
            nameEl.style.display = "";
            nameEl.textContent = currentName;
        }

        inp.addEventListener("click", function (e) { e.stopPropagation(); });
        inp.addEventListener("dblclick", function (e) { e.stopPropagation(); });
        inp.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); commit(); }
            else if (e.key === "Escape") { e.preventDefault(); cancel(); }
        });
        inp.addEventListener("blur", commit);
    }

    function branchAction(payload) {
        fetch("/api/branches", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (!d || !d.ok) return;
            renderBranches(d.branches);
            // Переименование историю не меняет. Create/switch/delete могут
            // сменить активную ветку — тогда обновляем окно из серверных данных.
            if (payload.action !== "rename" && Array.isArray(d.messages)) {
                items = d.messages.slice();
                render();
            }
        })
        .catch(function () {});
    }

    // ------------------------------------------------------------------
    // СОСТОЯНИЕ ЗАДАЧИ (Task State Machine)
    // ------------------------------------------------------------------
    // Формализованный конечный автомат: этап (planning -> execution ->
    // validation -> done) + текущий шаг + ожидаемое действие. Задачу можно
    // поставить на паузу на любом этапе и продолжить с того же места.
    var taskBox = document.getElementById("task-box");
    var taskGoalInp = document.getElementById("task-goal");
    var taskStagesBox = document.getElementById("task-stages");
    var taskStartBtn = document.getElementById("task-start");
    var taskAdvanceBtn = document.getElementById("task-advance");
    var taskPauseBtn = document.getElementById("task-pause");
    var taskResumeBtn = document.getElementById("task-resume");
    var taskFinishBtn = document.getElementById("task-finish");
    var taskResetBtn = document.getElementById("task-reset");
    var taskState = { active: false, stage: "planning", paused: false,
                      stages: ["planning", "execution", "validation", "done"],
                      stage_labels: {} };
    var taskJournalOpen = false;

    // Рисует панель задачи по снимку, пришедшему от сервера.
    function renderTask(task) {
        if (!task) return;
        taskState = task;
        if (!taskBox) return;

        var active = !!task.active;
        var paused = !!task.paused;
        var stage = task.stage || "planning";
        var labels = task.stage_labels || {};
        var stages = task.stages || ["planning", "execution", "validation", "done"];

        taskBox.innerHTML = "";
        taskBox.classList.toggle("active", active && !paused && stage !== "done");
        taskBox.classList.toggle("paused", active && paused);
        taskBox.classList.toggle("done", active && stage === "done");

        if (!active) {
            var empty = document.createElement("div");
            empty.className = "task-empty";
            empty.textContent = "задача не заведена — задайте цель ниже";
            taskBox.appendChild(empty);
        } else {
            // Цель задачи.
            var goal = document.createElement("div");
            goal.className = "task-goal";
            goal.innerHTML = "<b>Цель:</b> " + esc(task.goal || "(не указана)");
            taskBox.appendChild(goal);

            // Полоска этапов.
            var prog = document.createElement("div");
            prog.className = "task-progress";
            var curIdx = stages.indexOf(stage);
            stages.forEach(function (s, i) {
                var el = document.createElement("span");
                el.className = "task-stage";
                if (s === stage) {
                    el.classList.add("current");
                    if (paused) el.classList.add("paused");
                } else if (i < curIdx) {
                    el.classList.add("done");
                }
                el.textContent = labels[s] || s;
                prog.appendChild(el);
            });
            taskBox.appendChild(prog);

            // Бейдж статуса (пауза / активно / завершено).
            var badge = document.createElement("span");
            if (stage === "done") {
                badge.className = "task-badge done";
                badge.textContent = "завершено";
            } else if (paused) {
                badge.className = "task-badge paused";
                badge.textContent = "пауза";
            } else {
                badge.className = "task-badge active";
                badge.textContent = "в работе";
            }
            taskBox.appendChild(badge);

            // Шаг и ожидаемое действие.
            if (task.step) {
                var sEl = document.createElement("div");
                sEl.className = "task-field";
                sEl.innerHTML = "<b>Шаг:</b> " + esc(task.step);
                taskBox.appendChild(sEl);
            }
            if (task.expected) {
                var eEl = document.createElement("div");
                eEl.className = "task-field";
                eEl.innerHTML = "<b>Ожидается:</b> " + esc(task.expected);
                taskBox.appendChild(eEl);
            }
            // журнал (что уже сделано) — чтобы видеть «продолжение без
            // повторных объяснений».
            var hist = task.history || [];
            if (hist.length) {
                var jhead = document.createElement("div");
                jhead.className = "task-field";
                jhead.style.cursor = "pointer";
                jhead.textContent = (taskJournalOpen ? "▾ " : "▸ ") +
                    "журнал (" + hist.length + ")";
                jhead.addEventListener("click", function () {
                    taskJournalOpen = !taskJournalOpen;
                    renderTask(taskState);
                });
                taskBox.appendChild(jhead);
                if (taskJournalOpen) {
                    var ul = document.createElement("ul");
                    ul.className = "task-journal";
                    hist.slice(-8).forEach(function (h) {
                        var li = document.createElement("li");
                        li.textContent = "[" + (h.ts || "") + "] " +
                            (h.stage_label || h.stage || "") + ": " +
                            (h.detail || "");
                        ul.appendChild(li);
                    });
                    taskBox.appendChild(ul);
                }
            }
        }

        // Кнопки этапов: ручной переход (сервер проверит корректность).
        if (taskStagesBox) {
            taskStagesBox.innerHTML = "";
            if (active && stage !== "done") {
                stages.forEach(function (s) {
                    var b = document.createElement("button");
                    b.type = "button";
                    b.className = "stage-btn" + (s === stage ? " current" : "");
                    b.textContent = labels[s] || s;
                    b.title = "Перевести задачу на этап «" + (labels[s] || s) + "»";
                    b.addEventListener("click", function () {
                        taskAction({ action: "advance", stage: s });
                    });
                    taskStagesBox.appendChild(b);
                });
            }
        }

        // Доступность кнопок по состоянию автомата.
        var canStart = true;
        var canAdvance = active && !paused && stage !== "done";
        var canPause = active && !paused && stage !== "done";
        var canResume = active && paused;
        var canFinish = active && !paused && stage === "validation";
        var canReset = active;
        if (taskStartBtn) taskStartBtn.disabled = !canStart;
        if (taskAdvanceBtn) taskAdvanceBtn.disabled = !canAdvance;
        if (taskPauseBtn) taskPauseBtn.disabled = !canPause;
        if (taskResumeBtn) taskResumeBtn.disabled = !canResume;
        if (taskFinishBtn) taskFinishBtn.disabled = !canFinish;
        if (taskResetBtn) taskResetBtn.disabled = !canReset;
        if (taskGoalInp) taskGoalInp.disabled = active && stage !== "done";
        refreshSections();
    }

    // Отправляет действие с задачей на сервер и применяет ответ.
    function taskAction(payload) {
        return fetch("/api/task", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (d) {
            if (!d) return;
            if (d.task) renderTask(d.task);
            if (d.ok === false) {
                setStatus(d.error || "Недопустимый переход задачи.", "error");
            } else {
                var act = payload.action;
                if (act === "start") setStatus("Задача заведена.", "ok");
                else if (act === "pause") setStatus("Задача на паузе.", "ok");
                else if (act === "resume") setStatus("Задача продолжена.", "ok");
                else if (act === "finish") setStatus("Задача завершена.", "ok");
                else if (act === "reset") setStatus("Состояние задачи сброшено.", "ok");
                else if (act === "advance") setStatus("Этап задачи изменён.", "ok");
            }
        })
        .catch(function () {});
    }

    // «Следующий этап» — переход по цепочке автомата:
    // planning -> execution -> validation -> done (finish из validation).
    function nextStage() {
        var s = taskState.stage || "planning";
        if (s === "planning") return "execution";
        if (s === "execution") return "validation";
        if (s === "validation") return "done";
        return null;
    }

    if (taskStartBtn) taskStartBtn.addEventListener("click", function () {
        var goal = taskGoalInp ? taskGoalInp.value.trim() : "";
        if (!goal) { setStatus("Введите цель задачи.", "error"); return; }
        taskAction({ action: "start", goal: goal });
        if (taskGoalInp) taskGoalInp.value = "";
    });
    if (taskAdvanceBtn) taskAdvanceBtn.addEventListener("click", function () {
        var ns = nextStage();
        if (!ns) { setStatus("Задача уже завершена.", "error"); return; }
        if (ns === "done") taskAction({ action: "finish" });
        else taskAction({ action: "advance", stage: ns });
    });
    if (taskPauseBtn) taskPauseBtn.addEventListener("click", function () {
        taskAction({ action: "pause" });
    });
    if (taskResumeBtn) taskResumeBtn.addEventListener("click", function () {
        taskAction({ action: "resume" });
    });
    if (taskFinishBtn) taskFinishBtn.addEventListener("click", function () {
        taskAction({ action: "finish" });
    });
    if (taskResetBtn) taskResetBtn.addEventListener("click", function () {
        if (!window.confirm("Сбросить состояние задачи?")) return;
        taskAction({ action: "reset" });
    });

    // ------------------------------------------------------------------
    // ИНВАРИАНТЫ (правила, которые ассистент НЕ вправе нарушать)
    // ------------------------------------------------------------------
    // Инварианты хранятся ОТДЕЛЬНО от диалога и имеют КАТЕГОРИЮ:
    //   architecture — архитектура; tech — техрешения;
    //   stack — стек; business — бизнес-правила.
    // При конфликте запроса с инвариантом ассистент ОТКАЗЫВАЕТСЯ от решения
    // и объясняет причину (см. серверную пост-проверку агента).
    var invState = { invariants: [], categories: [], count: 0, max: 0 };

    // Заполняет выпадающий список категорий (один раз по данным сервера).
    function fillInvariantCategories(categories) {
        if (!invariantCategorySel || !Array.isArray(categories)) return;
        invariantCategorySel.innerHTML = "";
        categories.forEach(function (c) {
            var o = document.createElement("option");
            o.value = c.id;
            o.textContent = c.label;
            invariantCategorySel.appendChild(o);
        });
    }

    // Короткая метка категории по её коду.
    function invariantCategoryLabel(code) {
        var found = (invState.categories || []).filter(function (c) {
            return c.id === code;
        })[0];
        return found ? found.label : code;
    }

    // Рисует список инвариантов: текст + категория + удаление.
    function renderInvariants(state) {
        if (!invariantsBox) return;
        if (state && Array.isArray(state.invariants)) {
            invState = state;
        }
        if (state && Array.isArray(state.categories)) {
            fillInvariantCategories(state.categories);
        }
        var list = invState.invariants || [];
        invariantsBox.innerHTML = "";
        if (!list.length) {
            var empty = document.createElement("div");
            empty.className = "invariants-empty";
            empty.textContent = "инвариантов нет — добавьте правило ниже";
            invariantsBox.appendChild(empty);
            refreshSections();
            return;
        }
        list.forEach(function (inv) {
            var row = document.createElement("div");
            row.className = "invariant-row";

            var badge = document.createElement("span");
            badge.className = "inv-badge inv-" + (inv.category || "business");
            badge.textContent = invariantCategoryLabel(inv.category);
            row.appendChild(badge);

            var text = document.createElement("span");
            text.className = "inv-text";
            text.textContent = inv.text || "";
            text.title = inv.text || "";
            row.appendChild(text);

            var del = document.createElement("button");
            del.type = "button"; del.className = "inv-del"; del.textContent = "\u00d7";
            del.title = "Удалить инвариант";
            del.addEventListener("click", function () {
                invariantAction({ action: "delete", id: inv.id });
            });
            row.appendChild(del);

            invariantsBox.appendChild(row);
        });
        refreshSections();
    }

    // Отправляет действие с инвариантами на сервер и применяет ответ.
    function invariantAction(payload) {
        return fetch("/api/invariants", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (d) {
            if (!d) return;
            if (d.ok === false) {
                setStatus(d.error || "Не удалось выполнить действие с инвариантом.",
                          "error");
                return;
            }
            renderInvariants(d);
            var act = payload.action;
            if (act === "add") setStatus("Инвариант добавлен.", "ok");
            else if (act === "delete") setStatus("Инвариант удалён.", "ok");
            else if (act === "clear") setStatus("Инварианты очищены.", "ok");
        })
        .catch(function () { setStatus("Ошибка связи с инвариантами.", "error"); });
    }

    if (invariantAddBtn) {
        invariantAddBtn.addEventListener("click", function () {
            var text = invariantTextInp ? invariantTextInp.value.trim() : "";
            if (!text) { setStatus("Введите текст инварианта.", "error"); return; }
            var category = invariantCategorySel ? invariantCategorySel.value : "business";
            invariantAction({ action: "add", text: text, category: category });
            if (invariantTextInp) { invariantTextInp.value = ""; invariantTextInp.focus(); }
        });
    }
    if (invariantClearBtn) {
        invariantClearBtn.addEventListener("click", function () {
            if (!invState.invariants || !invState.invariants.length) return;
            if (!window.confirm("Удалить все инварианты?")) return;
            invariantAction({ action: "clear" });
        });
    }


    // ------------------------------------------------------------------
    // Статистика токенов в заголовке (текущая сессия)
    // ------------------------------------------------------------------
    // Суммарный расход токенов за сессию: tokIn — вход (запрос), tokOut — выход (ответ)
    var tokIn = 0;
    var tokOut = 0;
    // Оценка входных токенов, ушедших на контекст-историю диалога
    var tokHist = 0;

    // Форматирует число с разделителями тысяч.

    function resetTokenStats() {
        tokIn = 0;
        tokOut = 0;
        tokHist = 0;
        renderTokenStats();
    }

    // Учитывает трафик очередного обмена (input/output токенов за запрос).
    function addTokenUsage(u) {
        if (!u) return;
        var i = Math.max(0, parseInt(u.input, 10) || 0);
        var o = Math.max(0, parseInt(u.output, 10) || 0);
        var h = Math.max(0, parseInt(u.history, 10) || 0);
        tokIn += i;
        tokOut += o;
        tokHist += h;
        renderTokenStats();
    }

    // Обновляет цифры и полоску-диаграмму «запрос/ответ» + долю истории.
    function renderTokenStats() {
        var total = tokIn + tokOut;
        if (tsTotal) tsTotal.textContent = fmt(total);
        if (tsIn) tsIn.textContent = fmt(tokIn);
        if (tsOut) tsOut.textContent = fmt(tokOut);
        if (tsHist) tsHist.textContent = fmt(tokHist);
        if (tsHistPct) {
            var pct = total > 0 ? (tokHist / total * 100) : 0;
            tsHistPct.textContent = "(" + pct.toFixed(1) + "%)";
        }
        var empty = document.getElementById("ts-empty");
        if (empty) empty.style.display = total ? "none" : "";
        if (segIn && segOut) {
            if (total) {
                segIn.style.width = (tokIn / total * 100).toFixed(2) + "%";
                segOut.style.width = (tokOut / total * 100).toFixed(2) + "%";
            } else {
                segIn.style.width = "50%";
                segOut.style.width = "50%";
            }
        }
    }

    // Счётчики использования памяти в заголовке: сколько РАЗ данные были
    // заимствованы из РАБОЧЕЙ (фисташковая) и ДОЛГОВРЕМЕННОЙ (фуксия) памяти
    // за сессию (накопительно). «Раз» = за сколько ответов память пригодилась.
    function applyContextStats(ctx) {
        if (!ctx) return;
        if (tsCompacted) tsCompacted.textContent = fmt(parseInt(ctx.memory_use_count_working, 10) || 0);
        if (tsFromSummary) tsFromSummary.textContent = fmt(parseInt(ctx.memory_use_count_longterm, 10) || 0);
    }

    function resetContextStats() {
        if (tsCompacted) tsCompacted.textContent = "0";
        if (tsFromSummary) tsFromSummary.textContent = "0";
    }

    // ------------------------------------------------------------------
    // Накопительная статистика по каждой модели (в столбик в заголовке)
    // ------------------------------------------------------------------
    // modelStats: { label: {input, output, cost, hasCost} }
    var modelStats = {};
    // Порядок отображения моделей (метки из /api/model).
    var availableLabels = [];
    // Метки моделей для выпадающего списка персоны (по умолчанию — те же,
    // что и доступные модели). Объявлено здесь, т.к. используется в
    // fillProfileModelSelect/renderProfiles и обработчиках профилей.
    var availableModelLabels = [];

    function ensureModel(label) {
        if (!label) return null;
        if (!modelStats[label]) {
            modelStats[label] = { input: 0, output: 0, cost: 0, hasCost: false, tps: 0, local: false };
        }
        return modelStats[label];
    }

    // Учитывает расход по моделям за один обмен (массив answers).
    function addAnswerUsage(answers) {
        if (!answers || !answers.length) return;
        answers.forEach(function (a) {
            if (!a) return;
            var st = ensureModel(a.label);
            if (!st) return;
            st.input += Math.max(0, parseInt(a.input, 10) || 0);
            st.output += Math.max(0, parseInt(a.output, 10) || 0);
            if (a.cost != null && !isNaN(parseFloat(a.cost))) {
                st.cost += parseFloat(a.cost);
                st.hasCost = true;
            }
            // Скорость генерации (токенов/с) и признак локальной модели:
            // для локального источника показываем производительность.
            if (a.tps != null && !isNaN(parseFloat(a.tps))) st.tps = parseFloat(a.tps);
            if (a.local) st.local = true;
        });
        renderModelStats();
    }

    function resetModelStats() {
        Object.keys(modelStats).forEach(function (k) {
            modelStats[k] = { input: 0, output: 0, cost: 0, hasCost: false, tps: 0, local: false };
        });
        renderModelStats();
    }

    // Собирает упорядоченный список меток: сначала доступные, затем прочие.
    function orderedLabels() {
        var seen = {}, out = [];
        availableLabels.forEach(function (l) { if (!seen[l]) { seen[l] = 1; out.push(l); } });
        Object.keys(modelStats).forEach(function (l) { if (!seen[l]) { seen[l] = 1; out.push(l); } });
        return out;
    }

    function renderModelStats() {
        if (!tsModels) return;
        var labels = orderedLabels();
        tsModels.innerHTML = "";
        var any = false;
        labels.forEach(function (label) {
            var st = modelStats[label] || { input: 0, output: 0, cost: 0, hasCost: false, tps: 0, local: false };
            var tot = st.input + st.output;
            if (tot > 0) any = true;
            var li = document.createElement("li");
            li.className = "tstat-model";
            var cost = "";
            if (st.hasCost) cost = " · ¥" + st.cost.toFixed(6);
            // Скорость генерации для локальной модели (токенов/с).
            var speed = "";
            if (st.local && st.tps > 0) {
                speed = ' <span class="tm-speed" title="скорость генерации">'
                    + st.tps.toFixed(1) + ' ток/с</span>';
            }
            li.innerHTML = '<span class="tm-name">' + esc(label) + '</span>' +
                '<span class="tm-val">' + fmt(st.input) + ' / ' + fmt(st.output) + cost + speed + '</span>';
            tsModels.appendChild(li);
        });
        // Если ни одна модель ещё не отвечала — подсказка
        if (!labels.length || !any) {
            tsModels.innerHTML = '<li class="tstat-model empty">нет данных — задайте вопрос</li>';
        }
    }

    // ------------------------------------------------------------------
    // Память агента: три типа (short / working / longterm)
    // ------------------------------------------------------------------
    // 1) краткосрочная — текущий диалог (автоматическая, только чтение);
    // 2) рабочая      — данные текущей задачи (редактируется вручную);
    // 3) долговременная — профиль/решения/знания (редактируется вручную).
    // Запись идёт ЧЕРЕЗ /api/memory с ЯВНЫМ указанием типа (задание B2).
    var memShortInfo = document.getElementById("mem-short-info");
    var memWorkingBox = document.getElementById("mem-working-box");
    var memLongtermBox = document.getElementById("mem-longterm-box");

    // «Грязные» слои памяти: пользователь изменил строки, но ещё не сохранил.
    // Фоновые обновления (после ответа/загрузки сессии) НЕ должны затирать
    // такие правки — иначе введённое в одном слое терялось бы при
    // сохранении/обновлении другого слоя. Ключ — тип слоя, значение — bool.
    var memDirty = { working: false, longterm: false };

    // Помечает слой «грязным» при любом ручном вводе.
    function markMemDirty(memType) {
        if (memType in memDirty) memDirty[memType] = true;
    }

    // Рисует строки key=value для редактируемого слоя памяти.
    // memType нужен, чтобы отмечать слой «грязным» при ручном редактировании.
    function memRow(key, valValDiv, memType) {
        var row = document.createElement("div");
        row.className = "mem-row";
        var k = document.createElement("input");
        k.type = "text"; k.className = "mem-key"; k.value = key || "";
        k.placeholder = "ключ";
        var v = document.createElement("input");
        v.type = "text"; v.className = "mem-val"; v.value = valValDiv || "";
        v.placeholder = "значение";
        // Любой ручной ввод в поле/удаление строки — слоя касались, значит
        // его нельзя затирать фоновым обновлением из снимка сервера.
        k.addEventListener("input", function () { markMemDirty(memType); });
        v.addEventListener("input", function () { markMemDirty(memType); });
        var del = document.createElement("button");
        del.type = "button"; del.className = "mem-del"; del.textContent = "\u00d7";
        del.title = "Удалить строку";
        del.addEventListener("click", function () {
            markMemDirty(memType);
            row.remove();
        });
        row.appendChild(k); row.appendChild(v); row.appendChild(del);
        return row;
    }

    // Отрисовка редактируемого слоя памяти из словаря.
    function renderMemRows(box, data, memType) {
        if (!box) return;
        box.innerHTML = "";
        var keys = data ? Object.keys(data) : [];
        if (!keys.length) {
            var empty = document.createElement("div");
            empty.className = "mem-empty";
            empty.textContent = "пусто — добавьте строку";
            box.appendChild(empty);
            return;
        }
        keys.forEach(function (k) { box.appendChild(memRow(k, data[k], memType)); });
    }

    // Собирает словарь из строк редактируемого слоя.
    function collectMemRows(box) {
        var out = {};
        if (!box) return out;
        var rows = box.querySelectorAll(".mem-row");
        for (var i = 0; i < rows.length; i++) {
            var k = rows[i].querySelector(".mem-key").value.trim();
            var v = rows[i].querySelector(".mem-val").value.trim();
            if (k) out[k] = v;
        }
        return out;
    }

    // Отрисовка слоёв памяти из снимка сервера.
    //
    // onlyType — если задан ("working"/"longterm"), перерисовываем ТОЛЬКО
    // этот слой (например, после его сохранения), не трогая другой: тогда
    // несохранённые правки в другом слое сохраняются.
    // force — если true, перерисовать и «грязные» слои (используется только
    // при явном сохранении/очистке и принудительной синхронизации).
    // По умолчанию «грязные» слои НЕ перерисовываются фоновыми обновлениями.
    function renderMemory(mem, onlyType, force) {
        if (!mem) return;
        // 1) краткосрочная — только информация о диалоге.
        if (memShortInfo) {
            var s = mem.short || {};
            memShortInfo.textContent = "сообщений в диалоге: " + (s.items || 0) +
                " · веток: " + (s.branches || 1);
        }
        // Связываем факты с их слоем памяти (для select в панели «Факты»).
        // Обновляем карту только по тем слоям, которые сейчас синхронизируем.
        if (!onlyType) factsMemory = {};
        if (!onlyType) {
            Object.keys(mem.working || {}).forEach(function (k) {
                factsMemory[k] = "working";
            });
            Object.keys(mem.longterm || {}).forEach(function (k) {
                factsMemory[k] = "longterm";
            });
        }
        // 2) рабочая и 3) долговременная — редактируемые.
        var wantWorking = (!onlyType || onlyType === "working");
        var wantLongterm = (!onlyType || onlyType === "longterm");
        if (wantWorking && (force || !memDirty.working)) {
            renderMemRows(memWorkingBox, mem.working || {}, "working");
            memDirty.working = false;
        }
        if (wantLongterm && (force || !memDirty.longterm)) {
            renderMemRows(memLongtermBox, mem.longterm || {}, "longterm");
            memDirty.longterm = false;
        }
        refreshSections();
    }

    // ЯВНО сохраняет слой памяти на сервер (action=replace, type=<слой>).
    function saveMemoryLayer(memType, box) {
        var payload = {
            action: "replace",
            type: memType,
            data: collectMemRows(box),
        };
        return fetch("/api/memory", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (d && d.ok) {
                // Перерисовываем ТОЛЬКО сохранённый слой: несохранённые
                // правки в другом слое остаются нетронутыми.
                renderMemory(d.memory, memType, true);
                setStatus("Память «" + memType + "» сохранена.", "ok");
            } else {
                setStatus("Не удалось сохранить память.", "error");
            }
        })
        .catch(function () { setStatus("Ошибка связи при сохранении памяти.", "error"); });
    }

    // ЯВНО очищает слой памяти на сервере.
    function clearMemoryLayer(memType, box) {
        return fetch("/api/memory", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "clear", type: memType }),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (d && d.ok) {
                // Очищаем и перерисовываем только этот слой.
                renderMemory(d.memory, memType, true);
                setStatus("Память «" + memType + "» очищена.", "ok");
            }
        })
        .catch(function () {});
    }

    function bindMemoryLayer(memType, box, addBtn, saveBtn, clearBtn) {
        if (addBtn) addBtn.addEventListener("click", function () {
            var empty = box.querySelector(".mem-empty");
            if (empty) empty.remove();
            markMemDirty(memType);
            box.appendChild(memRow("", "", memType));
            refreshSections();
        });
        if (saveBtn) saveBtn.addEventListener("click", function () {
            saveMemoryLayer(memType, box);
        });
        if (clearBtn) clearBtn.addEventListener("click", function () {
            clearMemoryLayer(memType, box);
        });
    }

    bindMemoryLayer("working",
        document.getElementById("mem-working-box"),
        document.getElementById("mem-working-add"),
        document.getElementById("mem-working-save"),
        document.getElementById("mem-working-clear"));
    bindMemoryLayer("longterm",
        document.getElementById("mem-longterm-box"),
        document.getElementById("mem-longterm-add"),
        document.getElementById("mem-longterm-save"),
        document.getElementById("mem-longterm-clear"));

    // ------------------------------------------------------------------
    // ПРОФИЛИ (персоны)
    // ------------------------------------------------------------------
    // Профиль — именованный набор состояния: своя память, свой диалог,
    // свои настройки и своя МАНЕРА ОБЩЕНИЯ. У профиля два атрибута:
    //   character — ХАРАКТЕР (тон общения);
    //   style     — ХАРАКТЕР ОТВЕТОВ (формат и длина).
    // Оба подставляются в системный промпт каждой модели. Переключение
    // профиля заменяет активную память и диалог (сервер возвращает их снимок).
    var profilesBox = document.getElementById("profiles-box");
    var profileNameInp = document.getElementById("profile-name");
    var profileModelSel = document.getElementById("profile-model");
    var profileCharInp = document.getElementById("profile-character");
    var profileStyleInp = document.getElementById("profile-style");
    var profileForm = document.getElementById("profiles-form");
    var profileNewBtn = document.getElementById("profile-new");
    var profileSaveBtn = document.getElementById("profile-save");
    var profileCancelBtn = document.getElementById("profile-cancel");
    // Текущий список профилей и id активного (снимок сервера).
    var profilesState = { profiles: [], active: null };

    // Раскрывает форму создания профиля (поля + кнопки) и скрывает кнопку
    // «Создать профиль». Соответствует логике: старт — только кнопка; после
    // нажатия появляются поля.
    function openProfileForm() {
        if (profileForm) profileForm.hidden = false;
        if (profileNewBtn) profileNewBtn.hidden = true;
        if (profileNameInp) profileNameInp.focus();
    }

    // Сворачивает форму обратно к кнопке «Создать профиль» и очищает поля.
    function closeProfileForm() {
        if (profileForm) profileForm.hidden = true;
        if (profileNewBtn) profileNewBtn.hidden = false;
        if (profileNameInp) profileNameInp.value = "";
        if (profileCharInp) profileCharInp.value = "";
        if (profileStyleInp) profileStyleInp.value = "";
        if (profileModelSel) profileModelSel.value = "";
    }

    // Возвращает первую букву имени для аватарки.
    function profileInitial(name) {
        var s = String(name || "").trim();
        return s ? s.charAt(0).toUpperCase() : "?";
    }

    // Заполняет выпадающий список моделей в форме создания персоны.
    function fillProfileModelSelect(labels) {
        if (!profileModelSel) return;
        if (Array.isArray(labels) && labels.length) availableModelLabels = labels.slice();
        profileModelSel.innerHTML = "";
        // Пустой вариант — «модель по выбору сверху» (если у персоны не задана).
        var none = document.createElement("option");
        none.value = "";
        none.textContent = "модель: по выбору сверху";
        profileModelSel.appendChild(none);
        availableModelLabels.forEach(function (l) {
            var o = document.createElement("option");
            o.value = l;
            o.textContent = l;
            profileModelSel.appendChild(o);
        });
    }
    // Рисует строки профилей: имя, характер/стиль и кнопки действий.
    function renderProfiles(state) {
        if (!profilesBox) return;
        // Сервер возвращает профили в ДВУХ формах:
        //   * объект {profiles:[...], active:"id"}  — из GET /api/session;
        //   * массив [...] + отдельный ключ active  — из POST /api/profiles.
        // Нормализуем обе формы к виду {profiles:[...], active:"id"}.
        if (Array.isArray(state)) {
            // Плоский массив профилей. Активный определим по полю p.active.
            var act = null;
            state.forEach(function (p) { if (p && p.active) act = p.id; });
            profilesState = { profiles: state, active: act };
        } else if (state && Array.isArray(state.profiles)) {
            profilesState = {
                profiles: state.profiles,
                active: (state.active !== undefined ? state.active : null)
            };
        }
        var list = profilesState.profiles || [];
        profilesBox.innerHTML = "";
        if (!list.length) {
            var empty = document.createElement("div");
            empty.className = "profiles-empty";
            empty.textContent = "Профилей пока нет. Нажмите «Создать профиль».";
            profilesBox.appendChild(empty);
            updateModelBarMode();
            refreshSections();
            return;
        }
        list.forEach(function (p) {
            var row = document.createElement("div");
            row.className = "profile-row" + (p.active ? " active" : "");
            row.title = "Клик — переключиться на этот профиль";

            // Аватарка (первая буква имени) — всегда видна в карточке профиля.
            var avatar = document.createElement("span");
            avatar.className = "p-avatar";
            avatar.textContent = profileInitial(p.name);
            row.appendChild(avatar);

            var name = document.createElement("span");
            name.className = "p-name";
            name.textContent = p.name || "(без имени)";
            row.appendChild(name);

            var meta = document.createElement("span");
            meta.className = "p-meta";
            var bits = [];
            if (p.model) bits.push("модель: " + p.model);
            if (p.character) bits.push(p.character);
            if (p.style) bits.push(p.style);
            meta.textContent = bits.join(" · ") || "без характера";
            row.appendChild(meta);

            var size = document.createElement("span");
            size.className = "p-size";
            size.textContent = (p.size || 0) + " сообщ.";
            row.appendChild(size);

            // Кнопка редактирования (карандаш) — просим новые атрибуты.
            var ren = document.createElement("button");
            ren.type = "button"; ren.className = "p-ren"; ren.textContent = "\u270e";
            ren.title = "Изменить имя/характер/стиль";
            ren.addEventListener("click", function (e) {
                e.stopPropagation();
                var newName = window.prompt("Имя профиля:", p.name || "");
                if (newName === null) return;
                var newModel = window.prompt("Модель персоны (" +
                    availableModelLabels.join(" / ") + "):", p.model || "");
                if (newModel === null) return;
                var newChar = window.prompt("Характер (тон):", p.character || "");
                if (newChar === null) return;
                var newStyle = window.prompt("Характер ответов (формат/длина):",
                                             p.style || "");
                if (newStyle === null) return;
                profileAction({ action: "update", id: p.id, name: newName,
                                model: newModel, character: newChar, style: newStyle });
            });
            row.appendChild(ren);

            // Удаление доступно всегда — можно удалить и последний профиль
            // (тогда профилей снова не будет, как по умолчанию).
            var del = document.createElement("button");
            del.type = "button"; del.className = "p-del"; del.textContent = "\u00d7";
            del.title = "Удалить профиль";
            del.addEventListener("click", function (e) {
                e.stopPropagation();
                if (!window.confirm("Удалить профиль «" + (p.name || "") +
                                    "» вместе с его памятью и диалогом?")) return;
                profileAction({ action: "delete", id: p.id });
            });
            row.appendChild(del);

            row.addEventListener("click", function () {
                if (p.active) return;
                profileAction({ action: "switch", id: p.id });
            });
            profilesBox.appendChild(row);
        });
        updateModelBarMode();
        refreshSections();
    }

    // Применяет снимок, пришедший от сервера после действий с профилями:
    // заменяет диалог, память и настройки на состояние активного профиля.
    function applyProfileSnapshot(d) {
        if (!d) return;
        if (d.profiles) renderProfiles(d.profiles);
        if (Array.isArray(d.messages)) {
            items = d.messages.slice();
            render();
        }
        if (d.memory) renderMemory(d.memory, null, true);
        if (d.branches) renderBranches(d.branches);
        if (d.task) renderTask(d.task);
        if (d.compact) applyCompactFromServer(d.compact);
        if (d.strategy) applyStrategyFromServer(d.strategy);
        if (d.context) applyContextStats(d.context);
    }

    function profileAction(payload) {
        fetch("/api/profiles", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (!d || !d.ok) {
                setStatus((d && d.error) || "Не удалось выполнить действие.",
                          "error");
                return;
            }
            applyProfileSnapshot(d);
            var act = payload.action;
            if (act === "create") setStatus("Профиль создан.", "ok");
            else if (act === "switch") setStatus("Профиль переключён.", "ok");
            else if (act === "update") setStatus("Профиль обновлён.", "ok");
            else if (act === "delete") setStatus("Профиль удалён.", "ok");
        })
        .catch(function () { setStatus("Ошибка связи с профилями.", "error"); });
    }

    // Логика формы создания профиля:
    //   «Создать профиль» -> раскрыть поля -> «Сохранить профиль» -> создать
    //   профиль (карточка с аватаркой) и свернуть форму обратно к кнопке.
    if (profileNewBtn) {
        profileNewBtn.addEventListener("click", openProfileForm);
    }
    if (profileCancelBtn) {
        profileCancelBtn.addEventListener("click", closeProfileForm);
    }
    if (profileSaveBtn) {
        profileSaveBtn.addEventListener("click", function () {
            var name = profileNameInp ? profileNameInp.value.trim() : "";
            if (!name) { setStatus("Введите имя профиля.", "error"); return; }
            profileAction({
                action: "create",
                name: name,
                model: profileModelSel ? profileModelSel.value : "",
                character: profileCharInp ? profileCharInp.value.trim() : "",
                style: profileStyleInp ? profileStyleInp.value.trim() : "",
            });
            // После сохранения форму сворачиваем — остаётся только карточка.
            closeProfileForm();
        });
    }

    // Вкл/выкл панели выбора моделей сверху.
    // Если есть активная персона — выбор моделей недоступен
    // (отвечает персона своей моделью); если персон нет — режим
    // «напрямую с моделями», выбор доступен.
    function updateModelBarMode() {
        if (!modelBox) return;
        var locked = hasActiveProfile();
        modelBox.classList.toggle("locked", locked);
        var hint = document.querySelector(".model-bar-hint");
        if (hint) {
            hint.textContent = locked
                ? "Отвечает персона — её модель задана в персоне (выбор моделей недоступен)."
                : "Выберите модели для запроса (можно несколько) и температуру каждой:";
        }
    }

    // ------------------------------------------------------------------
    // Отправка запроса
    // ------------------------------------------------------------------
    // Активен ли хотя бы один профиль (без профиля отвечать нельзя).
    function hasActiveProfile() {
        return !!(profilesState && profilesState.active);
    }

    // Включена ли хотя бы одна модель.
    function hasSelectedModel() {
        return modelState.some(function (m) { return m.on; });
    }

    function ask() {
        var question = qEl.value.trim();
        if (!question || busy) { if (!question) setStatus("Введите запрос.", "error"); return; }

        // РЕЖИМЫ:
        //   * есть активная персона — отвечает она СВОЕЙ моделью,
        //     выбор моделей сверху недоступен;
        //   * персон нет — диалог идёт напрямую с выбранными моделями.
        // ОСОБЫЙ СЛУЧАЙ — MCP: запрос идёт через MCP (модель берётся из
        // селекта «Модель MCP», ответ — таблицей «Доски») ТОЛЬКО если:
        //   * ВКЛЮЧЁН чекбокс «Использовать MCP», ЛИБО
        //   * выбран сервер «Доска» (запрос к ней не зависит от чекбокса), ЛИБО
        //   * АКТИВНА КОМПОЗИЦИЯ (фоном работают календарь→замполит→завхоз, а
        //     обращение в чате ВСЕГДА идёт к «Доске»).
        // В этих случаях выбранная сверху модель/персона НЕ требуются.
        // ВАЖНО: сам ВЫБОР сервера в списке (без чекбокса и без композиции)
        // MCP-режим НЕ включает.
        var mcpReady = !!(mcpState && mcpState.enabled &&
            (mcpState.model || mcpState.available));
        // КОМАНДЫ /help, /review и /files работают без обязательной модели:
        // сервер сам вернёт справку, понятную ошибку или результат поиска.
        // Для /files docs|new сервер отдельно проверит наличие модели.
        var isHelp = /^\s*\/help\b/i.test(question);
        var isReview = /^\s*\/review\b/i.test(question);
        var isFiles = /^\s*\/files\b/i.test(question);
        if (!isHelp && !isReview && !isFiles &&
                !hasActiveProfile() && !hasSelectedModel() && !mcpReady) {
            setStatus("Выберите модель или создайте персону.", "error");
            return;
        }

        busy = true;
        submit.disabled = true;
        setStatus("Обрабатываю…", "");
        qEl.value = "";

        // Сервер сам держит историю диалога; передаём вопрос, выбор моделей
        // и настройки сжатия/стратегии.
        var body = {
            question: question,
            models: selectedModelsPayload(),
            compact: getCompactPayload(),
            strategy: getStrategyPayload(),
        };

        fetch("/api/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        })
        .then(function (resp) {
            return resp.json().catch(function () {
                return { ok: false, error: "Сервер вернул некорректный ответ." };
            });
        })
        .then(function (data) {
            setStatus("", "");
            // Доставленные напоминания показываем ПЕРВЫМИ (красным фоном),
            // даже если ответ модели не удался — напоминание важнее.
            if (Array.isArray(data.reminders) && data.reminders.length) {
                data.reminders.forEach(function (txt) {
                    items.push({ role: "reminder", content: txt });
                });
            }
            if (!data.ok) {
                // /review, /support и /files: ошибка приходит с готовым
                // текстом — показываем её карточкой в чате, а не только в
                // строке статуса.
                if ((data.review || data.support || data.files_task)
                        && data.text) {
                    items.push({ role: "user", content: question });
                    items.push({ role: "assistant", content: data.text,
                                 html: data.html || "",
                                 answers: data.answers || [] });
                    render();
                    setStatus(data.review
                        ? "Ревью не выполнено — причина в сообщении."
                        : (data.files_task
                            ? "Файлы: операция не выполнена — причина в сообщении."
                            : "Поддержка: причина в сообщении."), "error");
                    return;
                }
                render();
                setStatus(data.error || "Произошла ошибка.", "error");
                return;
            }
            items.push({ role: "user", content: question });
            items.push({ role: "assistant", content: data.text || "",
                         html: data.html || "", answers: data.answers || [] });
            render();
            addTokenUsage(data.usage);
            addAnswerUsage(data.answers);
            applyContextStats(data.context);
            if (data.memory) renderMemory(data.memory);
            if (data.facts) renderFacts(data.facts);
            if (data.branches) renderBranches(data.branches);
            if (data.task) renderTask(data.task);
            if (data.invariants) renderInvariants(data.invariants);
            renderTrace(data.trace, data.meta);
        })
        .catch(function (err) { setStatus("Ошибка связи: " + err.message, "error"); })
        .finally(function () {
            busy = false;
            submit.disabled = false;
        });
    }

    // Сброс (очистка) диалога — на сервере и локально.
    function startNewChat() {
        fetch("/api/newchat", { method: "POST" })
            .then(function () { clearLocalChat("Начат новый разговор."); })
            .catch(function () {
                // даже если сеть не ответила, очистим окно
                clearLocalChat("Новый разговор (сессия очищена локально).");
            });
    }

    function clearLocalChat(statusText) {
        items = [];
        resetTokenStats();
        resetModelStats();
        resetContextStats();
        render();
        renderTrace(null, null);
        // Новый разговор сбрасывает и состояние задачи (см. /api/newchat).
        renderTask({ active: false, stage: "planning", paused: false,
                     stages: ["planning", "execution", "validation", "done"],
                     stage_labels: taskState.stage_labels || {} });
        setStatus(statusText, "ok");
        qEl.focus();
    }

    // Загрузка сохранённой истории с сервера (непрерывность беседы).
    function loadSession() {
        return fetch("/api/session")
            .then(function (r) { return r.ok ? r.json() : { ok: false }; })
            .then(function (d) {
                if (d && d.ok !== false && Array.isArray(d.messages)) {
                    items = d.messages.slice();
                    // Применяем настройки сжатия и стратегии с сервера
                    applyCompactFromServer(d.compact);
                    applyStrategyFromServer(d.strategy);
                    if (d.profiles) renderProfiles(d.profiles);
                    renderMemory(d.memory);
                    renderFacts(d.facts);
                    renderBranches(d.branches);
                    if (d.task) renderTask(d.task);
                    if (d.invariants) renderInvariants(d.invariants);
                    // накапливаем статистику токенов из сохранённой истории
                    tokIn = 0;
                    tokOut = 0;
                    tokHist = 0;
                    modelStats = {};
                    items.forEach(function (m) {
                        if (m && m.role === "assistant") {
                            if (m.usage) {
                                tokIn += Math.max(0, parseInt(m.usage.input, 10) || 0);
                                tokOut += Math.max(0, parseInt(m.usage.output, 10) || 0);
                                tokHist += Math.max(0, parseInt(m.usage.history, 10) || 0);
                            }
                            if (Array.isArray(m.answers)) {
                                m.answers.forEach(function (a) {
                                    if (!a || !a.label) return;
                                    var st = ensureModel(a.label);
                                    st.input += Math.max(0, parseInt(a.input, 10) || 0);
                                    st.output += Math.max(0, parseInt(a.output, 10) || 0);
                                    if (a.cost != null && !isNaN(parseFloat(a.cost))) {
                                        st.cost += parseFloat(a.cost);
                                        st.hasCost = true;
                                    }
                                });
                            }
                        }
                    });
                    renderTokenStats();
                    renderModelStats();
                    applyContextStats(d.context);
                    render();
                    if (d.has_history) {
                        setStatus("Загружена сохранённая сессия.", "ok");
                    }
                }
            })
            .catch(function () {});
    }

    // ------------------------------------------------------------------
    // Инициализация
    // ------------------------------------------------------------------
    submit.addEventListener("click", ask);
    qEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            ask();
        }
    });

    var newchatBtn = document.getElementById("newchat");
    if (newchatBtn) newchatBtn.addEventListener("click", startNewChat);

    // Загружает список доступных моделей и строит панель выбора.
    // Вызывается при старте страницы и при изменении состояния локальной
    // модели (запуск/остановка), чтобы «Local» появлялся/исчезал сразу.
    function refreshModels() {
        return fetch("/api/model")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                if (d.available && d.available.length) {
                    availableLabels = d.available.map(function (m) { return m.label; });
                    buildModelControls(d.available);
                } else if (d.models && d.models.length) {
                    availableLabels = d.models.slice();
                    buildModelControls(d.models.map(function (l) {
                        return { label: l, cls: "" };
                    }));
                }
                fillProfileModelSelect(availableLabels);
                fillMcpModelSelect(availableLabels);
                renderModelStats();
            })
            .catch(function () {});
    }
    refreshModels();


    // ------------------------------------------------------------------
    // MCP (Model Context Protocol)
    // ------------------------------------------------------------------
    // В левой колонке: чекбокс включения MCP, кнопка проверки СТАТУСА
    // сервера и селект МОДЕЛИ, применяемой при работе с MCP.
    // Состояние (вкл/выкл + модель) хранится на сервере (GET/POST /api/mcp).
        var mcpEnabledEl = document.getElementById("mcp-enabled");
    var mcpServerSel = document.getElementById("mcp-server");
    var mcpModelSel = document.getElementById("mcp-model");
    var mcpCheckBtn = document.getElementById("mcp-check");
    var mcpStatusEl = document.getElementById("mcp-status");
    var mcpToolsEl = document.getElementById("mcp-tools");
    // КОМПОЗИЦИЯ (task5): кнопка запуска пайплайна и поле отчёта.
    var mcpComposeBtn = document.getElementById("mcp-compose");
    var mcpComposeStatus = document.getElementById("mcp-compose-status");

    // Локальный снимок настроек MCP.
    var mcpState = { enabled: false, server: "", servers: [],
                     model: "", status: null, available: false,
                     composition: false };

    // id сервера «Доска»: запрос к нему обрабатывается MCP-путём
    // независимо от чекбокса «Использовать MCP» (см. docs/task5.md).


    // Заполняет селект доступных MCP-серверов (демо / календарь).
    function fillMcpServerSelect(servers) {
        if (!mcpServerSel) return;
        if (Array.isArray(servers) && servers.length) mcpState.servers = servers;
        var list = mcpState.servers || [];
        var current = mcpState.server || (mcpServerSel.value || "");
        mcpServerSel.innerHTML = "";
        list.forEach(function (s) {
            var o = document.createElement("option");
            o.value = s.id;
            o.textContent = s.label || s.id;
            mcpServerSel.appendChild(o);
        });
        if (current && list.some(function (s) { return s.id === current; })) {
            mcpServerSel.value = current;
        } else if (list.length) {
            mcpServerSel.value = list[0].id;
        }
        mcpState.server = mcpServerSel.value || "";
    }

    // Заполняет селект моделей MCP (те же метки, что и доступные модели).
    function fillMcpModelSelect(labels) {
        if (!mcpModelSel) return;
        var current = mcpState.model || (mcpModelSel.value || "");
        mcpModelSel.innerHTML = "";
        var none = document.createElement("option");
        none.value = "";
        none.textContent = "модель: первая доступная";
        mcpModelSel.appendChild(none);
        (labels || []).forEach(function (l) {
            var o = document.createElement("option");
            o.value = l;
            o.textContent = l;
            mcpModelSel.appendChild(o);
        });
        // Восстанавливаем сохранённый выбор, если он есть в списке.
        mcpModelSel.value = (current && (labels || []).indexOf(current) >= 0)
            ? current : "";
        mcpState.model = mcpModelSel.value || "";
    }

    // Показывает статус MCP-сервера (текст + список инструментов).
    function renderMcpStatus(status) {
        mcpState.status = status || null;
        if (mcpStatusEl) {
            if (!status) {
                mcpStatusEl.textContent = "";
                mcpStatusEl.className = "mcp-status";
            } else if (status.ok) {
                mcpStatusEl.textContent = "подключён · инструментов: " +
                    (status.tools_count || 0);
                mcpStatusEl.className = "mcp-status ok";
                mcpStatusEl.title = status.server || "";
            } else {
                mcpStatusEl.textContent = "недоступен";
                mcpStatusEl.className = "mcp-status error";
                mcpStatusEl.title = status.error || "";
            }
        }
        if (mcpToolsEl) {
            if (status && status.ok && status.tools && status.tools.length) {
                mcpToolsEl.hidden = false;
                mcpToolsEl.textContent = "инструменты: " + status.tools.join(", ");
            } else {
                mcpToolsEl.hidden = true;
                mcpToolsEl.textContent = "";
            }
        }
    }

    // Применяет состояние MCP, пришедшее от сервера.
        function applyMcpState(d) {
        if (!d) return;
        mcpState.enabled = !!d.enabled;
        mcpState.available = !!d.available;
        // РЕЖИМ КОМПОЗИЦИИ (взаимоисключающий с «Использовать MCP»).
        // Ключ composition_running — булев; "composition" в ответе на запуск
        // композиции содержит ОТЧЁТ прогона (его обрабатывает кнопка).
        if (typeof d.composition_running === "boolean") {
            mcpState.composition = d.composition_running;
        }
        if (Array.isArray(d.servers) && d.servers.length) {
            mcpState.servers = d.servers;
        }
        if (typeof d.server === "string") mcpState.server = d.server;
        if (typeof d.model === "string") mcpState.model = d.model;
        if (mcpEnabledEl) mcpEnabledEl.checked = mcpState.enabled;
        if (mcpState.servers && mcpState.servers.length) {
            fillMcpServerSelect(mcpState.servers);
        }
        if (mcpModelSel && mcpModelSel.options.length) {
            mcpModelSel.value = mcpState.model || "";
        }
        if ("status" in d) renderMcpStatus(d.status);
        if (d.available === false && !d.status && mcpStatusEl) {
            mcpStatusEl.textContent = "MCP недоступен";
            mcpStatusEl.className = "mcp-status error";
        }
        syncComposeModeUI();
        refreshSections();
    }

    // Отражает режим композиции в интерфейсе: при активной композиции
    // чекбокс «Использовать MCP» выключен, а выбранный сервер НЕ применяется
    // к обычным запросам (композиция работает сама по себе).
    function syncComposeModeUI() {
        var on = !!mcpState.composition;
        if (mcpComposeBtn) {
            mcpComposeBtn.classList.toggle("active", on);
            mcpComposeBtn.title = on
                ? "Композиция запущена (работает сама по себе). "
                  + "Включение «Использовать MCP» остановит её."
                : "Запустить композицию (взаимоисключающе с «Использовать MCP»)";
        }
        if (on && mcpComposeStatus && mcpComposeStatus.hidden) {
            // Показываем, что режим композиции активен.
            mcpComposeStatus.hidden = false;
            mcpComposeStatus.className = "mcp-compose-status ok";
            mcpComposeStatus.textContent = "композиция активна (MCP выключен)";
        }
        if (on && mcpStatusEl && (!mcpState.status || !mcpState.status.ok)) {
            mcpStatusEl.textContent = "режим композиции";
            mcpStatusEl.className = "mcp-status ok";
        }
    }

    // Отправляет действие MCP на сервер.
    function mcpAction(action, extra) {
        var payload = { action: action };
        if (extra) {
            Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; });
        }
        return fetch("/api/mcp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) applyMcpState(d); return d; })
        .catch(function () { return null; });
    }

    // Загружает состояние MCP при старте страницы.
    function initMcp() {
        fetch("/api/mcp")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) applyMcpState(d); })
            .catch(function () {});
    }

    if (mcpEnabledEl) {
        mcpEnabledEl.addEventListener("change", function () {
            var on = mcpEnabledEl.checked;
            if (on) {
                // ВКЛЮЧЕНИЕ MCP ОСТАНАВЛИВАЕТ композицию (взаимоисключающе):
                // переходим в режим отдельных серверов по выбору.
                setStatus(mcpState.composition
                    ? "MCP включён. Композиция остановлена — работают отдельные серверы по выбору."
                    : "MCP включён.", "ok");
            } else {
                setStatus("MCP выключен.", "ok");
            }
            // Сервер сам выключит режим композиции при enabled=true.
            mcpAction("set", { enabled: on });
        });
    }
        if (mcpServerSel) {
        mcpServerSel.addEventListener("change", function () {
            mcpState.server = mcpServerSel.value || "";
            // При смене сервера прошлый статус неактуален — сбрасываем.
            renderMcpStatus(null);
            setStatus("MCP-сервер: " + (mcpServerSel.options[mcpServerSel.selectedIndex] ?
                mcpServerSel.options[mcpServerSel.selectedIndex].textContent :
                mcpState.server), "ok");
            mcpAction("set", { server: mcpState.server });
        });
    }
    if (mcpModelSel) {
        mcpModelSel.addEventListener("change", function () {
            mcpState.model = mcpModelSel.value || "";
            mcpAction("set", { model: mcpState.model });
        });
    }
    if (mcpCheckBtn) {
        mcpCheckBtn.addEventListener("click", function () {
            mcpCheckBtn.disabled = true;
            if (mcpStatusEl) {
                mcpStatusEl.textContent = "проверяю…";
                mcpStatusEl.className = "mcp-status";
            }
            mcpAction("status").finally(function () {
                mcpCheckBtn.disabled = false;
            });
        });
    }

    // Показывает отчёт о запуске композиции (календарь → замполит → завхоз).
    // model — метка модели из селекта «Модель MCP», сформировавшей сообщение.
    function renderComposeStatus(rep, model) {
        if (!mcpComposeStatus) return;
        if (!rep) {
            mcpComposeStatus.hidden = true;
            mcpComposeStatus.textContent = "";
            return;
        }
        mcpComposeStatus.hidden = false;
        var byModel = model ? (" · модель: " + model) : "";
        if (rep.ok) {
            mcpComposeStatus.className = "mcp-compose-status ok";
            mcpComposeStatus.textContent =
                "композиция выполнена: " + (rep.detail || "") +
                (rep.saved ? (" · " + rep.saved) : "") + byModel;
        } else {
            mcpComposeStatus.className = "mcp-compose-status error";
            mcpComposeStatus.textContent = "ошибка: " + (rep.error || "?") + byModel;
        }
        mcpComposeStatus.title = JSON.stringify(rep.steps || [], null, 1);
    }

    if (mcpComposeBtn) {
        mcpComposeBtn.addEventListener("click", function () {
            mcpComposeBtn.disabled = true;
            if (mcpComposeStatus) {
                mcpComposeStatus.hidden = false;
                mcpComposeStatus.className = "mcp-compose-status";
                mcpComposeStatus.textContent = "запускаю композицию…";
            }
            mcpAction("compose")
                .then(function (d) {
                    if (d && d.composition) {
                        renderComposeStatus(d.composition,
                                            d.composition_message_model);
                    } else {
                        renderComposeStatus({ ok: false, error: "нет отчёта" });
                    }
                    // Композиция включилась, чекбокс «Использовать MCP» на
                    // сервере выключен — сообщаем пользователю режим работы.
                    if (d && d.composition && d.composition.ok) {
                        setStatus("Композиция запущена (MCP выключен — работает сама по себе).",
                                  "ok");
                    }
                    // ОТДЕЛЬНОЕ СООБЩЕНИЕ в чат: какие MCP-серверы задействованы
                    // и что каждый делает (названия + краткое описание).
                    // Текст генерит модель из селекта «Модель MCP».
                    var msg = d && d.composition_message;
                    if (msg) {
                        pushCompositionMessage(msg);
                    }
                })
                .finally(function () { mcpComposeBtn.disabled = false; });
        });
    }

    // Показывает ОТДЕЛЬНОЕ сообщение композиции в потоке чата (как ответ
    // «ассистента»): список MCP-серверов с кратким описанием их работы.
    // Простейший Markdown -> HTML: **жирный** и строки-пункты «- …».
    function pushCompositionMessage(text) {
        var html = esc(text)
            .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
            .split("\n").map(function (ln) {
                return /^\s*-\s+/.test(ln)
                    ? '<div class="compose-li">' + ln.replace(/^\s*-\s+/, "• ") + "</div>"
                    : (ln ? "<div>" + ln + "</div>" : "<div>&nbsp;</div>");
            }).join("");
        items.push({ role: "assistant", content: text, html: html });
        render();
    }


    // ------------------------------------------------------------------
    // RAG (Retrieval-Augmented Generation) — база знаний из документации,
    // кода и PDF
    // ------------------------------------------------------------------
    // Кнопки/поля блока RAG: чекбокс включения, путь к папке с документами,
    // число фрагментов (top-k), кнопки «Переиндексировать», «Проверить»
    // (доступность модели эмбеддингов Ollama) и «Очистить» индекс.
    // Настройки и статистика хранятся на сервере (GET/POST /api/rag).
    var ragEnabledEl = document.getElementById("rag-enabled");
    var ragDocsDirEl = document.getElementById("rag-docs-dir");
    var ragTopKEl = document.getElementById("rag-top-k");
    var ragRewriteEl = document.getElementById("rag-rewrite");
    var ragHybridEl = document.getElementById("rag-hybrid");
    var ragMinScoreEl = document.getElementById("rag-min-score");
    var ragPoolMultEl = document.getElementById("rag-pool-mult");
    var ragRerankEl = document.getElementById("rag-rerank");
    var ragReindexBtn = document.getElementById("rag-reindex");
    var ragCheckBtn = document.getElementById("rag-check");
    var ragClearBtn = document.getElementById("rag-clear");
    var ragStatusEl = document.getElementById("rag-status");
    var ragEmbedStatusEl = document.getElementById("rag-embed-status");

    // Локальный снимок состояния RAG.
    var ragState = { enabled: false, docs_dir: "", top_k: 5,
                     rewrite: true, hybrid: true, hybrid_alpha: 0.6,
                     pool_mult: 6, min_score: 0, rerank: "none",
                     files: 0, chunks: 0, available: false,
                     embed_model: "", updated: null,
                     embed_tunnel: null };

    // Статус Ollama-эмбеддингов (через туннель с ПК).
    function renderRagEmbedStatus() {
        if (!ragEmbedStatusEl) return;
        var t = ragState.embed_tunnel || {};
        if (t.alive) {
            ragEmbedStatusEl.textContent = t.model_ready
                ? "эмбеддинги: Ollama ПК (туннель) — доступна"
                : "эмбеддинги: Ollama ПК доступна, но модели нет";
            ragEmbedStatusEl.className = "rag-status " +
                (t.model_ready ? "ok" : "warn");
        } else {
            ragEmbedStatusEl.textContent =
                "эмбеддинги: Ollama ПК (туннель) — недоступна";
            ragEmbedStatusEl.className = "rag-status warn";
        }
    }

    // Показывает статус RAG: вкл/выкл, число файлов и фрагментов, модель.
    function renderRagStatus(extraText) {
        if (!ragStatusEl) return;
        if (!ragState.enabled) {
            ragStatusEl.textContent = "RAG выключен";
            ragStatusEl.className = "rag-status";
            return;
        }
        var parts = [];
        if (ragState.available) {
            parts.push("файлов: " + (ragState.files || 0));
            parts.push("фрагментов: " + (ragState.chunks || 0));
        } else {
            parts.push("индекс пуст — нажмите «Переиндексировать»");
        }
        if (ragState.embed_model) parts.push("модель: " + ragState.embed_model);
        if (ragState.updated) parts.push("обновлён: " + ragState.updated);
        if (extraText) parts.push(extraText);
        ragStatusEl.textContent = parts.join(" · ");
        ragStatusEl.className = "rag-status " + (ragState.available ? "ok" : "warn");
    }

    // Применяет состояние RAG, пришедшее от сервера.
    function applyRagState(d) {
        if (!d) return;
        if (typeof d.enabled === "boolean") ragState.enabled = d.enabled;
        if (typeof d.docs_dir === "string") ragState.docs_dir = d.docs_dir;
        if (typeof d.top_k === "number") ragState.top_k = d.top_k;
        if (typeof d.rewrite === "boolean") ragState.rewrite = d.rewrite;
        if (typeof d.hybrid === "boolean") ragState.hybrid = d.hybrid;
        if (typeof d.hybrid_alpha === "number") ragState.hybrid_alpha = d.hybrid_alpha;
        if (typeof d.pool_mult === "number") ragState.pool_mult = d.pool_mult;
        if (typeof d.min_score === "number") ragState.min_score = d.min_score;
        if (typeof d.rerank === "string") ragState.rerank = d.rerank;
        if (typeof d.files === "number") ragState.files = d.files;
        if (typeof d.chunks === "number") ragState.chunks = d.chunks;
        if (typeof d.available === "boolean") ragState.available = d.available;
        if (typeof d.embed_model === "string") ragState.embed_model = d.embed_model;
        if (d.embed_tunnel) ragState.embed_tunnel = d.embed_tunnel;
        if ("updated" in d) ragState.updated = d.updated;
        if (ragEnabledEl) ragEnabledEl.checked = ragState.enabled;
        if (ragDocsDirEl && document.activeElement !== ragDocsDirEl) {
            ragDocsDirEl.value = ragState.docs_dir || "";
        }
        if (ragTopKEl && document.activeElement !== ragTopKEl) {
            ragTopKEl.value = ragState.top_k || 5;
        }
        if (ragRewriteEl) ragRewriteEl.checked = !!ragState.rewrite;
        if (ragHybridEl) ragHybridEl.checked = !!ragState.hybrid;
        if (ragMinScoreEl && document.activeElement !== ragMinScoreEl) {
            ragMinScoreEl.value = (typeof ragState.min_score === "number")
                ? ragState.min_score : 0;
        }
        if (ragPoolMultEl && document.activeElement !== ragPoolMultEl) {
            ragPoolMultEl.value = ragState.pool_mult || 6;
        }
        if (ragRerankEl) ragRerankEl.value = ragState.rerank || "none";
        // Сообщение о проверке модели эмбеддингов (action: "check").
        if (d.embed_check) {
            if (d.embed_check.ok) {
                renderRagStatus("эмбеддинги: ок (dim " + d.embed_check.dim + ")");
            } else {
                renderRagStatus("эмбеддинги недоступны: " +
                                (d.embed_check.error || "?"));
            }
        } else {
            renderRagStatus();
        }
        renderRagEmbedStatus();
        refreshSections();
    }

    // Отправляет действие RAG на сервер.
    function ragAction(action, extra, done) {
        var payload = { action: action };
        if (extra) {
            Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; });
        }
        return fetch("/api/rag", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) applyRagState(d); if (done) done(d); return d; })
        .catch(function () { if (done) done(null); return null; });
    }

    // Загружает состояние RAG при старте страницы.
    function initRag() {
        fetch("/api/rag")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) applyRagState(d); })
            .catch(function () {});
    }

    if (ragEnabledEl) {
        ragEnabledEl.addEventListener("change", function () {
            var on = ragEnabledEl.checked;
            ragAction("set", { enabled: on });
            setStatus(on ? "RAG включён." : "RAG выключен.", "ok");
        });
    }
    if (ragDocsDirEl) {
        // Сохраняем путь при потере фокуса / Enter (чтобы не слать на каждый символ).
        var saveRagDir = function () {
            var dir = (ragDocsDirEl.value || "").trim();
            if (!dir) return;
            ragAction("set", { docs_dir: dir });
        };
        ragDocsDirEl.addEventListener("blur", saveRagDir);
        ragDocsDirEl.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); saveRagDir(); }
        });
    }
    if (ragTopKEl) {
        ragTopKEl.addEventListener("change", function () {
            ragAction("set", { top_k: parseInt(ragTopKEl.value, 10) || 5 });
        });
    }
    // Улучшенный поиск: query rewrite / гибрид / порог / пул / реранкер.
    if (ragRewriteEl) {
        ragRewriteEl.addEventListener("change", function () {
            ragAction("set", { rewrite: ragRewriteEl.checked });
        });
    }
    if (ragHybridEl) {
        ragHybridEl.addEventListener("change", function () {
            ragAction("set", { hybrid: ragHybridEl.checked });
        });
    }
    if (ragMinScoreEl) {
        ragMinScoreEl.addEventListener("change", function () {
            ragAction("set", { min_score: parseFloat(ragMinScoreEl.value) || 0 });
        });
    }
    if (ragPoolMultEl) {
        ragPoolMultEl.addEventListener("change", function () {
            ragAction("set", { pool_mult: parseInt(ragPoolMultEl.value, 10) || 6 });
        });
    }
    if (ragRerankEl) {
        ragRerankEl.addEventListener("change", function () {
            ragAction("set", { rerank: ragRerankEl.value });
        });
    }
    if (ragReindexBtn) {
        ragReindexBtn.addEventListener("click", function () {
            ragReindexBtn.disabled = true;
            if (ragStatusEl) {
                ragStatusEl.textContent = "индексирую документы и код… (может занять время)";
                ragStatusEl.className = "rag-status warn";
            }
            setStatus("RAG: индексация документов и кода…", "");
            ragAction("reindex", null, function (d) {
                ragReindexBtn.disabled = false;
                var rep = (d && d.report) || {};
                if (rep && rep.ok) {
                    var msg = "RAG: проиндексировано " + (rep.files || 0) +
                              " файлов, " + (rep.chunks || 0) + " фрагментов";
                    if (rep.errors && rep.errors.length) {
                        msg += " (ошибок: " + rep.errors.length + ")";
                    }
                    setStatus(msg, "ok");
                } else {
                    setStatus("RAG: индексация не удалась" +
                              (rep && rep.error ? " — " + rep.error : ""), "err");
                }
            });
        });
    }
    if (ragCheckBtn) {
        ragCheckBtn.addEventListener("click", function () {
            ragCheckBtn.disabled = true;
            ragAction("check").finally(function () { ragCheckBtn.disabled = false; });
        });
    }
    if (ragClearBtn) {
        ragClearBtn.addEventListener("click", function () {
            ragAction("clear");
            setStatus("RAG: индекс очищен.", "ok");
        });
    }

    // ------------------------------------------------------------------
    // ЛОКАЛЬНАЯ МОДЕЛЬ (llama.cpp / Ollama / LM Studio) — блок управления.
    // См. POST/GET /api/local: scan (корень папки), params (автоподбор ngl по
    // видеопамяти), start/stop/status, set (сохранение настроек). После запуска
    // источник «Local» появляется в списке моделей агента.
    var localEnabledEl = document.getElementById("local-enabled");
    var localRootEl = document.getElementById("local-root");
    var localServerSel = document.getElementById("local-server");
    var localModelSel = document.getElementById("local-model");
    var localScanBtn = document.getElementById("local-scan");
    var localStartBtn = document.getElementById("local-start");
    var localStopBtn = document.getElementById("local-stop");
    var localStatusBtn = document.getElementById("local-status-btn");
    var localSuggestEl = document.getElementById("local-suggest");
    var localStatusEl = document.getElementById("local-status");
    var localCtxEl = document.getElementById("local-ctx");
    var localNglEl = document.getElementById("local-ngl");
    var localThreadsEl = document.getElementById("local-threads");
    var localPortEl = document.getElementById("local-port");
    var localFlashEl = document.getElementById("local-flash");
    // --- Туннель к ПК (сильная модель + Ollama-эмбеддинги) ---
    var localPcScanEl = document.getElementById("local-pc-scan");
    var localPcTunnelEl = document.getElementById("local-pc-tunnel");
    var localEngineGroup = document.getElementById("local-engine-group");
    var localEngineStatusEl = document.getElementById("local-engine-status");
    var localPcPairEl = document.getElementById("local-pc-pair");
    var localPairBoxEl = document.getElementById("local-pair-box");
    var localPairCmdEl = document.getElementById("local-pair-cmd");
    var localPairCopyEl = document.getElementById("local-pair-copy");
    var localPairCheckEl = document.getElementById("local-pair-check");
    var localPairHideEl = document.getElementById("local-pair-hide");
    var localPairStatusEl = document.getElementById("local-pair-status");
    var localPairPollTimer = null;

    // Локальный снимок состояния блока.
    var localState = { enabled: false, root: "", server_path: "",
                       models: [], servers: [], running: false,
                       runtime: null, params: {},
                       tunnel: null, engine: "weak",
                       active_label: "", weak_label: "Qwen-2B",
                       strong_label: "Qwen-9B", pc_models: [], pair: null,
                       source: "vps", pc_root: "" };

    // Путь ПК: "E:\..." / "E:/..." или UNC "\\server\...".
    function isPcPath(path) {
        var p = String(path || "").trim();
        return /^[A-Za-z]:[\\/]/.test(p) || /^\\\\/.test(p);
    }

    // Читает параметры запуска из полей формы.
    function localParamsFromForm() {
        return {
            model: localModelSel ? localModelSel.value : "",
            ctx: parseInt(localCtxEl ? localCtxEl.value : 8192, 10) || 8192,
            ngl: parseInt(localNglEl ? localNglEl.value : 0, 10) || 0,
            threads: parseInt(localThreadsEl ? localThreadsEl.value : 0, 10) || 0,
            port: parseInt(localPortEl ? localPortEl.value : 8080, 10) || 8080,
            flash_attn: localFlashEl ? localFlashEl.value : "auto",
        };
    }

    // Показывает строку статуса блока (запущен/нет + URL).
    function renderLocalStatus(extraText) {
        if (!localStatusEl) return;
        var parts = [];
        if (localState.source === "pc") {
            parts.push(localState.running
                ? "ПК: модель запущена" : "ПК: модель не запущена");
            parts.push("слабая VPS работает");
        } else if (localState.running) {
            var rt = localState.runtime || {};
            parts.push("слабая VPS запущена");
            if (rt.url) parts.push(rt.url);
            if (rt.pid) parts.push("pid " + rt.pid);
            if (rt.ready === false) parts.push("не отвечает");
        } else {
            parts.push("локальная модель не запущена");
        }
        if (localState.models && localState.models.length) {
            parts.push("моделей найдено: " + localState.models.length);
        }
        // ЗАДАЧА 4: статистика локальной модели — видеопамять
        // (занято/всего), размер модели и параметры запуска.
        var rt2 = localState.runtime || {};
        if (localState.running && rt2) {
            if (rt2.vram_used_mb) {
                var vtot = rt2.vram_total_mb ? ' / ' + rt2.vram_total_mb : '';
                parts.push('VRAM ' + rt2.vram_used_mb + vtot + ' МБ');
            }
            if (rt2.model_mb) parts.push('модель ' + rt2.model_mb + ' МБ');
            var pp = rt2.params || {};
            if (pp.ngl !== undefined) parts.push('ngl=' + pp.ngl);
            if (pp.ctx) parts.push('ctx=' + pp.ctx);
            if (pp.threads) parts.push('threads=' + pp.threads);
        }
        if (extraText) parts.push(extraText);
        localStatusEl.textContent = parts.join(" · ");
        localStatusEl.className = "local-status " +
            (localState.running ? "ok" : "");
    }

    // Заполняет селекты серверов и моделей из результатов сканирования.
    function fillLocalSelects() {
        if (localServerSel) {
            var cur = localState.server_path || localServerSel.value || "";
            localServerSel.innerHTML = "";
            var hasCur = false;
            (localState.servers || []).forEach(function (s) {
                var o = document.createElement("option");
                o.value = s.path;
                o.textContent = (s.kind || "") + ": " + (s.name || s.path);
                localServerSel.appendChild(o);
                if (s.path === cur) hasCur = true;
            });
            // Сервер может быть вне корня моделей (например,
            // /usr/local/bin/llama-server на VPS) — показываем его отдельно.
            if (cur && !hasCur) {
                var oc = document.createElement("option");
                oc.value = cur;
                oc.textContent = "llama.cpp: " + cur;
                localServerSel.appendChild(oc);
            }
            if (cur) localServerSel.value = cur;
            localState.server_path = localServerSel.value || "";
        }
        if (localModelSel) {
            var curM = localModelSel.value || "";
            localModelSel.innerHTML = "";
            (localState.models || []).forEach(function (m) {
                var o = document.createElement("option");
                o.value = m.path;
                o.textContent = (m.name || m.path) +
                    (m.size_mb ? " (" + m.size_mb + " МБ)" : "");
                localModelSel.appendChild(o);
            });
            if (curM) localModelSel.value = curM;
            // После смены источника (VPS <-> ПК) прежний путь может
            // отсутствовать — выбираем первую доступную модель.
            if (!localModelSel.value && localModelSel.options.length) {
                localModelSel.selectedIndex = 0;
            }
        }
    }

    // Применяет состояние блока локальной модели, пришедшее от сервера.
    function applyLocalState(d) {
        if (!d) return;
        var wasRunning = localState.running;
        localState.enabled = !!d.enabled;
        if (d.pc_result) {
            // Результат сканирования папки НА ПК.
            localState.source = "pc";
            localState.pc_root = d.pc_result.root || localState.pc_root || "";
            localState.models = d.pc_models || d.pc_result.models || [];
            localState.servers = d.pc_result.servers || [];
            localState.server_path = "";
            localState.pc_scan = d.pc_result;
        } else if (d.scan) {
            // Результат локального сканирования VPS.
            localState.source = "vps";
            localState.root = d.root || "";
            localState.server_path = d.server_path || "";
            localState.models = d.models || [];
            localState.servers = d.servers || [];
        } else if (localState.source !== "pc") {
            localState.root = d.root || "";
            localState.server_path = d.server_path || "";
            localState.models = d.models || [];
            localState.servers = d.servers || [];
        }
        localState.running = localState.source === "pc"
            ? !!((d.tunnel || localState.tunnel || {}).pc_alive)
            : !!d.running;
        localState.runtime = d.runtime || null;
        if (d.params) localState.params = d.params;
        if (d.tunnel) localState.tunnel = d.tunnel;
        if (d.engine) localState.engine = d.engine;
        if (d.active_label) localState.active_label = d.active_label;
        if (d.weak_label) localState.weak_label = d.weak_label;
        if (d.strong_label) localState.strong_label = d.strong_label;
        if (d.pc_models) localState.pc_models = d.pc_models;
        if (d.pair) localState.pair = d.pair;
        renderLocalPair(d);

        if (localEnabledEl) localEnabledEl.checked = localState.enabled;
        if (localRootEl && document.activeElement !== localRootEl) {
            localRootEl.value = (localState.source === "pc"
                ? (localState.pc_root || "")
                : (localState.root || ""));
        }
        fillLocalSelects();
        // Параметры из снимка (если пришли) — подставляем, не затирая ввод.
        var p = localState.params || {};
        if (localCtxEl && document.activeElement !== localCtxEl && p.ctx) {
            localCtxEl.value = p.ctx;
        }
        if (localNglEl && document.activeElement !== localNglEl &&
                p.ngl !== undefined) {
            localNglEl.value = p.ngl;
        }
        if (localThreadsEl && document.activeElement !== localThreadsEl && p.threads) {
            localThreadsEl.value = p.threads;
        }
        if (localPortEl && document.activeElement !== localPortEl && p.port) {
            localPortEl.value = p.port;
        }
        if (localFlashEl && p.flash_attn) localFlashEl.value = p.flash_attn;

        // Результат запуска (если пришёл) — показываем отдельно.
        if (d.start) {
            if (d.start.ok) {
                setStatus("Локальная модель запущена: " + (d.start.url || ""), "ok");
            } else {
                setStatus("Не удалось запустить локальную модель: " +
                          (d.start.error || "?"), "error");
            }
        }
        // Подбор параметров (при сканировании) — показываем подсказку.
        if (d.suggest && localSuggestEl) {
            var s = d.suggest;
            localSuggestEl.hidden = false;
            localSuggestEl.textContent = "подобрано: ngl=" + s.ngl +
                (s.ngl_auto ? " (авто)" : "") + ", ctx=" + s.ctx +
                ", threads=" + s.threads +
                (s.vram_free_mb ? ", VRAM~" + s.vram_free_mb + " МБ" : "");
            // Подставляем предложенные параметры в поля.
            if (localCtxEl) localCtxEl.value = s.ctx;
            if (localNglEl) localNglEl.value = s.ngl;
            if (localThreadsEl) localThreadsEl.value = s.threads;
            if (localPortEl) localPortEl.value = s.port;
            if (localFlashEl && s.flash_attn) localFlashEl.value = s.flash_attn;
        }
        renderLocalStatus();
        renderLocalEngine();
        // Запустили/остановили локальный сервер — обновляем список моделей
        // (Local появляется/исчезает) и он включается по умолчанию.
        if (wasRunning !== localState.running) {
            refreshModels();
        }
        refreshSections();
    }

    // Отправляет действие с локальной моделью на сервер.
    function localAction(action, extra, done) {
        var payload = { action: action };
        if (extra) {
            Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; });
        }
        // Единый транспорт из core/api.js: обработка HTTP-ошибок в одном месте.
        return apiPost("/api/local", payload)
            .then(function (d) { if (d) applyLocalState(d); if (done) done(d); return d; })
            .catch(function () { if (done) done(null); return null; });
    }

    // Отрисовка панели «Поднять сильную модель (ПК)»: команда + статус.
    function renderLocalPair(d) {
        var pair = (d && d.pair) || localState.pair;
        if (pair) localState.pair = pair;
        if (localPairCmdEl && pair && pair.command) {
            localPairCmdEl.value = pair.command;
        }
        if (!localPairStatusEl) return;
        var tn = (d && d.tunnel) || localState.tunnel || {};
        if (tn.pc_alive) {
            localPairStatusEl.textContent =
                "ПК подключён — сильная модель доступна.";
            localPairStatusEl.className = "local-status ok";
        } else {
            localPairStatusEl.textContent =
                "ожидаю запуск ПК-агента на ПК…";
            localPairStatusEl.className = "local-status warn";
        }
    }

    // Фоновый опрос /api/local (action=pair), пока панель открыта.
    function startLocalPairPoll() {
        stopLocalPairPoll();
        localPairPollTimer = setInterval(function () {
            localAction("pair", null, function (d) {
                if (d) renderLocalPair(d);
            });
        }, 4000);
    }

    function stopLocalPairPoll() {
        if (localPairPollTimer) {
            clearInterval(localPairPollTimer);
            localPairPollTimer = null;
        }
    }

    // Отрисовывает активный движок (слабая/сильная) и доступность туннеля.
    function renderLocalEngine() {
        if (!localEngineStatusEl) return;
        var d = localState.tunnel || {};
        var eng = d.engine || localState.engine || "weak";
        var label = d.active_label || (eng === "strong"
            ? (localState.strong_label || "Qwen-9B")
            : (localState.weak_label || "Qwen-2B"));
        var parts = ["активна: " + label];
        if (d.pc_alive) {
            parts.push("ПК доступен" + (d.pc_models && d.pc_models.length
                ? " (" + d.pc_models.length + " мод.)" : ""));
        } else {
            parts.push("ПК недоступен");
        }
        if (d.confirmed) parts.push("подтверждено");
        if (d.embed_alive) {
            parts.push(d.embed_model_ready
                ? "эмбеддинги: Ollama ПК OK"
                : "Ollama ПК: нет модели эмбеддингов");
        }
        localEngineStatusEl.textContent = parts.join(" · ");
        localEngineStatusEl.className = "local-status " +
            (eng === "strong" ? "ok" : "");
        if (localEngineGroup) {
            var btns = localEngineGroup.querySelectorAll(".engine-btn");
            for (var i = 0; i < btns.length; i++) {
                btns[i].classList.toggle("active",
                    btns[i].dataset.engine === eng);
            }
        }
    }

    // Загружает состояние блока при старте страницы.
    function initLocal() {
        apiGet("/api/local")
            .then(function (d) { if (d) applyLocalState(d); })
            .catch(function () {});
    }

    if (localEnabledEl) {
        localEnabledEl.addEventListener("change", function () {
            var on = localEnabledEl.checked;
            localAction("set", { enabled: on });
            setStatus(on ? "Локальная модель включена."
                         : "Локальная модель выключена.", "ok");
        });
    }
    if (localScanBtn) {
        localScanBtn.addEventListener("click", function () {
            var root = localRootEl ? localRootEl.value.trim() : "";
            if (!root) { setStatus("Укажите путь к папке моделей.", "error"); return; }
            var pc = isPcPath(root);
            localState.source = pc ? "pc" : "vps";
            if (pc) localState.pc_root = root;
            localScanBtn.disabled = true;
            if (localStatusEl) {
                localStatusEl.textContent = pc
                    ? "сканирую папку НА ПК…" : "сканирую папку…";
                localStatusEl.className = "local-status warn";
            }
            localAction(pc ? "pc_scan" : "scan", { root: root }, function (d) {
                localScanBtn.disabled = false;
                if (pc) {
                    var res = (d && d.pc_result) || {};
                    if (res.error) {
                        setStatus("ПК-агент недоступен: " +
                            (res.error || "нет туннеля"), "error");
                    } else {
                        setStatus("На ПК найдено моделей: " +
                            ((res.models || []).length) + ", серверов: " +
                            ((res.servers || []).length), "ok");
                    }
                } else if (d && d.scan && d.scan.ok) {
                    setStatus("Найдено моделей: " + (d.scan.models || []).length +
                              ", серверов: " + (d.scan.servers || []).length, "ok");
                } else {
                    setStatus("В папке не найдено ни моделей, ни сервера.", "error");
                }
            });
        });
    }
    if (localRootEl) {
        var saveLocalRoot = function () {
            var root = (localRootEl.value || "").trim();
            if (!root) return;
            if (isPcPath(root)) {
                // Путь ПК не сохраняем в настройки VPS-модели.
                localState.pc_root = root;
                localState.source = "pc";
                return;
            }
            localState.source = "vps";
            localAction("set", { root: root });
        };
        localRootEl.addEventListener("blur", saveLocalRoot);
        localRootEl.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); saveLocalRoot(); }
        });
    }
    if (localServerSel) {
        localServerSel.addEventListener("change", function () {
            localState.server_path = localServerSel.value || "";
            localAction("set", { server_path: localState.server_path });
        });
    }
    if (localStartBtn) {
        localStartBtn.addEventListener("click", function () {
            var params = localParamsFromForm();
            if (!params.model) {
                setStatus("Сначала отсканируйте папку и выберите модель.", "error");
                return;
            }
            localStartBtn.disabled = true;
            if (localStatusEl) {
                localStatusEl.textContent = "запускаю сервер… (загрузка модели)";
                localStatusEl.className = "local-status warn";
            }
            var pc = localState.source === "pc";
            setStatus(pc ? "Запускаю модель НА ПК…"
                         : "Локальная модель: запуск сервера…", "");
            localAction(pc ? "pc_start" : "start",
                        { server_path: localState.server_path,
                          params: params }, function (d) {
                localStartBtn.disabled = false;
                if (pc) {
                    var res = (d && d.pc_start_result) || {};
                    if (res.ok === false) {
                        setStatus("Не удалось запустить модель ПК: " +
                            (res.error || "?"), "error");
                    } else {
                        setStatus("Модель ПК запущена, туннель активен.", "ok");
                    }
                } else if (d && d.start && d.start.already) {
                    setStatus("Слабая модель VPS уже работает.", "ok");
                }
            });
        });
    }
    if (localStopBtn) {
        localStopBtn.addEventListener("click", function () {
            localStopBtn.disabled = true;
            var pc = localState.source === "pc";
            localAction(pc ? "pc_stop" : "stop", null, function (d) {
                localStopBtn.disabled = false;
                if (pc) {
                    var res = (d && d.pc_stop_result) || {};
                    setStatus(res.ok === false
                        ? "Не удалось остановить модель ПК: " +
                          (res.error || "?")
                        : "Модель ПК остановлена, активна слабая (VPS).",
                        res.ok === false ? "error" : "ok");
                } else if (d && d.stopped === false) {
                    setStatus("Слабая модель VPS работает постоянно.", "ok");
                } else {
                    setStatus("Локальная модель остановлена.", "ok");
                }
            });
        });
    }
    if (localStatusBtn) {
        localStatusBtn.addEventListener("click", function () {
            localAction("status");
        });
    }
    // Поля параметров: сохраняем в настройки при изменении.
    [localCtxEl, localNglEl, localThreadsEl, localPortEl, localFlashEl]
        .forEach(function (el) {
            if (!el) return;
            el.addEventListener("change", function () {
                localAction("set", { params: localParamsFromForm() });
            });
        });

    // --- Переключение движка: слабая (VPS) <-> сильная (ПК) ---
    if (localEngineGroup) {
        localEngineGroup.addEventListener("click", function (e) {
            var btn = e.target.closest(".engine-btn");
            if (!btn) return;
            var want = btn.dataset.engine;
            var cur = (localState.tunnel || {}).engine ||
                localState.engine || "weak";
            if (want === cur) return;
            var action = want === "strong" ? "tunnel_up" : "tunnel_down";
            setStatus(want === "strong"
                ? "Включаю сильную модель (ПК)…"
                : "Возвращаю слабую модель (VPS)…", "");
            localAction(action, null, function (d) {
                var res = (d && d.tunnel_result) || {};
                if (res.ok === false) {
                    setStatus("Не удалось переключить модель: " +
                        (res.error || "?"), "error");
                } else {
                    setStatus(want === "strong"
                        ? "Активна сильная модель (ПК)."
                        : "Активна слабая модель (VPS).", "ok");
                }
            });
        });
    }

    // Сканирование папки НА ПК (VPS просит ПК-агент через служебный API).
    if (localPcScanEl) {
        localPcScanEl.addEventListener("click", function () {
            var root = localRootEl ? localRootEl.value.trim() : "";
            localPcScanEl.disabled = true;
            setStatus("Сканирую папку НА ПК…", "");
            localAction("pc_scan", { root: root }, function (d) {
                localPcScanEl.disabled = false;
                var res = (d && d.pc_result) || {};
                if (res.ok === false || res.error) {
                    setStatus("ПК-агент недоступен: " +
                        (res.error || "нет туннеля"), "error");
                } else {
                    setStatus("На ПК найдено моделей: " +
                        ((res.models || []).length) + ", серверов: " +
                        ((res.servers || []).length), "ok");
                }
            });
        });
    }

    // «Поднять сильную модель (ПК)»: показать команду и следить за туннелем.
    if (localPcPairEl) {
        localPcPairEl.addEventListener("click", function () {
            if (localPairBoxEl) localPairBoxEl.hidden = false;
            setStatus("Готовлю команду для ПК…", "");
            localAction("pair", null, function (d) {
                if (d) renderLocalPair(d);
            });
            startLocalPairPoll();
        });
    }
    if (localPairHideEl) {
        localPairHideEl.addEventListener("click", function () {
            if (localPairBoxEl) localPairBoxEl.hidden = true;
            stopLocalPairPoll();
        });
    }
    if (localPairCopyEl) {
        localPairCopyEl.addEventListener("click", function () {
            var cmd = localPairCmdEl ? localPairCmdEl.value : "";
            if (!cmd) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(cmd).then(function () {
                    setStatus("Команда скопирована — вставьте её в "
                        + "PowerShell на ПК.", "ok");
                }).catch(function () {
                    if (localPairCmdEl) localPairCmdEl.select();
                    setStatus("Скопируйте команду вручную (Ctrl+C).", "warn");
                });
            } else if (localPairCmdEl) {
                localPairCmdEl.select();
                setStatus("Скопируйте команду вручную (Ctrl+C).", "warn");
            }
        });
    }
    if (localPairCheckEl) {
        localPairCheckEl.addEventListener("click", function () {
            localPairCheckEl.disabled = true;
            setStatus("Проверяю подключение ПК…", "");
            localAction("pair", null, function (d) {
                localPairCheckEl.disabled = false;
                if (d) renderLocalPair(d);
                var tn = (d && d.tunnel) || localState.tunnel || {};
                if (tn.pc_alive) {
                    localAction("tunnel_up", null, function (dd) {
                        var res = (dd && dd.tunnel_result) || {};
                        if (res.ok === false) {
                            setStatus("Туннель есть, но включить сильную "
                                + "модель не удалось: " + (res.error || "?"),
                                "error");
                        } else {
                            setStatus("Готово: активна сильная модель (ПК).",
                                "ok");
                            stopLocalPairPoll();
                            if (localPairBoxEl) localPairBoxEl.hidden = true;
                        }
                    });
                } else {
                    setStatus("ПК пока не подключился. Запустите команду "
                        + "на ПК и нажмите «Проверить подключение» ещё раз.",
                        "warn");
                }
            });
        });
    }

    // Проверка/подъём туннеля к ПК (включить сильную модель).
    if (localPcTunnelEl) {
        localPcTunnelEl.addEventListener("click", function () {
            localPcTunnelEl.disabled = true;
            setStatus("Проверяю туннель к ПК…", "");
            localAction("tunnel_up", null, function (d) {
                localPcTunnelEl.disabled = false;
                var res = (d && d.tunnel_result) || {};
                if (res.ok === false) {
                    setStatus("Туннель к ПК недоступен: " +
                        (res.error || "?"), "error");
                } else {
                    setStatus("Туннель к ПК активен — сильная модель.", "ok");
                }
            });
        });
    }

    // ------------------------------------------------------------------
    // СЕКЦИИ (сворачивание) и автоподбор ширины панели
    // ------------------------------------------------------------------
    // Сворачивание блоков левой колонки (.side-sec) и автоподбор ширины
    // .side вынесены в ОТДЕЛЬНЫЙ модуль features/sections.js (рефакторинг
    // на ES-модули). Здесь лишь ПОДКЛЮЧАЕМ его и передаём функцию-геттер
    // состояния «использования» секций (dependency injection).
    sectionsRefresh = initSections(function () {
        return {
            strategy: !!(strategyState && strategyState.strategy &&
                         strategyState.strategy !== "none"),
            task: !!(taskState && taskState.active),
            profiles: !!(profilesState && profilesState.active),
            invariants: !!(invState && invState.invariants &&
                           invState.invariants.length),
            mcp: !!(mcpState && (mcpState.enabled ||
                                 mcpState.composition_running)),
            rag: !!(ragState && ragState.enabled && ragState.available),
            local: !!(localState && (localState.enabled ||
                                     localState.running)),
            summary: !!(compactState && parseInt(compactState.keep, 10) > 0),
            memory: (function () {
                var w = memWorkingBox ?
                    memWorkingBox.querySelectorAll(".mem-row").length : 0;
                var l = memLongtermBox ?
                    memLongtermBox.querySelectorAll(".mem-row").length : 0;
                return (w + l) > 0;
            })(),
        };
    });
    autoFitSide();
    // Регистрируем модуль секций в общем реестре (подключаемая
    // архитектура): другие модули могут получить его через
    // registry.get("sections") или попросить перерисовать через
    // bus.emit("sections:refresh").
    registry.add("sections", { refresh: sectionsRefresh });
    // Сигнал шине: приложение инициализировано. Модули-плагины могут
    // подписаться на это событие для собственной инициализации.
    bus.emit("app:ready", {});

    loadSession();

    // Сторож сессии: следит за лимитом мест и завершением сессии.
    initSessionGuard();
})();
