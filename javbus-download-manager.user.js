// ==UserScript==
// @name         JavBus download manager (marker + missing magnet downloader)
// @namespace    https://github.com/roy/javbus-scripts
// @version      3.1
// @description  On any JavBus list page, tag the items you have already downloaded (checked via the Everything app HTTP server), and batch-download the missing ones by opening detail tabs and sending the preferred magnet to your torrent client. Default location scan covers all videos; mesubuta and 1000giri keep their custom rules.
// @author       Roy
// @match        https://www.javbus.com/*
// @grant        GM_openInTab
// @grant        GM_addStyle
// @grant        GM_xmlhttpRequest
// @connect       localhost
// @connect       127.0.0.1
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    // ===== Config =========================================================
    const CONFIG = {
        // Everything's built-in HTTP server (Tools > Options > HTTP Server).
        everythingHost: 'http://127.0.0.1:80',

        // Default locations scanned for every item. An item counts as
        // downloaded if its bango is found under ANY of these folders.
        defaultLocations: [
            'E:\\115\\云下载',
            'E:\\115\\!NSFW',
        ],

        // Per-studio custom rules. Key = studio id in the URL (/studio/<id>).
        //   locations : folder(s) to search instead of the defaults
        //   stem      : turn the page bango into the Everything search term
        // Default bango = the last path segment of the detail link
        //   e.g. "130311-RIO" for https://www.javbus.com/130311-RIO
        customRules: {
            // mesubuta: 160401_1042_01 -> 160401_1042 (drop trailing part no.)
            '3u': {
                locations: ['E:\\115\\!NSFW\\Anthology\\mesubuta'],
                stem: (b) => b.replace(/_\d+$/, ''),
            },
            // 1000giri: 150821-YUINA -> 150821 (leading digits only)
            '3s': {
                locations: ['E:\\115\\!NSFW\\Anthology\\1000 Giri'],
                stem: (b) => (b.match(/\d+/) || [b])[0],
            },
            // Gachinco: GACHI-1156 -> GACHI 1156 (hyphen to space)
            '3r': {
                locations: ['E:\\115\\!NSFW\\Anthology\\Gachinco'],
                stem: (b) => b.replace(/-/g, ''),
            },
        },

        // ---- Everything check tuning ----
        checkConcurrency: 8,
        checkTimeoutMs: 15000,

        // ---- Downloader tuning ----
        maxOpenDetailTabs: 10,
        detailTabOpenGapMs: 500,
        batchWaitMs: 10000,
        detailTimeoutMs: 45000,
        magnetOpenDelayMs: 1500,
        autoNextPageDelayMs: 3000,
        pollMs: 800,
        priorityWords: ['thz', 'ses-23', 'sis001', 'arsenal'],

        // ---- Storage keys ----
        taskKey: 'javbus-dm-tasks-v1',
        historyKey: 'javbus-dm-history-v1',
        runKey: 'javbus-dm-running-v1',
        panelKey: 'javbus-dm-panel-visible-v1',
    };
    // ======================================================================

    const detailCode = getDetailCode();
    if (detailCode) {
        detailMain(detailCode);
    } else if (isListPage()) {
        listPageMain();
    }

    // ---- page-type detection ---------------------------------------------
    function isListPage() {
        return !!document.querySelector('#waterfall .item a.movie-box');
    }

    function getDetailCode() {
        if (/^\/uncensored\//.test(location.pathname)) return null;
        if (/^\/(?:ajax|forum|genre|star|studio|search|actresses|series|label|director|post|doc)\b/.test(location.pathname)) return null;
        const code = location.pathname.replace(/^\/+|\/+$/g, '');
        if (!code || code.includes('/')) return null;
        return /[0-9]/.test(code) ? code : null;
    }

    function getStudioId() {
        const m = location.pathname.match(/\/studio\/([^\/]+)/);
        return m ? m[1] : null;
    }

    function getRuleForPage() {
        const id = getStudioId();
        const custom = id && CONFIG.customRules[id];
        return {
            locations: (custom && custom.locations) || CONFIG.defaultLocations,
            stem: (custom && custom.stem) || ((b) => b),
        };
    }

    function getCurrentPageNo() {
        const m = location.pathname.match(/\/(\d+)\/?$/);
        return m ? Number(m[1]) : 1;
    }

    // ---- storage helpers --------------------------------------------------
    function readJson(key, fallback) {
        try { return JSON.parse(localStorage.getItem(key)) || fallback; }
        catch (_) { return fallback; }
    }
    function writeJson(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
    function readTasks() { return readJson(CONFIG.taskKey, {}); }
    function writeTasks(tasks) { writeJson(CONFIG.taskKey, tasks); }
    function readHistory() { return readJson(CONFIG.historyKey, { processed: {}, failed: {} }); }
    function writeHistory(history) { writeJson(CONFIG.historyKey, history); }
    function isRunning() { return localStorage.getItem(CONFIG.runKey) === '1'; }
    function setRunning(v) { if (v) localStorage.setItem(CONFIG.runKey, '1'); else localStorage.removeItem(CONFIG.runKey); }

    // ---- Everything query -------------------------------------------------
    function everythingSearch(code, rule) {
        const stem = rule.stem(code);
        // <"<loc>" stem> joined by OR; <> groups so AND/OR precedence is clear.
        // Locations are quoted so chars like ! and spaces are taken literally.
        return rule.locations.map((loc) => `<"${loc}" ${stem}>`).join(' | ');
    }

    function everythingCount(code, rule) {
        const url = CONFIG.everythingHost + '/?search=' +
            encodeURIComponent(everythingSearch(code, rule)) + '&json=1&count=1';
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                timeout: CONFIG.checkTimeoutMs,
                onload: (res) => {
                    try {
                        const data = JSON.parse(res.responseText);
                        const n = (typeof data.totalResults === 'number')
                            ? data.totalResults
                            : (data.results ? data.results.length : 0);
                        resolve(n);
                    } catch (e) { reject(new Error('parse: ' + e.message)); }
                },
                onerror: () => reject(new Error('network')),
                ontimeout: () => reject(new Error('timeout')),
            });
        });
    }

    // ======================================================================
    //  LIST PAGE: marker + downloader
    // ======================================================================
    function listPageMain() {
        const rule = getRuleForPage();
        let checksDone = false;
        let checkStats = { have: 0, missing: 0, errors: 0, done: 0, total: 0 };
        let panel, logEl, toggleBtn;
        let timer = null;
        let lastDetailTabOpenAt = 0;
        let openedInCurrentBatch = 0;
        let nextBatchAllowedAt = 0;

        GM_addStyle(`
            #jb-dm-toggle {
                position: fixed; right: 12px; bottom: 12px; z-index: 100000;
                border: 0; border-radius: 4px; padding: 6px 10px; color: #fff;
                background: #1976d2; cursor: pointer; font: 700 13px/1 Arial, sans-serif;
                box-shadow: 0 4px 14px rgba(0,0,0,.35);
            }
            #jb-dm-panel {
                position: fixed; right: 12px; bottom: 12px; z-index: 99999;
                width: 380px; max-height: 74vh; overflow: hidden;
                background: rgba(20,20,20,.94); color: #f5f5f5;
                border: 1px solid rgba(255,255,255,.16); border-radius: 6px;
                box-shadow: 0 8px 30px rgba(0,0,0,.35);
                font: 13px/1.45 Arial, sans-serif;
            }
            #jb-dm-panel button {
                border: 0; border-radius: 4px; padding: 5px 8px; color: #fff;
                background: #1976d2; cursor: pointer; font-weight: 700;
            }
            #jb-dm-panel button.secondary { background: #555; }
            #jb-dm-panel button.danger { background: #c62828; }
            #jb-dm-head {
                display: flex; align-items: center; justify-content: space-between;
                gap: 8px; padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,.12);
            }
            #jb-dm-body { padding: 10px; }
            #jb-dm-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
            #jb-dm-status { margin-bottom: 8px; color: #ddd; }
            #jb-dm-log {
                max-height: 250px; overflow: auto; padding: 6px;
                background: rgba(255,255,255,.06); border-radius: 4px;
                white-space: pre-wrap; word-break: break-word; color: #ddd;
            }
            /* downloaded = the only "marked" state by default */
            .item.dl-have > .movie-box { position: relative; outline: 3px solid #43a047; }
            .dl-have-badge {
                position: absolute; top: 6px; left: 6px; z-index: 20;
                padding: 2px 7px; border-radius: 4px; font: 700 12px/1 sans-serif;
                color: #fff; background: #43a047; pointer-events: none;
            }
            /* downloader states (apply to missing items) */
            .jb-dm-queued > .movie-box { outline: 3px solid #1976d2 !important; }
            .jb-dm-done   > .movie-box { outline: 3px solid #8e24aa !important; opacity: .72; }
            .jb-dm-failed > .movie-box { outline: 3px solid #fb8c00 !important; }
        `);

        buildPanel();
        buildToggle();
        applyPanelVisibility();
        updateStatus();
        runChecks();
        if (isRunning()) startMonitor();

        // ---- Everything scan ----
        function getAllItems() {
            return Array.from(document.querySelectorAll('#waterfall .item'))
                .map((item) => {
                    const a = item.querySelector('a.movie-box[href]');
                    if (!a) return null;
                    const url = new URL(a.href, location.href).href;
                    const code = url.replace(/\/+$/, '').split('/').pop();
                    return { item, a, url, code };
                })
                .filter(Boolean);
        }

        function runChecks() {
            const items = getAllItems();
            checkStats = { have: 0, missing: 0, errors: 0, done: 0, total: items.length };
            updateStatus();
            let idx = 0;
            const next = () => {
                if (idx >= items.length) return;
                const entry = items[idx++];
                everythingCount(entry.code, rule)
                    .then((n) => {
                        entry.item.classList.add('dl-checked');
                        if (n > 0) {
                            entry.item.classList.add('dl-have');
                            addHaveBadge(entry.item);
                            checkStats.have++;
                        } else {
                            entry.item.classList.add('dl-missing');
                            checkStats.missing++;
                        }
                    })
                    .catch(() => {
                        entry.item.classList.add('dl-checked');
                        entry.item.dataset.dmCheckError = '1';
                        checkStats.errors++;
                    })
                    .finally(() => {
                        checkStats.done++;
                        updateStatus();
                        if (checkStats.done >= items.length) {
                            checksDone = true;
                            paintItems();
                            updateStatus();
                            if (isRunning()) ensureQueued();
                        }
                        next();
                    });
            };
            for (let i = 0; i < Math.min(CONFIG.checkConcurrency, items.length); i++) next();
            if (!items.length) checksDone = true;
        }

        function addHaveBadge(item) {
            const box = item.querySelector('.movie-box');
            if (!box || box.querySelector('.dl-have-badge')) return;
            const b = document.createElement('div');
            b.className = 'dl-have-badge';
            b.textContent = '✔ DL';
            box.appendChild(b);
        }

        // missing = checked, not found, has code
        function getMissingItems() {
            return getAllItems().filter((e) =>
                e.item.classList.contains('dl-missing'));
        }

        // ---- panel / toggle ----
        function buildPanel() {
            panel = document.createElement('div');
            panel.id = 'jb-dm-panel';
            panel.innerHTML = `
                <div id="jb-dm-head">
                    <strong>JavBus downloader</strong>
                    <button class="secondary" id="jb-dm-hide" type="button">Hide</button>
                </div>
                <div id="jb-dm-body">
                    <div id="jb-dm-actions">
                        <button id="jb-dm-start" type="button">Start</button>
                        <button class="danger" id="jb-dm-stop" type="button">Stop</button>
                        <button class="secondary" id="jb-dm-retry" type="button">Retry page</button>
                        <button class="secondary" id="jb-dm-clear" type="button">Clear history</button>
                    </div>
                    <div id="jb-dm-status"></div>
                    <div id="jb-dm-log"></div>
                </div>`;
            document.body.appendChild(panel);
            logEl = panel.querySelector('#jb-dm-log');
            panel.querySelector('#jb-dm-start').addEventListener('click', start);
            panel.querySelector('#jb-dm-stop').addEventListener('click', stop);
            panel.querySelector('#jb-dm-retry').addEventListener('click', retryPage);
            panel.querySelector('#jb-dm-clear').addEventListener('click', clearHistory);
            panel.querySelector('#jb-dm-hide').addEventListener('click', () => setPanelVisible(false));
        }

        function buildToggle() {
            toggleBtn = document.createElement('button');
            toggleBtn.id = 'jb-dm-toggle';
            toggleBtn.type = 'button';
            toggleBtn.textContent = '⬇ Downloader';
            toggleBtn.addEventListener('click', () => setPanelVisible(true));
            document.body.appendChild(toggleBtn);
        }

        function setPanelVisible(v) {
            localStorage.setItem(CONFIG.panelKey, v ? '1' : '0');
            applyPanelVisibility();
        }
        function applyPanelVisibility() {
            const visible = localStorage.getItem(CONFIG.panelKey) === '1';
            panel.style.display = visible ? '' : 'none';
            toggleBtn.style.display = visible ? 'none' : '';
        }

        // ---- run control ----
        function start() {
            setRunning(true);
            log('Started.');
            ensureQueued();
            startMonitor();
        }

        function ensureQueued() {
            if (!checksDone) {
                log('Scanning downloads… will queue missing items when done.');
                return;
            }
            queueMissingItems();
        }

        function queueMissingItems() {
            const history = readHistory();
            const tasks = readTasks();
            const pageNo = getCurrentPageNo();
            let added = 0;
            getMissingItems().forEach(({ code, url }) => {
                if (history.processed[code]) return;
                if (!tasks[code]) {
                    tasks[code] = { code, url, page: pageNo, status: 'queued', createdAt: Date.now(), updatedAt: Date.now() };
                    added++;
                } else if (tasks[code].status === 'failed') {
                    tasks[code].status = 'queued';
                    tasks[code].updatedAt = Date.now();
                    added++;
                }
            });
            if (added) {
                writeTasks(tasks);
                log(`Queued ${added} missing item(s).`);
                paintItems();
            }
        }

        function stop() {
            setRunning(false);
            if (timer) clearInterval(timer);
            timer = null;
            log('Stopped. Already opened detail tabs may finish one item each.');
            updateStatus();
        }

        function retryPage() {
            const tasks = readTasks();
            const history = readHistory();
            getMissingItems().forEach(({ code }) => {
                delete tasks[code]; delete history.processed[code]; delete history.failed[code];
            });
            writeTasks(tasks); writeHistory(history);
            paintItems(); updateStatus();
            log('Cleared this page from queue/history.');
        }

        function clearHistory() {
            writeTasks({}); writeHistory({ processed: {}, failed: {} });
            setRunning(false);
            paintItems(); updateStatus();
            log('Cleared all downloader queue/history.');
        }

        // ---- monitor ----
        function startMonitor() {
            if (timer) clearInterval(timer);
            monitor();
            timer = setInterval(monitor, CONFIG.pollMs);
        }

        function monitor() {
            if (!isRunning()) { updateStatus(); return; }
            if (!checksDone) { updateStatus(); return; }

            queueMissingItems();

            const now = Date.now();
            const tasks = readTasks();
            const history = readHistory();
            let changed = false;

            Object.values(tasks).forEach((task) => {
                if (task.status === 'opening' && now - task.updatedAt > CONFIG.detailTimeoutMs) {
                    task.status = 'queued'; task.updatedAt = now; task.error = 'detail tab timed out'; changed = true;
                }
                if (task.status === 'done') {
                    history.processed[task.code] = { name: task.name, href: task.href, at: task.updatedAt };
                    delete history.failed[task.code]; delete tasks[task.code];
                    log(`${task.code}: ${task.name || 'done'}`); changed = true;
                }
                if (task.status === 'failed') {
                    history.failed[task.code] = { error: task.error || 'failed', at: task.updatedAt };
                    delete tasks[task.code];
                    log(`${task.code}: FAILED - ${history.failed[task.code].error}`); changed = true;
                }
            });

            const activeCount = Object.values(tasks).filter((t) => t.status === 'opening').length;
            const slots = Math.max(0, CONFIG.maxOpenDetailTabs - activeCount);
            const batchReady = now >= nextBatchAllowedAt;
            const canOpenNext = slots > 0 && batchReady && now - lastDetailTabOpenAt >= CONFIG.detailTabOpenGapMs;
            const nextTask = canOpenNext
                ? Object.values(tasks).filter((t) => t.status === 'queued').sort((a, b) => a.createdAt - b.createdAt)[0]
                : null;

            if (nextTask) {
                nextTask.status = 'opening'; nextTask.updatedAt = now; changed = true;
                writeTasks(tasks);
                openDetailTab(nextTask);
                lastDetailTabOpenAt = Date.now();
                openedInCurrentBatch++;
                if (openedInCurrentBatch >= CONFIG.maxOpenDetailTabs) {
                    openedInCurrentBatch = 0;
                    nextBatchAllowedAt = Date.now() + CONFIG.batchWaitMs;
                    log(`Batch limit reached. Waiting ${Math.round(CONFIG.batchWaitMs / 1000)}s before more tabs.`);
                }
            }

            if (changed) { writeTasks(tasks); writeHistory(history); paintItems(); }
            updateStatus();

            const pageCodes = new Set(getMissingItems().map((e) => e.code));
            const pageHasPending = Object.values(tasks).some((t) => pageCodes.has(t.code));
            const latest = readHistory();
            const pageUnresolved = getMissingItems().some(({ code }) => !latest.processed[code] && !latest.failed[code]);
            if (!pageHasPending && !pageUnresolved) {
                setRunning(false);
                if (timer) clearInterval(timer);
                timer = null;
                log('Page complete. Moving to next page soon.');
                setTimeout(goNextPage, CONFIG.autoNextPageDelayMs);
            }
        }

        function openDetailTab(task) {
            try {
                GM_openInTab(task.url, { active: false, insert: false, setParent: true });
            } catch (_) {
                window.open(task.url, '_blank', 'noopener');
            }
        }

        function paintItems() {
            const tasks = readTasks();
            const history = readHistory();
            getMissingItems().forEach(({ item, code }) => {
                item.classList.toggle('jb-dm-queued', !!tasks[code]);
                item.classList.toggle('jb-dm-done', !!history.processed[code]);
                item.classList.toggle('jb-dm-failed', !!history.failed[code]);
            });
        }

        function updateStatus() {
            const statusEl = panel.querySelector('#jb-dm-status');
            if (!statusEl) return;
            if (!checksDone) {
                statusEl.textContent = `Scanning ${checkStats.done}/${checkStats.total}… have ${checkStats.have}, missing ${checkStats.missing}` +
                    (checkStats.errors ? `, errors ${checkStats.errors} (check Everything HTTP server/port)` : '');
                return;
            }
            const tasks = Object.values(readTasks());
            const history = readHistory();
            const missing = getMissingItems();
            const queued = tasks.filter((t) => t.status === 'queued').length;
            const opening = tasks.filter((t) => t.status === 'opening').length;
            const done = missing.filter(({ code }) => history.processed[code]).length;
            const failed = missing.filter(({ code }) => history.failed[code]).length;
            const pending = missing.length - done;
            statusEl.textContent = `Page ${getCurrentPageNo()}: have ${checkStats.have}, missing ${missing.length} ` +
                `(${done} done, ${failed} failed, ${pending} pending). Queue ${queued}, open ${opening}.` +
                (checkStats.errors ? ` | scan errors ${checkStats.errors}` : '');
        }

        function log(message) {
            const stamp = new Date().toLocaleTimeString();
            logEl.textContent = `[${stamp}] ${message}\n` + logEl.textContent;
        }

        function goNextPage() {
            const next = document.querySelector('a#next[href]');
            if (next) location.href = next.href;
            else log('No next page link found. Finished.');
        }
    }

    // ======================================================================
    //  DETAIL PAGE: pick & open magnet
    // ======================================================================
    async function detailMain(code) {
        const task = readTasks()[code];
        if (!task || task.status !== 'opening') return;

        addDetailBadge('Working');
        try {
            const magnets = await waitForMagnets();
            const chosen = chooseMagnet(magnets, code);
            if (!chosen) throw new Error('no magnet rows found');

            const tasks = readTasks();
            if (tasks[code]) {
                tasks[code].status = 'done';
                tasks[code].name = chosen.name;
                tasks[code].href = chosen.href;
                tasks[code].updatedAt = Date.now();
                writeTasks(tasks);
            }

            addDetailBadge(`Opening: ${chosen.name}`);
            openMagnet(chosen);
            setTimeout(() => window.close(), CONFIG.magnetOpenDelayMs);
        } catch (err) {
            const tasks = readTasks();
            if (tasks[code]) {
                tasks[code].status = 'failed';
                tasks[code].error = err && err.message ? err.message : String(err);
                tasks[code].updatedAt = Date.now();
                writeTasks(tasks);
            }
            addDetailBadge(`Failed: ${err && err.message ? err.message : err}`);
        }
    }

    function waitForMagnets() {
        const started = Date.now();
        return new Promise((resolve, reject) => {
            const tick = () => {
                const magnets = parseMagnetRows(document);
                if (magnets.length) return resolve(magnets);
                const loading = document.querySelector('#movie-loading');
                const loadingHidden = loading && getComputedStyle(loading).display === 'none';
                if (loadingHidden && document.querySelector('#magnet-table')) return reject(new Error('magnet table empty'));
                if (Date.now() - started > CONFIG.detailTimeoutMs) return reject(new Error('magnet table timeout'));
                setTimeout(tick, 500);
            };
            tick();
        });
    }

    function parseMagnetRows(root) {
        return Array.from(root.querySelectorAll('#magnet-table tr')).map((tr) => {
            const a = tr.querySelector('a[href^="magnet:"]');
            if (!a) return null;
            const cells = tr.querySelectorAll('td');
            return {
                element: a,
                href: a.href,
                name: normalizeSpace(a.textContent || (cells[0] && cells[0].textContent) || ''),
                size: normalizeSpace((cells[1] && cells[1].textContent) || ''),
                date: normalizeSpace((cells[2] && cells[2].textContent) || ''),
            };
        }).filter(Boolean);
    }

    function chooseMagnet(candidates, code) {
        if (!candidates.length) return null;
        for (const word of CONFIG.priorityWords) {
            const hit = candidates.find((c) => c.name.toLowerCase().includes(word));
            if (hit) return hit;
        }
        const codeStem = (code.match(/\d+/) || [code])[0].toLowerCase();
        return candidates.slice().sort((a, b) => simplicityScore(a.name, codeStem) - simplicityScore(b.name, codeStem))[0];
    }

    function simplicityScore(name, codeStem) {
        const lower = name.toLowerCase();
        let score = lower.length;
        if (lower.includes(codeStem)) score -= 40;
        if (/\bhd\b/i.test(name)) score -= 8;
        score += (lower.match(/[\[\](){}]/g) || []).length * 8;
        score += (lower.match(/fhd|1080|2k|4k|uncensored|subtitle/g) || []).length * 10;
        score += (lower.match(/[^\w\s.-]/g) || []).length;
        return score;
    }

    function openMagnet(magnet) {
        try {
            if (magnet.element) { magnet.element.click(); return; }
            const a = document.createElement('a');
            a.href = magnet.href; a.style.display = 'none';
            document.body.appendChild(a); a.click(); a.remove();
        } catch (_) {
            location.href = magnet.href;
        }
    }

    function addDetailBadge(text) {
        let badge = document.querySelector('#jb-dm-detail-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.id = 'jb-dm-detail-badge';
            badge.style.cssText = [
                'position:fixed', 'right:12px', 'bottom:12px', 'z-index:99999',
                'max-width:360px', 'padding:8px 10px', 'border-radius:6px',
                'background:rgba(20,20,20,.94)', 'color:#fff',
                'font:13px/1.45 Arial,sans-serif', 'box-shadow:0 8px 30px rgba(0,0,0,.35)',
            ].join(';');
            document.body.appendChild(badge);
        }
        badge.textContent = `JavBus downloader: ${text}`;
    }

    function normalizeSpace(text) { return text.replace(/\s+/g, ' ').trim(); }
})();
