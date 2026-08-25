/* ============================================================
 * notify.js — LLM 完成提示（聲音 + 震動 + 瀏覽器通知）
 * by indexPage | 2026-08-23
 * - 聲音：Web Audio API 合成雙音提示音（無需音檔，手機/電腦皆可）
 * - 震動：navigator.vibrate（手機支援）
 * - 通知：Notification API（需瀏覽器授權，點擊 🔔 按鈕即會請求）
 * - 設定存於 localStorage，預設開啟
 * ============================================================ */
(function () {
    'use strict';
    var KEY = 'mok_notify_enabled';
    var SOUND_KEY = 'mok_notify_sound';
    var VIB_KEY = 'mok_notify_vibrate';
    var NOTIF_KEY = 'mok_notify_browser';

    var audioCtx = null;
    var enabled = localStorage.getItem(KEY) !== '0';
    var soundOn = localStorage.getItem(SOUND_KEY) !== '0';
    var vibOn = localStorage.getItem(VIB_KEY) !== '0';
    var notifOn = localStorage.getItem(NOTIF_KEY) !== '0';

    /* ---------- 初始化音頻上下文（需用戶手勢解鎖） ---------- */
    function ensureAudio() {
        try {
            if (!audioCtx) {
                var AC = window.AudioContext || window.webkitAudioContext;
                if (AC) audioCtx = new AC();
            }
            if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
        } catch (e) { audioCtx = null; }
    }

    /* ---------- 播放提示音（清脆雙音 chime） ---------- */
    function playSound() {
        if (!soundOn) return;
        ensureAudio();
        if (!audioCtx) return;
        try {
            var now = audioCtx.currentTime;
            var notes = [880, 1174.66]; // A5 → D6
            notes.forEach(function (freq, idx) {
                var osc = audioCtx.createOscillator();
                var gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.value = freq;
                var t = now + idx * 0.18;
                gain.gain.setValueAtTime(0.0001, t);
                gain.gain.exponentialRampToValueAtTime(0.35, t + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.55);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(t);
                osc.stop(t + 0.6);
            });
        } catch (e) {}
    }

    /* ---------- 震動 ---------- */
    function vibrate() {
        if (!vibOn) return;
        try {
            if (navigator.vibrate) navigator.vibrate([220, 120, 220]);
        } catch (e) {}
    }

    /* ---------- 瀏覽器通知授權 ---------- */
    function requestPermission() {
        if (!('Notification' in window)) return Promise.resolve('unsupported');
        if (Notification.permission === 'granted') return Promise.resolve('granted');
        if (Notification.permission === 'denied') return Promise.resolve('denied');
        try {
            return Notification.requestPermission();
        } catch (e) {
            return Promise.resolve('error');
        }
    }

    /* ---------- 顯示系統通知（僅頁面未聚焦時，避免重複干擾） ---------- */
    function showBrowserNotification(agent, reply) {
        if (!notifOn || !('Notification' in window)) return;
        if (Notification.permission !== 'granted') return;
        if (document.hasFocus && document.hasFocus()) return;
        try {
            var body = (reply || '').replace(/\s+/g, ' ').trim().slice(0, 90) || '已完成';
            var n = new Notification('🤖 ' + agent + ' 完成囉', {
                body: body,
                tag: 'mok-done-' + agent + '-' + Date.now(),
                renotify: true
            });
            n.onclick = function () { try { window.focus(); n.close(); } catch (e) {} };
            setTimeout(function () { try { n.close(); } catch (e) {} }, 20000);
        } catch (e) {}
    }

    /* ---------- 對外：收到 done 事件時呼叫 ---------- */
    function done(agent, reply) {
        if (!enabled) return;
        if (!agent) return;
        playSound();
        vibrate();
        showBrowserNotification(agent, reply);
        updateBtn();
    }

    /* ---------- 頂部按鈕狀態 ---------- */
    function updateBtn() {
        var btn = document.getElementById('notifyToggleBtn');
        if (!btn) return;
        if (!enabled) {
            btn.textContent = '🔕';
            btn.title = '完成提示已關閉（點擊開啟）';
            btn.style.opacity = '0.55';
        } else {
            btn.textContent = '🔔';
            btn.title = '完成提示已開啟（聲音+震動+通知）';
            btn.style.opacity = '1';
        }
    }

    function toggle() {
        enabled = !enabled;
        localStorage.setItem(KEY, enabled ? '1' : '0');
        if (enabled) {
            ensureAudio();               // 藉由點擊解鎖音頻
            requestPermission().then(function () { updateBtn(); });
            playSound();                 // 開啟時回饋一聲
            try { if (navigator.vibrate) navigator.vibrate(30); } catch (e) {}
        } else {
            try { if (navigator.vibrate) navigator.vibrate([30, 50, 30]); } catch (e) {}
        }
        updateBtn();
    }

    /* ---------- 初始化：確保按鈕存在並綁定 ---------- */
    function init() {
        var btn = document.getElementById('notifyToggleBtn');
        if (!btn) {
            btn = document.createElement('button');
            btn.id = 'notifyToggleBtn';
            btn.style.cssText = 'margin-left:6px; background:#9b59b6; border:none; border-radius:16px; padding:2px 12px; cursor:pointer; font-size:0.8rem; color:#fff; flex-shrink:0;';
            var ref = document.getElementById('showExperienceBtn') || document.getElementById('agentHeaderDisplay');
            if (ref && ref.parentNode) {
                ref.parentNode.insertBefore(btn, ref.nextSibling);
            } else {
                var header = document.querySelector('.chat-header-left');
                if (header) header.appendChild(btn);
            }
        }
        btn.addEventListener('click', toggle);
        updateBtn();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.Notifier = {
        done: done,
        toggle: toggle,
        requestPermission: requestPermission,
        isEnabled: function () { return enabled; }
    };
})();
