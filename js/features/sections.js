/* features/sections.js — сворачивание секций левой колонки + автоподбор
 * ширины панели.
 *
 * Функциональность вынесена из монолитного app.js (рефакторинг на модули).
 * Модуль самостоятелен: работает с .side-sec / .side и не зависит от
 * остальных feature-модулей. Экспортирует initSections() и autoFitSide().
 *
 * «Подключаемость»: модуль регистрируется в core/bus и вызывается app.js
 * на старте. Его можно отключить, просто не вызывая init.
 */
import { bus } from "../core/bus.js";

// Ключ localStorage, где хранится карта «секция -> свёрнута».
var SECTIONS_KEY = "ds-side-sections";

/* Проверяет, ИСПОЛЬЗУЕТСЯ ли секция прямо сейчас (по состоянию модели или
 * наличию данных). Если свёрнутая секция используется — её заголовок
 * подсвечивается оливковым (CSS .side-sec.in-use), чтобы не потерять
 * активный функционал.
 *
 * getUsage() — callback, который предоставляет app.js: возвращает объект с
 * флагами состояния (strategy/task/profiles/invariants/mcp/rag/local/…).
 * Это развязывает модуль секций и бизнес-логику (dependency injection). */
function sectionInUse(name, usage) {
    usage = usage || {};
    switch (name) {
        case "strategy":   return !!usage.strategy;
        case "task":       return !!usage.task;
        case "profiles":   return !!usage.profiles;
        case "invariants": return !!usage.invariants;
        case "mcp":        return !!usage.mcp;
        case "rag":        return !!usage.rag;
        case "local":      return !!usage.local;
        case "keys":       return !!usage.keys;
        case "summary":    return !!usage.summary;
        case "memory":     return !!usage.memory;
        default:           return false;
    }
}

// Обновляет класс .in-use у всех секций по актуальному состоянию.
function refreshSections(usage) {
    var secs = document.querySelectorAll(".side-sec");
    for (var i = 0; i < secs.length; i++) {
        var nm = secs[i].dataset.section;
        secs[i].classList.toggle("in-use", sectionInUse(nm, usage));
    }
}

// Чтение/запись карты свёрнутости в localStorage.
function readCollapsedMap() {
    try {
        return JSON.parse(localStorage.getItem(SECTIONS_KEY) || "{}") || {};
    } catch (e) { return {}; }
}
function writeCollapsedMap(map) {
    try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(map)); } catch (e) {}
}

/* Устанавливает обработчики сворачивания секций.
 * getUsage — callback состояния (см. sectionInUse). Возвращает функцию
 * refresh() — перерисовать подсветку по требованию. */
export function initSections(getUsage) {
    var secs = document.querySelectorAll(".side-sec");
    var saved = readCollapsedMap();
    for (var i = 0; i < secs.length; i++) {
        (function (sec) {
            var name = sec.dataset.section;
            var head = sec.querySelector(".side-sec-head");
            if (!head) return;
            // Применяем сохранённое состояние сразу (до клика пользователя).
            var collapsed = !!(saved[name]);
            sec.classList.toggle("collapsed", collapsed);
            head.setAttribute("aria-expanded", collapsed ? "false" : "true");
            head.addEventListener("click", function () {
                var nowCollapsed = !sec.classList.contains("collapsed");
                sec.classList.toggle("collapsed", nowCollapsed);
                head.setAttribute("aria-expanded", nowCollapsed ? "false" : "true");
                var m = readCollapsedMap();
                m[name] = nowCollapsed;
                writeCollapsedMap(m);
                refreshSections(getUsage && getUsage());
            });
        })(secs[i]);
    }
    refreshSections(getUsage && getUsage());
    var refresh = function () { refreshSections(getUsage && getUsage()); };
    // Шина: другие модули могут попросить перерисовать подсветку секций,
    // когда меняется их состояние (например, включили MCP/RAG).
    bus.on("sections:refresh", refresh);
    return refresh;
}

/* Автоподбор ширины левой панели по самому широкому содержимому.
 *
 * Проблема: секции и кнопки имеют разную длину текста, и при узкой панели
 * подписи обрезаются. Здесь временно измеряем natural-width каждого ребёнка
 * колонки (max-content + nowrap) и выставляем ширину .side в [MIN, MAX].
 * Значение пишется в inline-style, чтобы не конфликтовать с CSS. */
export function autoFitSide() {
    var side = document.querySelector(".side");
    if (!side) return;
    var MIN = 180, MAX = 380;

    function measure() {
        var widest = MIN;
        var kids = side.children;
        for (var i = 0; i < kids.length; i++) {
            var el = kids[i];
            // Временный max-content + nowrap, чтобы измерить ширину «как есть».
            var prevWidth = el.style.width;
            var prevWhite = el.style.whiteSpace;
            el.style.width = "max-content";
            el.style.whiteSpace = "nowrap";
            var w = el.scrollWidth;
            el.style.width = prevWidth;
            el.style.whiteSpace = prevWhite;
            if (w > widest) widest = w;
        }
        // Учитываем внутренние отступы самой колонки (padding).
        var cs = window.getComputedStyle(side);
        var pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
        var target = Math.ceil(widest + pad + 4);
        if (target < MIN) target = MIN;
        if (target > MAX) target = MAX;
        side.style.width = target + "px";
    }

    measure();
    // Пересчитываем при изменениях окна и загрузке (шрифты подгружаются позже).
    window.addEventListener("resize", measure);
    window.addEventListener("load", measure);
    // И несколько раз с задержкой — на случай поздних изменений контента
    // (после ответов /api/*, подгрузки шрифтов и т.п.).
    setTimeout(measure, 300);
    setTimeout(measure, 1000);
    // Позволяем другим модулям инициировать пересчёт (напр. после добавления
    // строки в длинную секцию).
    bus.on("side:measure", measure);
}
