// AI 分身交易所 - 前端互動
document.addEventListener('DOMContentLoaded', () => {
    // 滾動淡入動畫
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('fadeUp');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });
    document.querySelectorAll('.avatar-card, .price-card, .biz-card, .step').forEach(el => {
        observer.observe(el);
    });

    // 數字滾動計數 (data-count)
    const counters = document.querySelectorAll('[data-count]');
    const countObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const el = entry.target;
                const target = parseInt(el.dataset.count, 10);
                const dur = 1200;
                const start = performance.now();
                const tick = (now) => {
                    const p = Math.min((now - start) / dur, 1);
                    el.textContent = Math.floor(target * (1 - Math.pow(1 - p, 3)));
                    if (p < 1) requestAnimationFrame(tick);
                    else el.textContent = target;
                };
                requestAnimationFrame(tick);
                countObserver.unobserve(el);
            }
        });
    }, { threshold: 0.5 });
    counters.forEach(el => countObserver.observe(el));

    // 平滑滾動
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) target.scrollIntoView({ behavior: 'smooth' });
        });
    });

    // 分身卡片點擊效果
    document.querySelectorAll('.avatar-card').forEach(card => {
        card.addEventListener('click', () => {
            card.style.transform = 'scale(0.98)';
            setTimeout(() => card.style.transform = '', 150);
        });
    });

    // 對話預覽：點「立即僱用」模擬對話
    document.querySelectorAll('[data-hire]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const name = btn.dataset.hire;
            const demo = document.getElementById('demo-chat');
            if (!demo) return;
            demo.scrollIntoView({ behavior: 'smooth' });
            const nameEl = document.getElementById('demo-name');
            if (nameEl) nameEl.textContent = name;
            const input = demo.querySelector('.demo-input');
            if (input) {
                input.value = '嗨！' + name + '，我想請你幫我...';
                input.focus();
            }
        });
    });

    // 送出按鈕 - 模擬分身回覆
    const sendBtn = document.querySelector('.demo-send');
    const input = document.querySelector('.demo-input');
    if (sendBtn && input) {
        const reply = () => {
            const msg = input.value.trim();
            if (!msg) return;
            const demo = document.getElementById('demo-chat');
            if (!demo) return;
            const userMsg = document.createElement('div');
            userMsg.className = 'demo-msg user';
            userMsg.textContent = msg;
            const aiMsg = document.createElement('div');
            aiMsg.className = 'demo-msg ai';
            aiMsg.textContent = '收到，老闆！這個任務我接下來了。🎯（正式版將由 AI 分身即時回覆）';
            const row = demo.querySelector('.demo-input-row');
            demo.insertBefore(userMsg, row);
            demo.insertBefore(aiMsg, row);
            input.value = '';
            demo.scrollIntoView({ behavior: 'smooth' });
        };
        sendBtn.addEventListener('click', reply);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') reply(); });
    }
});
