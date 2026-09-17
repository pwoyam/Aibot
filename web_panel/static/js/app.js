// ============================================================================
// Auth-aware fetch — ریدایرکت به login در صورت 401
// ============================================================================
async function authFetch(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        credentials: 'same-origin'
    });
    if (response.status === 401) {
        window.location.href = '/login';
        throw new Error('Unauthorized');
    }
    return response;
}

// API Base URL
const API_BASE = '';

// State
let currentModels = [];
let currentSettings = {};

// ============================================================================
// Toast Notifications
// ============================================================================
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icons = {
        success: 'fa-check-circle',
        error: 'fa-times-circle',
        warning: 'fa-exclamation-circle'
    };
    
    toast.innerHTML = `
        <i class="fas ${icons[type]}"></i>
        <span>${message}</span>
    `;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================================
// Navigation
// ============================================================================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
        // Update active state
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        
        // Show page
        const page = item.dataset.page;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');
        
        // Load page data
        loadPageData(page);
    });
});

function loadPageData(page) {
    switch(page) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'models':
            loadModels();
            break;
        case 'settings':
            loadSettings();
            break;
        case 'users':
            loadUsers();
            break;
        case 'logs':
            loadLogs();
            break;
    }
}

// ============================================================================
// Dashboard
// ============================================================================
async function loadDashboard() {
    try {
        const response = await authFetch(`${API_BASE}/api/dashboard`);
        const data = await response.json();
        
        document.getElementById('stat-total-users').textContent = data.total_users;
        document.getElementById('stat-total-messages').textContent = data.total_messages;
        document.getElementById('stat-active-models').textContent = data.active_models;
        document.getElementById('stat-today-messages').textContent = data.today_messages;
        
        // Load recent activities
        const logsResponse = await authFetch(`${API_BASE}/api/logs?limit=5`);
        const logs = await logsResponse.json();
        
        const container = document.getElementById('recent-activities');
        if (logs.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-inbox"></i>
                    <p>هنوز فعالیتی ثبت نشده است</p>
                </div>
            `;
        } else {
            container.innerHTML = logs.map(log => `
                <div style="padding: 12px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <strong>${log.action}</strong>
                        <p style="font-size: 12px; color: var(--gray); margin-top: 4px;">${log.details || ''}</p>
                    </div>
                    <small style="color: var(--gray);">${new Date(log.timestamp).toLocaleString('fa-IR')}</small>
                </div>
            `).join('');
        }
    } catch (error) {
        console.error('Error loading dashboard:', error);
        showToast('خطا در بارگذاری داشبورد', 'error');
    }
}

// ============================================================================
// Models Management
// ============================================================================
async function loadModels() {
    try {
        const response = await authFetch(`${API_BASE}/api/models`);
        currentModels = await response.json();
        
        const tbody = document.getElementById('models-table-body');
        if (currentModels.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="empty-state">
                        <i class="fas fa-brain"></i>
                        <p>هنوز مدلی اضافه نشده است</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = currentModels.map(model => `
            <tr>
                <td><strong>${model.display_name}</strong></td>
                <td><code>${model.model_name}</code></td>
                <td style="font-size: 12px; direction: ltr; text-align: left;">${model.base_url}</td>
                <td>
                    <span class="badge ${model.is_active ? 'badge-success' : 'badge-danger'}">
                        ${model.is_active ? 'فعال' : 'غیرفعال'}
                    </span>
                </td>
                <td>
                    ${model.is_default ? 
                        '<span class="badge badge-primary">پیش‌فرض ⭐</span>' : 
                        `<button class="btn btn-sm btn-warning" onclick="setDefaultModel(${model.id})">
                            <i class="fas fa-star"></i>
                        </button>
                    <button class="btn btn-sm btn-warning" onclick="toggleModel(${model.id})" title="تغییر وضعیت">
                        <i class="fas fa-power-off"></i>
                    </button>
                    `
                    }
                </td>
                <td>
                    <button class="btn btn-sm btn-primary" onclick="editModel(${model.id})">
                        <i class="fas fa-edit"></i>
                    </button>
                    ${!model.is_default ? 
                        `<button class="btn btn-sm btn-danger" onclick="deleteModel(${model.id})">
                            <i class="fas fa-trash"></i>
                        </button>` : ''
                    }
                </td>
            </tr>
        `).join('');
    } catch (error) {
        console.error('Error loading models:', error);
        showToast('خطا در بارگذاری مدل‌ها', 'error');
    }
}

function openAddModelModal() {
    document.getElementById('model-modal-title').textContent = 'افزودن مدل جدید';
    document.getElementById('model-form').reset();
    document.getElementById('model-id').value = '';
    document.getElementById('model-modal').classList.add('active');
}

function closeModelModal() {
    document.getElementById('model-modal').classList.remove('active');
}

function editModel(id) {
    const model = currentModels.find(m => m.id === id);
    if (!model) return;
    
    document.getElementById('model-modal-title').textContent = 'ویرایش مدل';
    document.getElementById('model-id').value = model.id;
    document.getElementById('model-name').value = model.name;
    document.getElementById('model-display-name').value = model.display_name;
    document.getElementById('model-api-key').value = model.api_key;
    document.getElementById('model-base-url').value = model.base_url;
    document.getElementById('model-model-name').value = model.model_name;
    
    document.getElementById('model-modal').classList.add('active');
}

async function deleteModel(id) {
    if (!confirm('آیا از حذف این مدل مطمئن هستید؟')) return;
    
    try {
        const response = await authFetch(`${API_BASE}/api/models/${id}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
            loadModels();
            loadDashboard();
        } else {
            showToast(data.detail || 'خطا در حذف مدل', 'error');
        }
    } catch (error) {
        showToast('خطا در حذف مدل', 'error');
    }
}

async function setDefaultModel(id) {
    try {
        const response = await authFetch(`${API_BASE}/api/models/${id}/set-default`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
            loadModels();
        } else {
            showToast(data.detail || 'خطا', 'error');
        }
    } catch (error) {
        showToast('خطا در تنظیم مدل پیش‌فرض', 'error');
    }
}

safeAddEventListener('model-form', 'submit', async (e) => {
    e.preventDefault();
    
    const id = document.getElementById('model-id').value;
    const modelData = {
        name: document.getElementById('model-name').value,
        display_name: document.getElementById('model-display-name').value,
        api_key: document.getElementById('model-api-key').value,
        base_url: document.getElementById('model-base-url').value,
        model_name: document.getElementById('model-model-name').value
    };
    
    try {
        const url = id ? `${API_BASE}/api/models/${id}` : `${API_BASE}/api/models`;
        const method = id ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(modelData)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
            closeModelModal();
            loadModels();
            loadDashboard();
        } else {
            showToast(data.detail || 'خطا در ذخیره', 'error');
        }
    } catch (error) {
        showToast('خطا در ذخیره مدل', 'error');
    }
});

// ============================================================================
// Settings Management
// ============================================================================
async function loadSettings() {
    try {
        const response = await authFetch(`${API_BASE}/api/settings`);
        currentSettings = await response.json();
        
        // Fill form
        const fields = ['bot_name', 'welcome_message', 'daily_message_limit'];
        fields.forEach(field => {
            const element = document.getElementById(`setting-${field}`);
            if (element && currentSettings[field]) {
                element.value = currentSettings[field];
            }
        });
        
        // Checkboxes
        const checkboxes = ['enable_chat', 'enable_translate', 'enable_summarize', 'enable_url_summarize', 'enable_pdf_download'];
        checkboxes.forEach(field => {
            const element = document.getElementById(`setting-${field}`);
            if (element) {
                element.checked = currentSettings[field] === '1';
            }
        });
    } catch (error) {
        console.error('Error loading settings:', error);
        showToast('خطا در بارگذاری تنظیمات', 'error');
    }
}

safeAddEventListener('settings-form', 'submit', async (e) => {
    e.preventDefault();
    
    const settings = {
        bot_name: document.getElementById('setting-bot_name').value,
        welcome_message: document.getElementById('setting-welcome_message').value,
        daily_message_limit: document.getElementById('setting-daily_message_limit').value,
        enable_chat: document.getElementById('setting-enable_chat').checked ? '1' : '0',
        enable_translate: document.getElementById('setting-enable_translate').checked ? '1' : '0',
        enable_summarize: document.getElementById('setting-enable_summarize').checked ? '1' : '0',
        enable_url_summarize: document.getElementById('setting-enable_url_summarize').checked ? '1' : '0',
        enable_pdf_download: document.getElementById('setting-enable_pdf_download').checked ? '1' : '0'
    };
    
    try {
        const response = await authFetch(`${API_BASE}/api/settings`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showToast(data.message, 'success');
        } else {
            showToast('خطا در ذخیره تنظیمات', 'error');
        }
    } catch (error) {
        showToast('خطا در ذخیره تنظیمات', 'error');
    }
});

// ============================================================================
// Users Management
// ============================================================================
async function loadUsers() {
    try {
        const response = await authFetch(`${API_BASE}/api/users`);
        const users = await response.json();
        
        const tbody = document.getElementById('users-table-body');
        if (users.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="empty-state">
                        <i class="fas fa-users"></i>
                        <p>هنوز کاربری ثبت نشده است</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        tbody.innerHTML = users.map(user => `
            <tr>
                <td>${user.id}</td>
                <td>${user.username || '-'}</td>
                <td>${user.first_name} ${user.last_name || ''}</td>
                <td>${user.messages_count || 0}</td>
                <td>
                    ${user.is_admin ? '<span class="badge badge-primary">ادمین</span>' : ''}
                    ${user.is_vip ? '<span class="badge badge-success">VIP</span>' : ''}
                    ${user.is_banned ? '<span class="badge badge-danger">مسدود</span>' : '<span class="badge badge-success">فعال</span>'}
                </td>
                <td>
                    <button class="btn btn-sm btn-primary">
                        <i class="fas fa-eye"></i>
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (error) {
        console.error('Error loading users:', error);
        showToast('خطا در بارگذاری کاربران', 'error');
    }
}

// ============================================================================
// Logs
// ============================================================================
async function loadLogs() {
    try {
        const response = await authFetch(`${API_BASE}/api/logs?limit=50`);
        const logs = await response.json();
        
        const container = document.getElementById('logs-container');
        if (logs.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-history"></i>
                    <p>هنوز لاگی ثبت نشده است</p>
                </div>
            `;
            return;
        }
        
        container.innerHTML = logs.map(log => `
            <div style="padding: 12px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <strong>${log.action}</strong>
                    <p style="font-size: 12px; color: var(--gray); margin-top: 4px;">${log.details || ''}</p>
                </div>
                <small style="color: var(--gray);">${new Date(log.timestamp).toLocaleString('fa-IR')}</small>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading logs:', error);
        showToast('خطا در بارگذاری لاگ‌ها', 'error');
    }
}

// ============================================================================
// Initialize
// ============================================================================

// Safe event listener helper
function safeAddEventListener(id, event, handler) {
    const element = document.getElementById(id);
    if (element) {
        element.addEventListener(event, handler);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
});

// Close modal on outside click
safeAddEventListener('model-modal', 'click', (e) => {
    if (e.target.id === 'model-modal') {
        closeModelModal();
    }
});


async function toggleModel(id) {
    try {
        const response = await fetch(`/api/models/${id}/toggle`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
            // بارگذاری مجدد لیست مدل‌ها
            loadModels();
        } else {
            showToast(data.detail || 'خطا', 'error');
        }
    } catch (error) {
        showToast('خطا در تغییر وضعیت مدل', 'error');
    }
}
