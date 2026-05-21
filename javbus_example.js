// ==UserScript==
// @name         Javbus 番号预览悬浮窗（延迟显示）
// @namespace    http://tampermonkey.net/
// @version      1.2
// @description  鼠标悬停两秒后预览 Javbus 页面，悬浮窗固定位置
// @match        https://sukebei.nyaa.si/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    const previewBox = document.createElement('div');
    previewBox.style.position = 'fixed';
    previewBox.style.bottom = '20px';
    previewBox.style.right = '20px';
    previewBox.style.width = '800px';
    previewBox.style.height = '800px';
    previewBox.style.border = '2px solid #888';
    previewBox.style.backgroundColor = '#fff';
    previewBox.style.zIndex = '9999';
    previewBox.style.display = 'none';
    previewBox.style.boxShadow = '0 0 10px rgba(0,0,0,0.3)';
    previewBox.style.resize = 'both';
    previewBox.style.overflow = 'hidden';

    const iframe = document.createElement('iframe');
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.border = 'none';

    previewBox.appendChild(iframe);
    document.body.appendChild(previewBox);

    const codeRegex = /\b([A-Z]{2,5}-?\d{2,5})\b/i;
    let hoverTimer = null;

    document.addEventListener('mouseover', function (e) {
        const target = e.target;
        if (target.tagName === 'A' && target.textContent) {
            const match = target.textContent.match(codeRegex);
            if (match) {
                const code = match[1].toUpperCase().replace('–', '-');

                // 设置延迟显示
                hoverTimer = setTimeout(() => {
                    iframe.src = `https://www.javbus.com/${code}`;
                    previewBox.style.display = 'block';
                }, 1000); // 2秒延迟
            }
        }
    });

    document.addEventListener('mouseout', function (e) {
        if (e.target.tagName === 'A') {
            clearTimeout(hoverTimer);
            hoverTimer = null;
            previewBox.style.display = 'none';
            iframe.src = '';
        }
    });
})();
