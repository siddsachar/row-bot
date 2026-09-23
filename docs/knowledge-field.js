(() => {
    'use strict';

    const canvas = document.querySelector('[data-knowledge-field]');
    const story = document.querySelector('[data-story-controller]');
    const host = canvas?.parentElement;
    const context = canvas?.getContext('2d', { alpha: true });
    if (!canvas || !story || !host || !context) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const saveData = Boolean(navigator.connection?.saveData);
    let frozen = new URLSearchParams(window.location.search).get('motion') === 'freeze';
    const colors = {
        research: [71, 217, 255],
        create: [213, 173, 114],
        automate: [116, 221, 190],
        ship: [92, 150, 255]
    };
    let scene = story.dataset.scene in colors ? story.dataset.scene : 'research';
    let nodes = [];
    let edges = [];
    let width = 0;
    let height = 0;
    let visible = true;
    let frame = 0;
    let lastDraw = 0;
    const pointer = { x: .5, y: .52 };

    function random(index, salt = 0) {
        const value = Math.sin((index + 1) * 127.1 + salt * 311.7) * 43758.5453;
        return value - Math.floor(value);
    }

    function positionFor(beat, index, count) {
        const a = index / count * Math.PI * 2;
        const jitter = random(index, 1) - .5;
        if (beat === 'create') return {
            x: ((index % 7) - 3) * .37 + jitter * .11,
            y: (Math.floor(index / 7) - Math.floor(count / 14)) * .27 + (random(index, 2) - .5) * .1,
            z: (random(index, 3) - .5) * 1.3
        };
        if (beat === 'automate') return {
            x: Math.cos(a) * (1.19 + jitter * .16),
            y: Math.sin(a) * (.77 + jitter * .13),
            z: Math.sin(a * 2) * .55
        };
        if (beat === 'ship') return {
            x: (index < count / 2 ? -1 : 1) * (.48 + random(index, 4) * .9),
            y: (random(index, 5) - .5) * 1.75,
            z: (random(index, 6) - .5) * 1.2
        };
        const cluster = index % 4;
        return {
            x: [-1.07, -.35, .43, 1.12][cluster] + jitter * .72,
            y: (random(index, 7) - .5) * 1.9,
            z: (random(index, 8) - .5) * 1.5
        };
    }

    function buildEdges() {
        edges = [];
        if (scene === 'automate') {
            for (let index = 0; index < nodes.length; index += 1) {
                edges.push([index, (index + 1) % nodes.length]);
                if (index % 3 === 0) edges.push([index, (index + 3) % nodes.length]);
            }
            return;
        }
        for (let index = 0; index < nodes.length; index += 1) {
            const from = nodes[index].target;
            const near = [];
            for (let next = index + 1; next < nodes.length; next += 1) {
                if (scene === 'ship' && (index < nodes.length / 2) !== (next < nodes.length / 2)) continue;
                const to = nodes[next].target;
                const distance = Math.hypot(from.x - to.x, from.y - to.y, (from.z - to.z) * .65);
                if (distance < .82) near.push({ next, distance });
            }
            near.sort((a, b) => a.distance - b.distance);
            near.slice(0, 2).forEach(({ next }) => edges.push([index, next]));
        }
    }

    function setScene(next) {
        if (!(next in colors) || next === scene) return;
        scene = next;
        nodes.forEach((node, index) => { node.target = positionFor(scene, index, nodes.length); });
        buildEdges();
        if (!frame && visible && !document.hidden && !canvas.hidden) draw(0, true);
    }

    function resize() {
        const bounds = canvas.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        const ratio = Math.min(window.devicePixelRatio || 1, 1.5, Math.sqrt(1_200_000 / (width * height)));
        canvas.width = Math.max(1, Math.floor(width * ratio));
        canvas.height = Math.max(1, Math.floor(height * ratio));
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        const count = width < 760 ? 42 : 90;
        if (nodes.length !== count) {
            nodes = Array.from({ length: count }, (_, index) => {
                const target = positionFor(scene, index, count);
                return { x: target.x, y: target.y, z: target.z, target };
            });
            buildEdges();
        }
        draw(0, true);
    }

    function draw(time, still = false) {
        if (!width || !height || canvas.hidden) return;
        context.clearRect(0, 0, width, height);
        const color = colors[scene];
        const haloX = width * pointer.x;
        const haloY = height * pointer.y;
        const halo = context.createRadialGradient(haloX, haloY, 0, haloX, haloY, Math.max(width, height) * .56);
        halo.addColorStop(0, `rgba(${color.join(',')},.14)`);
        halo.addColorStop(1, `rgba(${color.join(',')},0)`);
        context.fillStyle = halo;
        context.fillRect(0, 0, width, height);

        const turn = (pointer.x - .5) * .23 + (still ? 0 : Math.sin(time * .00021) * .065);
        const tilt = (pointer.y - .5) * -.15;
        const cosine = Math.cos(turn);
        const sine = Math.sin(turn);
        const scale = Math.min(width * .36, height * .57);
        const projected = nodes.map((node, index) => {
            if (!still) {
                node.x += (node.target.x - node.x) * .065;
                node.y += (node.target.y - node.y) * .065;
                node.z += (node.target.z - node.z) * .065;
            }
            const x = node.x * cosine - node.z * sine;
            const z = node.x * sine + node.z * cosine;
            const drift = still ? 0 : Math.sin(time * .0007 + index * 1.9) * .018;
            const perspective = 3.5 / (3.9 + z);
            return {
                x: width * .5 + x * scale * perspective,
                y: height * .56 + (node.y + drift + x * tilt) * scale * perspective,
                depth: Math.max(.4, Math.min(1.25, perspective))
            };
        });
        context.lineWidth = .8;
        edges.forEach(([a, b], index) => {
            const start = projected[a];
            const end = projected[b];
            const strength = Math.min(start.depth, end.depth);
            context.strokeStyle = `rgba(${color.join(',')},${(.19 * strength).toFixed(3)})`;
            context.beginPath();
            context.moveTo(start.x, start.y);
            context.lineTo(end.x, end.y);
            context.stroke();
            if (!still && index % 11 === 0) {
                const progress = (time * .00016 + index * .11) % 1;
                context.fillStyle = `rgba(${color.join(',')},${(.64 * strength).toFixed(3)})`;
                context.beginPath();
                context.arc(start.x + (end.x - start.x) * progress,
                    start.y + (end.y - start.y) * progress, 1.7 * strength, 0, Math.PI * 2);
                context.fill();
            }
        });
        projected.forEach((point, index) => {
            const hub = index % 13 === 0;
            if (hub) {
                const glow = context.createRadialGradient(point.x, point.y, 0,
                    point.x, point.y, 23 * point.depth);
                glow.addColorStop(0, `rgba(${color.join(',')},.22)`);
                glow.addColorStop(1, `rgba(${color.join(',')},0)`);
                context.fillStyle = glow;
                context.beginPath();
                context.arc(point.x, point.y, 23 * point.depth, 0, Math.PI * 2);
                context.fill();
            }
            context.fillStyle = `rgba(${color.join(',')},${(hub ? 1 : .63) * point.depth})`;
            context.beginPath();
            context.arc(point.x, point.y, (hub ? 3 : 1.55) * point.depth, 0, Math.PI * 2);
            context.fill();
        });
    }

    function tick(time) {
        frame = window.requestAnimationFrame(tick);
        if (time - lastDraw < (width < 760 ? 50 : 33)) return;
        lastDraw = time;
        draw(time);
    }

    function syncMotion() {
        const allowed = !reduceMotion.matches && !saveData && !frozen;
        canvas.hidden = !allowed;
        if (!allowed || !visible || document.hidden) {
            window.cancelAnimationFrame(frame);
            frame = 0;
            return;
        }
        if (!frame) frame = window.requestAnimationFrame(tick);
    }

    host.addEventListener('pointermove', event => {
        if (event.pointerType === 'touch' || canvas.hidden) return;
        const bounds = host.getBoundingClientRect();
        pointer.x = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
        pointer.y = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height));
    }, { passive: true });
    host.addEventListener('pointerleave', () => { pointer.x = .5; pointer.y = .52; });
    new MutationObserver(() => setScene(story.dataset.scene)).observe(story, { attributes: true, attributeFilter: ['data-scene'] });
    if ('IntersectionObserver' in window) {
        new IntersectionObserver(entries => {
            visible = Boolean(entries[0]?.isIntersecting);
            syncMotion();
        }, { threshold: .01 }).observe(host);
    }
    if ('ResizeObserver' in window) new ResizeObserver(resize).observe(canvas);
    else window.addEventListener('resize', resize);
    document.addEventListener('visibilitychange', syncMotion);
    reduceMotion.addEventListener?.('change', syncMotion);
    resize();
    syncMotion();
    window.RowBotKnowledgeField = {
        freeze() { frozen = true; syncMotion(); }
    };
})();
