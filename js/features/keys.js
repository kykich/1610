/* features/keys.js — ключи облачных моделей (DeepSeek / GigaChat).
 *
 * Ключи живут ТОЛЬКО в памяти этой страницы (переменная модуля):
 *   * ничего не пишем в localStorage / sessionStorage / cookie / файлы;
 *   * перезагрузка страницы, закрытие окна и «новый разговор» их стирают —
 *     ключ нужно ввести заново;
 *   * на сервер ключи уходят в теле /api/ask (поле keys) и действуют только
 *     на время этого запроса (см. rtk_app/runtime_keys.py).
 *
 * Если поле пустое, сервер использует файлы apidpsk.txt / gigakey.txt.
 *
 * Экспорт: initClientKeys, clientKeysPayload, clearClientKeys,
 *          hasClientKeys.
 */
import { apiGet, apiPost } from "../core/api.js";
import { bus } from "../core/bus.js";

// ЕДИНСТВЕННОЕ хранилище ключей на клиенте — память вкладки.
var state = { deepseek: "", gigachat: "" };

var els = null;
// Сведения о файлах-фолбэках (только маски, приходят из GET /api/keys).
var fileInfo = { deepseek: null, gigachat: null };

// Маска для показа: "sk-…abcd" (полный ключ в DOM/логи не попадает).
function mask(value) {
    var s = String(value || "");
    if (!s) return "";
    var tail = s.length > 8 ? s.slice(-4) : s.slice(-2);
    return (s.indexOf("sk-") === 0 ? "sk-…" : "…") + tail;
}

function setStatus(el, text, kind) {
    if (!el) return;
    el.textContent = text || "";
    el.className = "mcp-status" + (kind ? " " + kind : "");
    el.title = text || "";
}

// Показывает состояние ключа: введён в поле / взят из файла / не задан.
function refreshStatus(provider) {
    if (!els) return;
    var value = state[provider] || "";
    var status = provider === "deepseek" ? els.dsStatus : els.gcStatus;
    var info = fileInfo[provider] || {};
    if (value) {
        setStatus(status, "задан · " + mask(value), "ok");
        return;
    }
    if (info.file) {
        setStatus(status, "из файла · " + (info.masked || ""), "");
        return;
    }
    setStatus(status, "не задан", "");
}

function readInputs() {
    if (!els) return;
    state.deepseek = (els.ds && els.ds.value ? els.ds.value : "").trim();
    state.gigachat = (els.gc && els.gc.value ? els.gc.value : "").trim();
    refreshStatus("deepseek");
    refreshStatus("gigachat");
    // Подсветка свёрнутой секции «Ключи облачных моделей».
    bus.emit("sections:refresh", {});
}

// Проверка вставленного ключа одним пробным запросом (ничего не сохраняем).
function checkKey(provider) {
    var value = state[provider] || "";
    var status = provider === "deepseek" ? els.dsStatus : els.gcStatus;
    if (!value) {
        setStatus(status, "введите ключ", "error");
        return;
    }
    setStatus(status, "проверяю…", "");
    apiPost("/api/keys", { action: "check", provider: provider, key: value })
        .then(function (d) {
            if (d && d.ok) {
                setStatus(status, "работает · " + (d.masked || mask(value)),
                          "ok");
            } else {
                setStatus(status, (d && d.detail) || "ключ не принят",
                          "error");
            }
            if (d && d.hint) status.title = d.hint;
        })
        .catch(function (err) {
            setStatus(status, "ошибка: " + err.message, "error");
        });
}

// Стирает ключи из памяти и полей (новый разговор / восстановление страницы).
export function clearClientKeys() {
    state.deepseek = "";
    state.gigachat = "";
    if (els) {
        if (els.ds) els.ds.value = "";
        if (els.gc) els.gc.value = "";
    }
    refreshStatus("deepseek");
    refreshStatus("gigachat");
    bus.emit("sections:refresh", {});
}

// Тело поля "keys" для /api/ask: пустые значения не отправляем.
// Значения читаем из полей «на месте» — так автозаполнение браузера
// (без события input) тоже попадёт в запрос.
export function clientKeysPayload() {
    if (els) {
        state.deepseek = (els.ds && els.ds.value ? els.ds.value : "").trim();
        state.gigachat = (els.gc && els.gc.value ? els.gc.value : "").trim();
    }
    var payload = {};
    if (state.deepseek) payload.deepseek = state.deepseek;
    if (state.gigachat) payload.gigachat = state.gigachat;
    return payload;
}

// Задан ли хотя бы один ключ (для подсветки секции).
export function hasClientKeys() {
    if (els) {
        state.deepseek = (els.ds && els.ds.value ? els.ds.value : "").trim();
        state.gigachat = (els.gc && els.gc.value ? els.gc.value : "").trim();
    }
    return !!(state.deepseek || state.gigachat);
}

export function initClientKeys() {
    els = {
        ds: document.getElementById("ds-key"),
        gc: document.getElementById("gc-key"),
        dsStatus: document.getElementById("ds-key-status"),
        gcStatus: document.getElementById("gc-key-status"),
        dsCheck: document.getElementById("ds-key-check"),
        gcCheck: document.getElementById("gc-key-check"),
        clear: document.getElementById("keys-clear"),
    };
    if (!els.ds && !els.gc) return;

    // Жёсткая очистка при загрузке: браузер умеет восстанавливать значения
    // полей после перезагрузки — ключи не должны «воскресать».
    state.deepseek = "";
    state.gigachat = "";
    if (els.ds) els.ds.value = "";
    if (els.gc) els.gc.value = "";
    refreshStatus("deepseek");
    refreshStatus("gigachat");

    if (els.ds) els.ds.addEventListener("input", readInputs);
    if (els.gc) els.gc.addEventListener("input", readInputs);
    if (els.dsCheck) els.dsCheck.addEventListener("click", function () {
        checkKey("deepseek");
    });
    if (els.gcCheck) els.gcCheck.addEventListener("click", function () {
        checkKey("gigachat");
    });
    if (els.clear) els.clear.addEventListener("click", clearClientKeys);

    // Возврат из bfcache может восстановить память JS — ключи стираем.
    window.addEventListener("pageshow", function (ev) {
        if (ev && ev.persisted) clearClientKeys();
    });

    // Подсказка о файлах-фолбэках (только маски; сами ключи не отдаются).
    apiGet("/api/keys").then(function (d) {
        fileInfo.deepseek = (d && d.deepseek) || null;
        fileInfo.gigachat = (d && d.gigachat) || null;
        refreshStatus("deepseek");
        refreshStatus("gigachat");
    }).catch(function () { /* нет слота/сети — не критично */ });
}
