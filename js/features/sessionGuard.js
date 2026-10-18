/* features/sessionGuard.js — клиентский сторож серверной сессии (VPS).
 *
 * Реализует клиентскую часть §5 ТЗ (docs/vps-llm-service.md):
 *   * периодически опрашивает GET /api/heartbeat;
 *   * при state == "check" показывает модальный диалог «Вы здесь?»
 *     с обратным отсчётом до deadline; подтверждение шлёт /api/ask-подобный
 *     «пинг» (любой реальный запрос продлит сессию) — используем /api/session;
 *   * при state == "offline" переводит страницу в read-only и предлагает
 *     «Начать новую сессию» (POST /api/newslot);
 *   * следит за HTTP-ответами 503/409 (переполнение/завершение) на любых
 *     запросах и показывает соответствующий экран.
 *
 * Модуль самодостаточен: создаёт свой DOM-оверлей, не требует правок HTML.
 * Экспорт: initSessionGuard().
 */
import { apiGet, apiPost } from "../core/api.js";

var HEARTBEAT_MS = 5000;      // период опроса состояния (мягкий, ~5 c)
var busy = false;             // идёт показ диалога «Вы здесь?»
var overlay = null;
var readonly = false;
var timer = null;
var countdownTimer = null;
var lastState = "active";

function ensureOverlay() {
    if (overlay) return overlay;
    if (!document || !document.body) return null;
    overlay = document.createElement("div");
    overlay.className = "sg-overlay";
    overlay.hidden = true;
    overlay.innerHTML =
        '<div class="sg-box" role="dialog" aria-modal="true">' +
        '  <h3 class="sg-title">Вы здесь?</h3>' +
        '  <p class="sg-text"></p>' +
        '  <div class="sg-actions">' +
        '    <button type="button" class="sg-yes primary">Я здесь</button>' +
        '    <button type="button" class="sg-new">Начать новую сессию</button>' +
        '  </div>' +
        '  <div class="sg-countdown"></div>' +
        '</div>';
    document.body.appendChild(overlay);
    return overlay;
}

function setText(msg) {
    if (!overlay) return;
    var el = overlay.querySelector(".sg-text");
    if (el) el.textContent = msg;
}

function showCheck(deadline) {
    if (readonly) return;
    if (!ensureOverlay()) return;
    overlay.hidden = false;
    overlay.dataset.mode = "check";
    setText("Мы не видели вас некоторое время. Подтвердите, что сессия ещё " +
            "нужна — иначе она освободится для других.");
    var nw = overlay.querySelector(".sg-new");
    var yes = overlay.querySelector(".sg-yes");
    if (nw) nw.hidden = true;
    if (yes) yes.hidden = false;
    startCountdown(deadline);
}

function showOffline(reason) {
    readonly = true;
    if (!ensureOverlay()) return;
    overlay.hidden = false;
    overlay.dataset.mode = "offline";
    setText(reason || "Ваша сессия завершена. Страница доступна только для " +
            "чтения. Начните новую сессию, чтобы продолжить.");
    var yes = overlay.querySelector(".sg-yes");
    var nw = overlay.querySelector(".sg-new");
    if (yes) yes.hidden = true;
    if (nw) nw.hidden = false;
    stopCountdown();
    applyReadonly(true);
}

function hideOverlay() {
    if (overlay) overlay.hidden = true;
    stopCountdown();
}

function startCountdown(deadline) {
    stopCountdown();
    if (!deadline || !overlay) return;
    countdownTimer = setInterval(function () {
        var left = Math.max(0, Math.round(deadline - Date.now() / 1000));
        var cd = overlay && overlay.querySelector(".sg-countdown");
        if (cd) cd.textContent = left > 0
            ? "осталось " + left + " c" : "истекает…";
        if (left <= 0) stopCountdown();
    }, 1000);
}

function stopCountdown() {
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    var cd = overlay && overlay.querySelector(".sg-countdown");
    if (cd) cd.textContent = "";
}

function applyReadonly(on) {
    document.body.classList.toggle("sg-readonly", !!on);
    var q = document.getElementById("question");
    var s = document.getElementById("submit");
    if (q) q.disabled = !!on;
    if (s) s.disabled = !!on;
}

// Подтверждение «Я здесь»: любой реальный запрос продлевает сессию.
// Используем GET /api/session (is_activity — да, это не heartbeat).
function confirmHere() {
    apiGet("/api/session")
        .then(function () {
            hideOverlay();
            readonly = false;
        })
        .catch(function () {});
}

// Запрос новой сессии (кнопка «Начать новую сессию»).
// Сервер: POST /api/newchat {renew:true} — выдаёт НОВЫЙ cookie sid,
// ОСВОБОЖДАЯ прежний слот. Владельцу слот находится приоритетно.
function startNewSession() {
    apiPost("/api/newchat", { renew: true })
        .then(function (d) {
            if (d && d.ok) {
                // Сервер выдал новый sid; перезагружаем страницу, чтобы
                // подтянуть состояние новой сессии.
                window.location.reload();
            }
        })
        .catch(function (err) {
            // 503 — мест всё ещё нет: сообщаем и оставляем read-only.
            setText("Свободных сессий сейчас нет. Попробуйте ещё раз через " +
                    "минуту.");
        });
}

// При ОБНОВЛЕНИИ страницы освобождаем прежний слот: просим сервер выдать
// новый sid взамен старого (старый слот при этом освобождается). Делаем это
// ОДИН раз за загрузку страницы. Если ротация не удалась (мест нет) —
// работаем с текущей сессией.
function rotateOnLoad() {
    if (window.__sgRotated) return;
    window.__sgRotated = true;
    apiPost("/api/newchat", { renew: true })
        .then(function (d) {
            if (d && d.ok && d.renewed) {
                // Новый sid уже в cookie; ничего перезагружать не нужно —
                // последующие запросы пойдут по новой сессии.
            }
        })
        .catch(function () { /* не мешаем работе: остаёмся на текущем sid */ });
}

// Реакция на HTTP-ошибки сессии из других запросов (перехват fetch).
function installFetchInterceptor() {
    if (typeof fetch !== "function" || window.__sgFetchPatched) return;
    window.__sgFetchPatched = true;
    var origFetch = fetch;
    window.fetch = function (input, init) {
        return origFetch.call(window, input, init).then(function (resp) {
            if (resp.status === 503) {
                // Переполнение: показываем заглушку-оверлей.
                showOffline("Все сессии заняты. Освободится через минуту — " +
                            "либо начните новую.");
            } else if (resp.status === 409) {
                showOffline();
            }
            return resp;
        });
    };
}

function poll() {
    apiGet("/api/heartbeat")
        .then(function (d) {
            var st = d && d.session ? d.session.state : "active";
            if (st === lastState) {
                // Состояние не изменилось — обновим только countdown.
                if (st === "check" && d.session.deadline && busy) {
                    startCountdown(d.session.deadline);
                }
                return;
            }
            lastState = st;
            if (st === "check") {
                busy = true;
                showCheck(d.session.deadline);
            } else if (st === "offline") {
                busy = false;
                showOffline();
            } else {
                busy = false;
                hideOverlay();
                readonly = false;
                applyReadonly(false);
            }
        })
        .catch(function () { /* сеть недоступна — тихо пропускаем */ });
}

export function initSessionGuard() {
    installFetchInterceptor();
    ensureOverlay();
    var yes = overlay && overlay.querySelector && overlay.querySelector(".sg-yes");
    var nw = overlay && overlay.querySelector && overlay.querySelector(".sg-new");
    if (yes) yes.addEventListener("click", confirmHere);
    if (nw) nw.addEventListener("click", startNewSession);

    // ОБНОВЛЕНИЕ СТРАНИЦЫ освобождает прежний слот (ротация sid).
    rotateOnLoad();

    // Первый опрос чуть позже загрузки, затем — периодически.
    timer = setInterval(poll, HEARTBEAT_MS);
    setTimeout(poll, 2000);
    return function stop() { if (timer) clearInterval(timer); };
}
