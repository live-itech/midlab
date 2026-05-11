/* ============================================================
   MidLab Web Console — app.js
   Fetch wrapper, SSE handler, auto-refresh, modal, toast
   ============================================================ */

const App = {
  // -- State --
  autoRefreshTimer: null,
  sseConnection: null,
  currentPage: '',

  // ============================================================
  // Initialization
  // ============================================================
  init() {
    this.initTheme();
    this.initSidebar();
    this.initModals();
    this.checkConnection();
    // Cek koneksi setiap 30 detik
    setInterval(() => this.checkConnection(), 30000);
  },

  // ============================================================
  // Theme Toggle (dark/light)
  // ============================================================
  initTheme() {
    const saved = localStorage.getItem('midlab-theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
    this.updateThemeIcon(saved);

    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('midlab-theme', next);
        this.updateThemeIcon(next);
      });
    }
  },

  updateThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    if (theme === 'dark') {
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
    } else {
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
    }
  },

  // ============================================================
  // Sidebar
  // ============================================================
  initSidebar() {
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.getElementById('sidebar');
    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
      });
      // Tutup sidebar saat klik di luar (mobile)
      document.addEventListener('click', (e) => {
        if (window.innerWidth <= 768 && sidebar.classList.contains('open')) {
          if (!sidebar.contains(e.target) && e.target !== toggle && !toggle.contains(e.target)) {
            sidebar.classList.remove('open');
          }
        }
      });
    }
  },

  // ============================================================
  // Connection Status
  // ============================================================
  async checkConnection() {
    const dot = document.getElementById('status-dot');
    const label = document.getElementById('status-label');
    if (!dot || !label) return;
    try {
      const resp = await fetch('/api/dashboard', { signal: AbortSignal.timeout(5000) });
      if (resp.ok) {
        dot.className = 'status-dot';
        label.textContent = 'Connected';
      } else {
        dot.className = 'status-dot offline';
        label.textContent = 'Error';
      }
    } catch {
      dot.className = 'status-dot offline';
      label.textContent = 'Offline';
    }
  },

  // ============================================================
  // API Fetch Wrapper
  // ============================================================
  async api(path, options = {}) {
    const defaults = {
      headers: { 'Content-Type': 'application/json' },
    };
    const config = { ...defaults, ...options };
    if (options.body && typeof options.body === 'object') {
      config.body = JSON.stringify(options.body);
    }
    try {
      const resp = await fetch(path, config);
      const data = await resp.json();
      if (!resp.ok) {
        const msg = data.detail || data.message || `Error ${resp.status}`;
        throw new Error(msg);
      }
      return data;
    } catch (err) {
      if (err.name === 'SyntaxError') {
        throw new Error('Invalid response from server');
      }
      throw err;
    }
  },

  // ============================================================
  // Toast Notifications
  // ============================================================
  toast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.className = 'toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icons = {
      success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
      error:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>',
      warning: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
      info:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
    };

    toast.innerHTML = `${icons[type] || icons.info}<span>${this.escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('removing');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  },

  // ============================================================
  // Modal
  // ============================================================
  initModals() {
    // Tutup modal saat klik overlay
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) this.closeModal(overlay.id);
      });
    });
    // Tutup modal saat klik tombol close
    document.querySelectorAll('.modal-close').forEach(btn => {
      btn.addEventListener('click', () => {
        const overlay = btn.closest('.modal-overlay');
        if (overlay) this.closeModal(overlay.id);
      });
    });
    // ESC key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const active = document.querySelector('.modal-overlay.active');
        if (active) this.closeModal(active.id);
      }
    });
  },

  openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
  },

  closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  },

  // ============================================================
  // Confirm Dialog
  // ============================================================
  confirm(title, message) {
    return new Promise((resolve) => {
      // Buat modal confirm on-the-fly
      let overlay = document.getElementById('confirm-dialog');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'confirm-dialog';
        overlay.className = 'modal-overlay';
        overlay.innerHTML = `
          <div class="modal" style="max-width:400px">
            <div class="modal-header">
              <h3 id="confirm-title"></h3>
              <button class="modal-close" onclick="App.closeModal('confirm-dialog')">&times;</button>
            </div>
            <div class="modal-body confirm-body">
              <p id="confirm-message"></p>
            </div>
            <div class="modal-footer">
              <button class="btn btn-ghost" id="confirm-cancel">Batal</button>
              <button class="btn btn-danger" id="confirm-ok">Konfirmasi</button>
            </div>
          </div>`;
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
          if (e.target === overlay) {
            this.closeModal('confirm-dialog');
            resolve(false);
          }
        });
      }
      document.getElementById('confirm-title').textContent = title;
      document.getElementById('confirm-message').textContent = message;
      this.openModal('confirm-dialog');

      const okBtn = document.getElementById('confirm-ok');
      const cancelBtn = document.getElementById('confirm-cancel');

      const cleanup = () => {
        okBtn.replaceWith(okBtn.cloneNode(true));
        cancelBtn.replaceWith(cancelBtn.cloneNode(true));
        this.closeModal('confirm-dialog');
      };

      document.getElementById('confirm-ok').addEventListener('click', () => { cleanup(); resolve(true); });
      document.getElementById('confirm-cancel').addEventListener('click', () => { cleanup(); resolve(false); });
    });
  },

  // ============================================================
  // SSE Log Streaming
  // ============================================================
  startSSE(service, onMessage) {
    this.stopSSE();
    const url = `/api/logs/${encodeURIComponent(service)}/stream`;
    this.sseConnection = new EventSource(url);
    this.sseConnection.onmessage = (e) => {
      if (onMessage) onMessage(e.data);
    };
    this.sseConnection.onerror = () => {
      // EventSource akan otomatis reconnect
    };
  },

  stopSSE() {
    if (this.sseConnection) {
      this.sseConnection.close();
      this.sseConnection = null;
    }
  },

  // ============================================================
  // Auto-Refresh
  // ============================================================
  startAutoRefresh(callback, intervalMs = 10000) {
    this.stopAutoRefresh();
    callback(); // Langsung panggil pertama kali
    this.autoRefreshTimer = setInterval(callback, intervalMs);
  },

  stopAutoRefresh() {
    if (this.autoRefreshTimer) {
      clearInterval(this.autoRefreshTimer);
      this.autoRefreshTimer = null;
    }
  },

  // ============================================================
  // Utility Helpers
  // ============================================================
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  formatUptime(seconds) {
    if (seconds == null) return '-';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  },

  formatDatetime(iso) {
    if (!iso) return '-';
    try {
      const d = new Date(iso);
      return d.toLocaleString('id-ID', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
    } catch {
      return iso;
    }
  },

  formatTimeAgo(iso) {
    if (!iso) return '-';
    try {
      const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
      if (diff < 60) return `${diff}s lalu`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m lalu`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h lalu`;
      return `${Math.floor(diff / 86400)}d lalu`;
    } catch {
      return iso;
    }
  },

  // Tampilkan JSON di modal
  showJsonModal(title, data) {
    let overlay = document.getElementById('json-modal');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'json-modal';
      overlay.className = 'modal-overlay';
      overlay.innerHTML = `
        <div class="modal modal-wide">
          <div class="modal-header">
            <h3 id="json-modal-title"></h3>
            <button class="modal-close" onclick="App.closeModal('json-modal')">&times;</button>
          </div>
          <div class="modal-body">
            <pre class="json-viewer" id="json-modal-content"></pre>
          </div>
          <div class="modal-footer">
            <button class="btn btn-ghost" onclick="App.copyJson()">Copy</button>
            <button class="btn btn-ghost" onclick="App.closeModal('json-modal')">Tutup</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) App.closeModal('json-modal');
      });
    }
    document.getElementById('json-modal-title').textContent = title;
    document.getElementById('json-modal-content').textContent =
      typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    this.openModal('json-modal');
  },

  copyJson() {
    const el = document.getElementById('json-modal-content');
    if (el) {
      navigator.clipboard.writeText(el.textContent).then(
        () => this.toast('JSON disalin ke clipboard', 'success'),
        () => this.toast('Gagal menyalin', 'error')
      );
    }
  },

  // Build query string dari object
  buildQuery(params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== '') {
        qs.append(k, v);
      }
    }
    const str = qs.toString();
    return str ? `?${str}` : '';
  },
};

// Init saat DOM ready
document.addEventListener('DOMContentLoaded', () => App.init());
