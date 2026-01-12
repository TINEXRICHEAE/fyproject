// Fair Cashier - Main JavaScript

// CSRF Token Helper
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

// Logout Function
async function logout() {
    try {
        const response = await fetch('/logout/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('Logged out successfully', 'success');
            setTimeout(() => {
                window.location.href = '/login/';
            }, 1000);
        } else {
            showNotification('Logout failed', 'error');
        }
    } catch (error) {
        console.error('Logout error:', error);
        showNotification('An error occurred', 'error');
    }
}

// User Registration
async function registerUser(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = '/login/';
            }, 1500);
        } else {
            showNotification(data.error || 'Registration failed', 'error');
        }
    } catch (error) {
        console.error('Registration error:', error);
        showNotification('An error occurred', 'error');
    }
}

// User Login
async function loginUser(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = data.redirect_url || '/';
            }, 1000);
        } else {
            showNotification(data.error || 'Login failed', 'error');
        }
    } catch (error) {
        console.error('Login error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Deposit Funds
async function depositFunds(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    showLoading();
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        hideLoading();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = '/wallet/';
            }, 2000);
        } else {
            showNotification(data.error || 'Deposit failed', 'error');
        }
    } catch (error) {
        hideLoading();
        console.error('Deposit error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Cash Out Funds
async function cashoutFunds(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    if (!confirm('Are you sure you want to cash out?')) {
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        hideLoading();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = '/wallet/';
            }, 2000);
        } else {
            showNotification(data.error || 'Cashout failed', 'error');
        }
    } catch (error) {
        hideLoading();
        console.error('Cashout error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Process Payment
async function processPayment(requestId) {
    if (!confirm('Confirm payment?')) {
        return;
    }
    
    showLoading();
    
    try {
        const response = await fetch(`/payment/${requestId}/process/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            }
        });
        
        const data = await response.json();
        
        hideLoading();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = data.return_url || '/buyer-dashboard/';
            }, 2000);
        } else {
            showNotification(data.error || 'Payment failed', 'error');
        }
    } catch (error) {
        hideLoading();
        console.error('Payment error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Clear Payment Item
async function clearPaymentItem(itemId) {
    if (!confirm('Confirm that you have received the item?')) {
        return;
    }
    
    try {
        const response = await fetch(`/payment-item/${itemId}/clear/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                location.reload();
            }, 1000);
        } else {
            showNotification(data.error || 'Failed to clear payment', 'error');
        }
    } catch (error) {
        console.error('Clear payment error:', error);
        showNotification('An error occurred', 'error');
    }
}

// File Dispute
async function fileDispute(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    if (!confirm('Are you sure you want to file a dispute?')) {
        return;
    }
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = '/buyer-dashboard/';
            }, 2000);
        } else {
            showNotification(data.error || 'Failed to file dispute', 'error');
        }
    } catch (error) {
        console.error('File dispute error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Resolve Dispute (Admin)
async function resolveDispute(disputeId, resolution) {
    const adminNotes = prompt('Enter admin notes:');
    
    if (!adminNotes) {
        return;
    }
    
    try {
        const response = await fetch(`/dispute/${disputeId}/resolve/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            },
            body: JSON.stringify({
                resolution: resolution,
                admin_notes: adminNotes
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification(data.message, 'success');
            setTimeout(() => {
                location.reload();
            }, 1000);
        } else {
            showNotification(data.error || 'Failed to resolve dispute', 'error');
        }
    } catch (error) {
        console.error('Resolve dispute error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Register Platform
async function registerPlatform(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    
    try {
        const response = await fetch(form.action, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrftoken
            },
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('Platform registered successfully', 'success');
            // Show API key in modal
            showApiKeyModal(data.api_key);
        } else {
            showNotification(data.error || 'Failed to register platform', 'error');
        }
    } catch (error) {
        console.error('Platform registration error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Delete Account
async function deleteAccount() {
    const confirmation = prompt('Type "DELETE" to confirm account deletion:');
    
    if (confirmation !== 'DELETE') {
        showNotification('Account deletion cancelled', 'info');
        return;
    }
    
    try {
        const response = await fetch('/delete-account/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrftoken
            }
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            showNotification(data.message, 'success');
            setTimeout(() => {
                window.location.href = '/';
            }, 2000);
        } else {
            showNotification(data.message, 'error');
        }
    } catch (error) {
        console.error('Delete account error:', error);
        showNotification('An error occurred', 'error');
    }
}

// Notification System
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 25px;
        border-radius: 5px;
        color: white;
        font-weight: bold;
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
    `;
    
    switch (type) {
        case 'success':
            notification.style.background = '#28a745';
            break;
        case 'error':
            notification.style.background = '#dc3545';
            break;
        case 'warning':
            notification.style.background = '#ffc107';
            notification.style.color = '#333';
            break;
        default:
            notification.style.background = '#17a2b8';
    }
    
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => {
            notification.remove();
        }, 300);
    }, 3000);
}

// Loading Indicator
function showLoading() {
    const loading = document.createElement('div');
    loading.id = 'loading-indicator';
    loading.innerHTML = '<div class="spinner"></div>';
    loading.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0,0,0,0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
    `;
    document.body.appendChild(loading);
}

function hideLoading() {
    const loading = document.getElementById('loading-indicator');
    if (loading) {
        loading.remove();
    }
}

// Modal Functions
function showModal(title, content) {
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-header">
                <h2>${title}</h2>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                ${content}
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function closeModal() {
    const modal = document.querySelector('.modal.active');
    if (modal) {
        modal.remove();
    }
}

function showApiKeyModal(apiKey) {
    showModal('Platform API Key', `
        <p>Your platform has been registered successfully!</p>
        <p><strong>API Key:</strong></p>
        <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 10px 0;">
            <code style="font-size: 1.1em;">${apiKey}</code>
        </div>
        <p style="color: #dc3545; margin-top: 15px;">
            <strong>⚠️ Important:</strong> Save this API key securely. It will not be shown again.
        </p>
        <button onclick="copyToClipboard('${apiKey}')" class="btn btn-primary">Copy API Key</button>
    `);
}

// Clipboard Function
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('API key copied to clipboard', 'success');
    }).catch(() => {
        showNotification('Failed to copy', 'error');
    });
}

// Format Currency
function formatCurrency(amount, currency = 'UGX') {
    return new Intl.NumberFormat('en-UG', {
        style: 'currency',
        currency: currency
    }).format(amount);
}

// Format Date
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-UG', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('Fair Cashier loaded');
    
    // Add event listeners for forms
    const registerForm = document.querySelector('form[action*="register"]');
    if (registerForm) {
        registerForm.addEventListener('submit', registerUser);
    }
    
    const loginForm = document.querySelector('form[action*="login"]');
    if (loginForm) {
        loginForm.addEventListener('submit', loginUser);
    }
    
    const depositForm = document.querySelector('form[action*="deposit"]');
    if (depositForm) {
        depositForm.addEventListener('submit', depositFunds);
    }
    
    const cashoutForm = document.querySelector('form[action*="cashout"]');
    if (cashoutForm) {
        cashoutForm.addEventListener('submit', cashoutFunds);
    }
    
    const disputeForm = document.querySelector('form[action*="dispute"]');
    if (disputeForm) {
        disputeForm.addEventListener('submit', fileDispute);
    }
    
    const platformForm = document.querySelector('form[action*="platform/register"]');
    if (platformForm) {
        platformForm.addEventListener('submit', registerPlatform);
    }
});