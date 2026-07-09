/**
 * ARCHIE Chat Overlay Widget
 * Self-contained floating chat bubble for admin users.
 * Uses .ac-overlay-* classes from archie-chat.css
 */
class ArchieOverlay {
    constructor() {
        this.conversationId = null;
        this.isExpanded = false;
        this.messages = [];
        this.isLoading = false;
        this.agentTarget = null;
        this.agentLabel = 'A.R.C.H.I.E.';
        this.agentIcon = '\u{1F4AC}';
        this.accentColor = '#00e5ff';
        this.moduleKey = null; // set via configure() to enable shared persistent channel
        this.expandUrl = null; // override the ↗ expand destination
    }

    init() {
        this._createDOM();
        this._bindEvents();
        this._restoreState();
    }

    _createDOM() {
        this.bubble = document.createElement('button');
        this.bubble.className = 'ac-overlay-bubble';
        this.bubble.textContent = this.agentIcon;
        this.bubble.title = 'Chat with ' + this.agentLabel;
        document.body.appendChild(this.bubble);

        this.window = document.createElement('div');
        this.window.className = 'ac-overlay-window ac-overlay-hidden';

        const header = document.createElement('div');
        header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid #333;background:#111;flex-shrink:0;';

        const headerLeft = document.createElement('div');
        headerLeft.style.cssText = 'display:flex;align-items:center;gap:8px;';

        this.portraitEl = document.createElement('img');
        this.portraitEl.style.cssText = 'width:28px;height:28px;border-radius:50%;object-fit:cover;border:1px solid ' + this.accentColor + '44;display:none;';
        headerLeft.appendChild(this.portraitEl);

        const statusDot = document.createElement('span');
        statusDot.style.cssText = 'width:8px;height:8px;border-radius:50%;background:' + this.accentColor + ';display:inline-block;';
        this._statusDot = statusDot;

        const label = document.createElement('span');
        label.style.cssText = "font-family:'Orbitron',sans-serif;font-size:0.85rem;color:" + this.accentColor + ";font-weight:700;letter-spacing:1px;";
        label.textContent = this.agentLabel;
        this._headerLabel = label;

        headerLeft.appendChild(statusDot);
        headerLeft.appendChild(label);

        const headerRight = document.createElement('div');
        headerRight.style.cssText = 'display:flex;gap:6px;';

        this.expandBtn = document.createElement('button');
        this.expandBtn.className = 'ac-overlay-expand-btn';
        this.expandBtn.title = 'Open full chat';
        this.expandBtn.style.cssText = 'background:none;border:1px solid #444;color:#aaa;border-radius:4px;cursor:pointer;padding:2px 8px;font-size:0.75rem;';
        this.expandBtn.textContent = '↗';

        this.minBtn = document.createElement('button');
        this.minBtn.className = 'ac-overlay-min-btn';
        this.minBtn.title = 'Minimize';
        this.minBtn.style.cssText = 'background:none;border:1px solid #444;color:#aaa;border-radius:4px;cursor:pointer;padding:2px 8px;font-size:0.75rem;';
        this.minBtn.textContent = '−';

        headerRight.appendChild(this.expandBtn);
        headerRight.appendChild(this.minBtn);
        header.appendChild(headerLeft);
        header.appendChild(headerRight);

        this.messagesEl = document.createElement('div');
        this.messagesEl.className = 'ac-overlay-messages';
        this.messagesEl.style.cssText = 'flex:1;overflow-y:auto;padding:10px 14px;display:flex;flex-direction:column;gap:8px;';

        const inputRow = document.createElement('div');
        inputRow.style.cssText = 'display:flex;gap:6px;padding:10px 14px;border-top:1px solid #333;background:#111;flex-shrink:0;';

        this.inputEl = document.createElement('input');
        this.inputEl.className = 'ac-overlay-input';
        this.inputEl.type = 'text';
        this.inputEl.placeholder = 'Message A.R.C.H.I.E. ...';
        this.inputEl.style.cssText = 'flex:1;background:#1a1a1e;border:1px solid #333;border-radius:6px;padding:8px 10px;color:#e0e0e0;font-size:0.85rem;outline:none;';

        this.sendBtn = document.createElement('button');
        this.sendBtn.className = 'ac-overlay-send-btn';
        this.sendBtn.style.cssText = 'background:' + this.accentColor + ';border:none;border-radius:6px;padding:8px 14px;color:#000;font-weight:700;cursor:pointer;font-size:0.85rem;';
        this.sendBtn.textContent = 'Send';

        inputRow.appendChild(this.inputEl);
        inputRow.appendChild(this.sendBtn);

        this.window.appendChild(header);
        this.window.appendChild(this.messagesEl);
        this.window.appendChild(inputRow);
        document.body.appendChild(this.window);
    }

    _bindEvents() {
        this.bubble.addEventListener('click', () => this.toggle());
        this.minBtn.addEventListener('click', () => this.collapse());
        this.expandBtn.addEventListener('click', () => this.expand());
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    configure({ agentTarget, label, icon, portraitUrl, accentColor, moduleKey, expandUrl } = {}) {
        if (agentTarget) this.agentTarget = agentTarget;
        if (label) {
            this.agentLabel = label;
            if (this._headerLabel) this._headerLabel.textContent = label;
            if (this.bubble) this.bubble.title = 'Chat with ' + label;
            if (this.inputEl) this.inputEl.placeholder = 'Message ' + label + ' ...';
        }
        if (icon) {
            this.agentIcon = icon;
            if (this.bubble && !portraitUrl) this.bubble.textContent = icon;
        }
        if (portraitUrl && this.portraitEl) {
            this.portraitEl.src = portraitUrl;
            this.portraitEl.style.display = 'block';
            if (this._statusDot) this._statusDot.style.display = 'none';
            if (this.bubble) {
                this.bubble.textContent = '';
                const img = document.createElement('img');
                img.src = portraitUrl;
                img.style.cssText = 'width:28px;height:28px;border-radius:50%;object-fit:cover;border:1px solid ' + this.accentColor + '44;';
                this.bubble.appendChild(img);
            }
        }
        if (accentColor) {
            this.accentColor = accentColor;
            if (this._statusDot) this._statusDot.style.background = accentColor;
            if (this._headerLabel) this._headerLabel.style.color = accentColor;
            if (this.sendBtn) this.sendBtn.style.background = accentColor;
            if (this.portraitEl) this.portraitEl.style.borderColor = accentColor + '44';
            // Override FAB bubble background + glow (CSS class is hardcoded cyan)
            if (this.bubble) {
                this.bubble.style.background = accentColor;
                this.bubble.style.boxShadow = '0 4px 20px ' + accentColor + '59, 0 0 30px ' + accentColor + '26';
            }
            // Also update portrait clone border inside FAB bubble
            const bubbleImg = this.bubble && this.bubble.querySelector('img');
            if (bubbleImg) bubbleImg.style.borderColor = accentColor + '44';
        }
        if (expandUrl) this.expandUrl = expandUrl;
        // Shared module channel — load existing thread on configure
        if (moduleKey) {
            this.moduleKey = moduleKey;
            this._loadChannel();
        } else {
            this.conversationId = null;
        }
    }

    _restoreState() {
        const state = localStorage.getItem('archie-overlay-state');
        if (state === 'expanded') {
            this._showWindow();
        }
    }

    toggle() {
        if (this.isExpanded) {
            this.collapse();
        } else {
            this._showWindow();
            localStorage.setItem('archie-overlay-state', 'expanded');
        }
    }

    collapse() {
        this.isExpanded = false;
        this.window.classList.add('ac-overlay-hidden');
        this.bubble.style.display = 'flex';
        localStorage.setItem('archie-overlay-state', 'collapsed');
    }

    expand() {
        if (this.expandUrl) {
            window.open(this.expandUrl, '_blank');
            return;
        }
        let url = '/tools/archie-chat';
        if (this.conversationId) {
            url += '?conversation=' + encodeURIComponent(this.conversationId);
        }
        window.open(url, '_blank');
    }

    _showWindow() {
        this.isExpanded = true;
        this.window.classList.remove('ac-overlay-hidden');
        this.bubble.style.display = 'none';
        this.inputEl.focus();
    }

    async _loadChannel() {
        if (!this.moduleKey) return;
        try {
            const agentParam = encodeURIComponent(this.agentTarget || 'W.A.R.D.E.N.');
            const modParam = encodeURIComponent(this.moduleKey);
            const r = await fetch(
                '/tools/archie-chat/api/v1/chat/channel?module=' + modParam + '&agent=' + agentParam,
                { credentials: 'same-origin' }
            );
            const d = await r.json();
            if (d.success && d.conversation_uuid) {
                this.conversationId = d.conversation_uuid;
                await this._loadHistory();
            }
        } catch (e) {
            // Non-fatal — overlay still works for new conversations
        }
    }

    async _loadHistory() {
        if (!this.conversationId) return;
        try {
            const r = await fetch(
                '/tools/archie-chat/api/v1/chat/conversations/' + this.conversationId + '/messages?limit=50&include_thinking=false',
                { credentials: 'same-origin' }
            );
            const d = await r.json();
            if (!d.success || !d.messages) return;
            // Clear current messages safely (no innerHTML)
            while (this.messagesEl.firstChild) {
                this.messagesEl.removeChild(this.messagesEl.firstChild);
            }
            d.messages.forEach((m) => {
                const role = m.role || (m.direction === 'inbound' ? 'user' : 'assistant');
                if (!m.content) return;
                const meta = (m.metadata && typeof m.metadata === 'object') ? m.metadata : {};
                let senderName, senderPortrait;
                if (role === 'user') {
                    senderName = meta.sender_display_name || meta.sender_username || m.sender || 'Admin';
                    senderPortrait = meta.sender_portrait || null;
                } else {
                    senderName = meta.agent_used || this.agentLabel;
                    senderPortrait = null;
                }
                this.renderMessage(role, m.content, { name: senderName, portrait: senderPortrait });
            });
        } catch (e) {
            // Non-fatal
        }
    }

    async sendMessage() {
        const text = this.inputEl.value.trim();
        if (!text || this.isLoading) return;

        this.inputEl.value = '';
        // Sender name resolved from last response; pre-populate with 'You' until first reply
        const selfName = this._selfDisplayName || 'You';
        const selfPortrait = this._selfPortrait || null;
        this.renderMessage('user', text, { name: selfName, portrait: selfPortrait });
        this.isLoading = true;
        this.sendBtn.disabled = true;

        const typingEl = document.createElement('div');
        typingEl.style.cssText = 'color:#666;font-size:0.8rem;font-style:italic;padding:4px 0;';
        typingEl.textContent = this.agentLabel + ' is thinking...';
        this.messagesEl.appendChild(typingEl);
        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;

        try {
            const body = { message: text, include_thinking: false };
            if (this.conversationId) {
                body.conversation_id = this.conversationId;
            }
            if (this.agentTarget) {
                body.agent_target = this.agentTarget;
            }

            const resp = await fetch('/tools/archie-chat/api/v1/chat/message', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            const data = await resp.json();
            typingEl.remove();

            if (data.success) {
                if (data.conversation_id) {
                    this.conversationId = data.conversation_id;
                }
                // Cache the current user's identity for subsequent message renders
                if (data.sender) {
                    this._selfDisplayName = data.sender.display_name || data.sender.username || null;
                    this._selfPortrait = data.sender.portrait || null;
                }
                const agentSender = {
                    name: (data.agent && (data.agent.display_name || data.agent.name)) || this.agentLabel,
                    portrait: null,
                };
                this.renderMessage('assistant', data.response || data.message || '...', agentSender);
            } else {
                this.renderMessage('system', 'Error: ' + (data.error || 'Unknown error'));
            }
        } catch (err) {
            typingEl.remove();
            this.renderMessage('system', 'Connection error: ' + err.message);
        } finally {
            this.isLoading = false;
            this.sendBtn.disabled = false;
        }
    }

    _parseActionBlock(text) {
        // Accept both ASCII [ACTION]...[/ACTION] and Unicode ⟦ACTION⟧...⟦/ACTION⟧
        const match = text.match(/\[ACTION\]([\s\S]*?)\[\/ACTION\]/) ||
                      text.match(/⟦ACTION⟧([\s\S]*?)⟦\/ACTION⟧/);
        if (!match) return { cleanText: text, action: null };
        let action = null;
        try { action = JSON.parse(match[1].trim()); } catch (e) { /* malformed — ignore */ }
        // Strip ALL action blocks (model may produce its own alongside server-injected one)
        const cleanText = text
            .replace(/\[ACTION\][\s\S]*?\[\/ACTION\]/g, '')
            .replace(/⟦ACTION⟧[\s\S]*?⟦\/ACTION⟧/g, '')
            .trim();
        return { cleanText, action };
    }

    _renderActionConfirm(action, parentEl) {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'margin-top:8px;';
        const btn = document.createElement('button');
        btn.style.cssText = [
            'background:transparent',
            'border:2px solid ' + this.accentColor,
            'color:' + this.accentColor,
            'border-radius:6px',
            'padding:8px 16px',
            'font-weight:700',
            'cursor:pointer',
            'font-size:0.82rem',
            'width:100%',
            'text-align:left',
            'font-family:inherit',
        ].join(';');
        btn.textContent = action.confirm || action.label || 'Confirm';
        btn.addEventListener('click', () => {
            btn.disabled = true;
            btn.style.opacity = '0.6';
            btn.textContent = 'Executing…';
            this._executeAction(action, btn);
        });
        wrap.appendChild(btn);
        parentEl.appendChild(wrap);
    }

    async _executeAction(action, btn) {
        try {
            const parts = (action.route || 'POST /').split(' ');
            const method = parts[0] || 'POST';
            const path = parts[1] || '/';
            const resp = await fetch(path, {
                method,
                credentials: 'same-origin',
                headers: method !== 'GET' ? { 'Content-Type': 'application/json' } : {},
                body: method !== 'GET' ? JSON.stringify(action.payload || {}) : undefined,
            });
            const data = await resp.json().catch(() => ({}));
            if (resp.ok && data.success !== false) {
                btn.textContent = '✓ ' + (data.message || 'Done');
                btn.style.borderColor = '#22c55e';
                btn.style.color = '#22c55e';
                this.inputEl.value = '[Action confirmed: ' + (action.label || action.type) + ']';
                await this.sendMessage();
            } else {
                btn.textContent = '✗ ' + (data.error || 'Request failed');
                btn.style.borderColor = '#ef4444';
                btn.style.color = '#ef4444';
                btn.disabled = false;
                btn.style.opacity = '1';
            }
        } catch (err) {
            btn.textContent = '✗ Error: ' + err.message;
            btn.style.borderColor = '#ef4444';
            btn.style.color = '#ef4444';
            btn.disabled = false;
            btn.style.opacity = '1';
        }
    }

    renderMessage(role, text, sender = null) {
        const isUser = role === 'user';
        const isSystem = role === 'system';

        // Wrapper aligns message and holds sender name above bubble
        const wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;flex-direction:column;'
            + (isUser ? 'align-items:flex-end;' : 'align-items:flex-start;');

        // Sender identity row — name + optional portrait thumbnail
        if (sender && sender.name && !isSystem) {
            const nameRow = document.createElement('div');
            nameRow.style.cssText = 'display:flex;align-items:center;gap:4px;margin-bottom:2px;';

            if (sender.portrait) {
                const thumb = document.createElement('img');
                thumb.src = sender.portrait;
                thumb.style.cssText = 'width:14px;height:14px;border-radius:50%;object-fit:cover;';
                nameRow.appendChild(thumb);
            }

            const nameEl = document.createElement('span');
            nameEl.style.cssText = 'font-size:0.68rem;font-weight:700;letter-spacing:0.5px;color:'
                + (isUser ? this.accentColor : '#888') + ';';
            nameEl.textContent = sender.name;
            nameRow.appendChild(nameEl);
            wrap.appendChild(nameRow);
        }

        const el = document.createElement('div');
        el.style.cssText = 'padding:8px 10px;border-radius:8px;font-size:0.82rem;line-height:1.4;max-width:88%;word-wrap:break-word;'
            + (isUser
                ? 'background:#1a3a4a;color:#e0e0e0;border:1px solid ' + this.accentColor + '33;'
                : isSystem
                    ? 'background:#3a1a1a;color:#ff6b6b;border:1px solid #ff6b6b33;'
                    : 'background:#1a1a2e;color:#e0e0e0;border:1px solid #33335533;');

        let displayText = text;
        let action = null;
        if (role === 'assistant') {
            const parsed = this._parseActionBlock(text);
            displayText = parsed.cleanText;
            action = parsed.action;
        }

        el.textContent = displayText;
        wrap.appendChild(el);
        this.messagesEl.appendChild(wrap);

        if (action) {
            this._renderActionConfirm(action, this.messagesEl);
        }

        this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (!window.location.pathname.startsWith('/tools/archie-chat')) {
        window.archieOverlay = new ArchieOverlay();
        window.archieOverlay.init();
    }
});
