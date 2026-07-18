(() => {
    'use strict';

    const WINDOWS_URL = 'https://github.com/siddsachar/row-bot/releases/download/v4.5.0/Row-Bot-4.5.0-Windows-x64.exe';
    const MAC_URL = 'https://github.com/siddsachar/row-bot/releases/download/v4.5.0/Row-Bot-4.5.0-macOS-arm64.dmg';
    const LINUX_TARGET = '#install';
    const LINUX_COMMAND = 'curl -fsSL https://raw.githubusercontent.com/siddsachar/row-bot/v4.5.0/installer/install-linux.sh | bash -s -- 4.5.0';

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function platformChoice() {
        const platform = String(navigator.userAgentData?.platform || navigator.platform || navigator.userAgent || '').toLowerCase();
        if (platform.includes('mac')) return { label: 'Download for macOS', name: 'macOS', hint: '.dmg', href: MAC_URL, platform: 'macos' };
        if (platform.includes('linux') || platform.includes('x11')) return { label: 'Install on Linux', name: 'Linux', hint: 'curl', href: LINUX_TARGET, platform: 'linux' };
        return { label: 'Download for Windows', name: 'Windows', hint: '.exe', href: WINDOWS_URL, platform: 'windows' };
    }

    const selectedPlatform = platformChoice();
    document.querySelectorAll('[data-os-primary]').forEach(link => {
        link.href = selectedPlatform.href;
        link.dataset.download = selectedPlatform.platform;
        link.innerHTML = link.dataset.osLabel === 'short'
            ? 'Download'
            : `${selectedPlatform.label} <small>· ${selectedPlatform.hint}</small>`;
    });

    document.querySelectorAll('[data-platform-note]').forEach(note => {
        note.textContent = `Detected: ${selectedPlatform.name}. Choose any platform:`;
    });
    document.querySelectorAll('[data-platform-choice]').forEach(link => {
        link.classList.toggle('is-detected', link.dataset.platformChoice === selectedPlatform.platform);
    });

    function trackSiteEvent(name, params) {
        if (typeof window.gtag === 'function') window.gtag('event', name, params || {});
    }

    function trackAdsConversion(params) {
        if (typeof window.gtag === 'function' && window.ROW_BOT_GOOGLE_ADS_CONVERSION_ID) {
            window.gtag('event', 'conversion', {
                send_to: window.ROW_BOT_GOOGLE_ADS_CONVERSION_ID,
                ...(params || {})
            });
        }
    }

    document.querySelectorAll('[data-download]').forEach(link => {
        link.addEventListener('click', event => {
            const standardClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
            const samePageHash = link.hash && link.origin === window.location.origin && link.pathname === window.location.pathname;
            trackSiteEvent('download_click', { platform: link.dataset.download, link_url: link.href });
            if (samePageHash || !standardClick || link.target === '_blank') {
                trackAdsConversion({ platform: link.dataset.download });
                return;
            }
            event.preventDefault();
            let navigated = false;
            const navigate = () => {
                if (!navigated) {
                    navigated = true;
                    window.location.href = link.href;
                }
            };
            trackAdsConversion({ platform: link.dataset.download, event_callback: navigate });
            window.setTimeout(navigate, 700);
        });
    });

    document.querySelectorAll('[data-copy-linux]').forEach(button => {
        button.addEventListener('click', async () => {
            const previous = button.textContent;
            try {
                await navigator.clipboard.writeText(LINUX_COMMAND);
                button.textContent = 'Copied';
            } catch (_error) {
                const command = document.querySelector('[data-linux-command]');
                if (command) window.getSelection()?.selectAllChildren(command);
                button.textContent = 'Select text';
            }
            window.setTimeout(() => { button.textContent = previous; }, 1600);
        });
    });

    const nav = document.querySelector('.site-nav');
    const navToggle = document.querySelector('.nav-hamburger');
    const setNavOpen = open => {
        nav?.classList.toggle('is-open', open);
        navToggle?.setAttribute('aria-expanded', String(open));
    };
    navToggle?.addEventListener('click', () => setNavOpen(!nav?.classList.contains('is-open')));
    document.querySelectorAll('.nav-menu a').forEach(link => {
        link.addEventListener('click', () => {
            setNavOpen(false);
        });
    });

    const progress = document.querySelector('.site-progress');
    if (progress) {
        const updateProgress = () => {
            const available = document.documentElement.scrollHeight - document.documentElement.clientHeight;
            progress.style.width = `${available > 0 ? Math.min(100, document.documentElement.scrollTop / available * 100) : 0}%`;
        };
        updateProgress();
        window.addEventListener('scroll', updateProgress, { passive: true });
    }

    if (!reduceMotion && 'IntersectionObserver' in window) {
        const revealObserver = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('is-visible');
                    revealObserver.unobserve(entry.target);
                }
            });
        }, { threshold: 0.11 });
        document.querySelectorAll('.reveal').forEach(element => revealObserver.observe(element));
    } else {
        document.querySelectorAll('.reveal').forEach(element => element.classList.add('is-visible'));
    }

    const stage = document.querySelector('.core-stage');
    if (stage && !reduceMotion && window.matchMedia('(pointer: fine)').matches) {
        const visual = stage.closest('.hero-visual');
        visual?.addEventListener('pointermove', event => {
            const bounds = visual.getBoundingClientRect();
            const x = (event.clientX - bounds.left) / bounds.width - 0.5;
            const y = (event.clientY - bounds.top) / bounds.height - 0.5;
            stage.style.setProperty('--ry', `${8 + x * 7}deg`);
            stage.style.setProperty('--rx', `${-4 - y * 5}deg`);
        });
        visual?.addEventListener('pointerleave', () => {
            stage.style.setProperty('--ry', '8deg');
            stage.style.setProperty('--rx', '-4deg');
        });
    }

    document.querySelectorAll('[data-youtube]').forEach(facade => {
        const activate = () => {
            const id = facade.dataset.youtube;
            if (!id) return;
            const iframe = document.createElement('iframe');
            iframe.src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1`;
            iframe.title = facade.getAttribute('aria-label') || 'Row-Bot product video';
            iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture';
            iframe.allowFullscreen = true;
            facade.replaceChildren(iframe);
        };
        facade.addEventListener('click', activate, { once: true });
        facade.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                activate();
            }
        }, { once: true });
    });
})();
