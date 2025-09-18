document.addEventListener('DOMContentLoaded', function() {
    const i18n = i18next.createInstance();

    async function loadTranslations() {
        const [enRes, zhRes] = await Promise.all([
            fetch('./static/locales/en.json'),
            fetch('./static/locales/zh.json')
        ]);
        return {
            en: await enRes.json(),
            zh: await zhRes.json()
        };
    }

    function updateUI() {
        document.querySelectorAll('[data-i18n]').forEach(function(element) {
            const key = element.getAttribute('data-i18n');
            element.textContent = i18n.t(key);
        });
    }

    function initLanguageSelector() {
        const langSelect = document.getElementById('languageSelect');
        if (langSelect) {
            // 从存储获取设置的语言，如果没有默认zh
            const preferredLang = localStorage.getItem('preferredLanguage') || 'zh';
            langSelect.value = preferredLang;
            
            // 监听语言选择变化
            langSelect.addEventListener('change', function() {
                const lang = this.value;
                localStorage.setItem('preferredLanguage', lang);
                i18n.changeLanguage(lang).then(updateUI);
            });
        }
    }

    loadTranslations().then(translations => {
        return i18n.init({
            resources: translations,
            lng: localStorage.getItem('preferredLanguage') || 'zh',
            fallbackLng: 'en',
            interpolation: {
                escapeValue: false
            }
        });
    }).then(() => {
        initLanguageSelector();
        updateUI();
        
        // 监听语言变化事件
        i18n.on('languageChanged', updateUI);
    });
});