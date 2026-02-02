// AuraBot Dashboard - Frontend JavaScript

const API_BASE = '/api';
const POLL_INTERVAL = 2000; // 2 seconds

let pollTimer = null;

// Format time in seconds to human-readable string
function formatTime(seconds) {
    if (seconds <= 0) return '0 seconds';
    
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    const parts = [];
    if (hours > 0) parts.push(`${hours} hour${hours !== 1 ? 's' : ''}`);
    if (minutes > 0) parts.push(`${minutes} minute${minutes !== 1 ? 's' : ''}`);
    if (secs > 0 && hours === 0) parts.push(`${secs} second${secs !== 1 ? 's' : ''}`);
    
    if (parts.length === 0) return 'less than a second';
    if (parts.length === 1) return parts[0];
    if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
    return `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1]}`;
}

// Format duration from seconds to hours:minutes:seconds
function formatDuration(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
    return `${minutes}:${String(secs).padStart(2, '0')}`;
}

// Format date string
function formatDate(dateStr) {
    try {
        const date = new Date(dateStr);
        return date.toLocaleString();
    } catch (e) {
        return dateStr;
    }
}

// Update session display
function updateSession(data) {
    const state = data.session.state;
    const time = data.session.current_time_seconds;
    
    const stateEl = document.getElementById('session-state');
    stateEl.textContent = state;
    stateEl.className = `value state-${state}`;
    
    document.getElementById('session-time').textContent = formatTime(time);
    
    // Update button states
    const btnStart = document.getElementById('btn-start');
    const btnPause = document.getElementById('btn-pause');
    const btnResume = document.getElementById('btn-resume');
    const btnStop = document.getElementById('btn-stop');
    
    btnStart.disabled = state === 'active';
    btnPause.disabled = state !== 'active';
    btnResume.disabled = state !== 'paused';
    btnStop.disabled = state === 'idle';
}

// Update timers display
function updateTimers(data) {
    const timers = data.timers;
    
    document.getElementById('timer-total').textContent = timers.total;
    document.getElementById('timer-user').textContent = timers.user;
    document.getElementById('timer-wellness').textContent = timers.wellness;
    
    const listEl = document.getElementById('timers-list');
    
    if (timers.active_timers.length === 0) {
        listEl.innerHTML = '<p class="empty-state">No active timers</p>';
        return;
    }
    
    listEl.innerHTML = timers.active_timers.map(timer => {
        const isWellness = timer.type === 'wellness';
        return `
            <div class="timer-item ${isWellness ? 'wellness' : ''}">
                <div class="timer-header">
                    <span class="timer-name">${timer.name}</span>
                    <span class="timer-type ${isWellness ? 'wellness' : ''}">${timer.type}</span>
                </div>
                <div class="timer-time">${formatDuration(timer.time_remaining)}</div>
            </div>
        `;
    }).join('');
}

// Update wellness config display
function updateWellnessConfig(data) {
    const config = data.wellness_config || {};
    
    const threshold = config.sitting_threshold_seconds || 0;
    const duration = config.break_duration_seconds || 0;
    const name = config.break_timer_name || '-';
    
    document.getElementById('wellness-threshold').textContent = 
        threshold > 0 ? formatTime(threshold) : '-';
    document.getElementById('wellness-duration').textContent = 
        duration > 0 ? formatTime(duration) : '-';
    document.getElementById('wellness-name').textContent = name;
}

// Update MQTT status
function updateMQTTStatus(connected) {
    const statusEl = document.getElementById('mqtt-status');
    statusEl.textContent = `MQTT: ${connected ? 'Connected' : 'Disconnected'}`;
    statusEl.className = `status-badge ${connected ? 'connected' : 'disconnected'}`;
}

// Update ESP32 status (online when we received sensor data recently)
function updateESP32Status(online) {
    const statusEl = document.getElementById('esp32-status');
    statusEl.textContent = `ESP32: ${online ? 'Online' : 'Offline'}`;
    statusEl.className = `status-badge ${online ? 'connected' : 'disconnected'}`;
}

// Update Camera status (online when vision enabled and receiving frames; disabled when vision off)
function updateCameraStatus(online, enabled) {
    const statusEl = document.getElementById('camera-status');
    if (!enabled) {
        statusEl.textContent = 'Camera: Disabled';
        statusEl.className = 'status-badge unknown';
        return;
    }
    statusEl.textContent = `Camera: ${online ? 'Online' : 'Offline'}`;
    statusEl.className = `status-badge ${online ? 'connected' : 'disconnected'}`;
}

// Fetch and update status
async function fetchStatus() {
    try {
        const response = await fetch(`${API_BASE}/status`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        updateSession(data);
        updateTimers(data);
        updateWellnessConfig(data);
        // Status badges: use explicit checks so missing keys (e.g. old API) still update
        updateMQTTStatus(Boolean(data.mqtt_connected));
        updateESP32Status(Boolean(data.esp32_online));
        updateCameraStatus(Boolean(data.camera_online), data.camera_enabled === true);
    } catch (error) {
        console.error('Error fetching status:', error);
        updateMQTTStatus(false);
        updateESP32Status(false);
        updateCameraStatus(false, false);
    }
}

// Fetch and update session history
async function fetchSessions() {
    try {
        const response = await fetch(`${API_BASE}/sessions?limit=20`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        const sessions = data.sessions || [];
        
        const listEl = document.getElementById('sessions-list');
        
        if (sessions.length === 0) {
            listEl.innerHTML = '<p class="empty-state">No session history</p>';
            return;
        }
        
        // Reverse to show most recent first
        listEl.innerHTML = sessions.slice().reverse().map(session => {
            return `
                <div class="session-item">
                    <div class="session-item-header">
                        <span class="session-date">${formatDate(session.start_datetime)}</span>
                        <span class="session-duration">${session.formatted_duration || formatTime(session.total_seconds)}</span>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Error fetching sessions:', error);
        document.getElementById('sessions-list').innerHTML = 
            '<p class="empty-state">Error loading session history</p>';
    }
}

// Send control command
async function sendControl(cmd) {
    try {
        const response = await fetch(`${API_BASE}/control`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ cmd }),
        });
        
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        if (data.status === 'error') {
            alert(`Error: ${data.error || 'Unknown error'}`);
        } else {
            // Refresh status immediately
            await fetchStatus();
        }
    } catch (error) {
        console.error('Error sending control:', error);
        alert(`Error: ${error.message}`);
    }
}

// Setup control buttons
function setupControls() {
    document.getElementById('btn-start').addEventListener('click', () => {
        sendControl('start_session');
    });
    
    document.getElementById('btn-pause').addEventListener('click', () => {
        sendControl('pause_session');
    });
    
    document.getElementById('btn-resume').addEventListener('click', () => {
        sendControl('resume_session');
    });
    
    document.getElementById('btn-stop').addEventListener('click', () => {
        if (confirm('Stop current session and save it?')) {
            sendControl('stop_session').then(() => {
                fetchSessions(); // Refresh history
            });
        }
    });
}

// Start polling
function startPolling() {
    fetchStatus();
    fetchSessions();
    
    pollTimer = setInterval(() => {
        fetchStatus();
    }, POLL_INTERVAL);
    
    // Refresh sessions less frequently
    setInterval(() => {
        fetchSessions();
    }, POLL_INTERVAL * 5); // Every 10 seconds
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupControls();
    startPolling();
});
