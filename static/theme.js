/* Theme color picker.
 *
 * The accent color (--accent) and its darker variant (--accent-dark) drive
 * every orange-ish color in the UI. The user's choice is persisted in
 * localStorage and re-applied before the page renders, so it survives
 * across sessions and never flashes the default color.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'hn-viewer-theme-color';
    var DEFAULT_COLOR = '#ff6600';
    var DARK_FACTOR = 0.91;  // #ff6600 -> #e85d00 (each channel * 0.91)

    var HEX_RE = /^#[0-9a-f]{6}$/i;

    function darken(hex, factor) {
        var channel = function (start) {
            var value = Math.round(parseInt(hex.slice(start, start + 2), 16) * factor);
            return value.toString(16).padStart(2, '0');
        };
        return '#' + channel(1) + channel(3) + channel(5);
    }

    function applyTheme(color) {
        var root = document.documentElement;
        root.style.setProperty('--accent', color);
        root.style.setProperty('--accent-dark', darken(color, DARK_FACTOR));
    }

    function storedColor() {
        try {
            var value = localStorage.getItem(STORAGE_KEY);
        } catch (err) {
            return null;  // storage unavailable (private mode, etc.)
        }
        return HEX_RE.test(value || '') ? value.toLowerCase() : null;
    }

    // Apply the saved theme immediately so the page never flashes the
    // default color while loading.
    var saved = storedColor();
    if (saved) applyTheme(saved);

    document.addEventListener('DOMContentLoaded', function () {
        var picker = document.getElementById('theme-color-picker');
        var resetBtn = document.getElementById('theme-color-reset');
        if (!picker) return;

        picker.value = saved || DEFAULT_COLOR;

        // "input" fires while the native picker is open: preview live.
        picker.addEventListener('input', function () {
            applyTheme(picker.value);
        });

        // "change" fires when the picker is closed: persist the choice.
        picker.addEventListener('change', function () {
            try {
                localStorage.setItem(STORAGE_KEY, picker.value);
            } catch (err) { /* keep the preview even if storage fails */ }
        });

        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                try {
                    localStorage.removeItem(STORAGE_KEY);
                } catch (err) { /* nothing stored to reset */ }
                picker.value = DEFAULT_COLOR;
                applyTheme(DEFAULT_COLOR);
            });
        }
    });
})();
