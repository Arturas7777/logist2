/**
 * ИИ-помощник Caromoto Lithuania
 * Чат-виджет для взаимодействия с клиентами
 */

// Генерация уникального ID сессии
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// Получаем или создаем ID сессии
let sessionId = localStorage.getItem('ai_chat_session_id');
if (!sessionId) {
    sessionId = generateSessionId();
    localStorage.setItem('ai_chat_session_id', sessionId);
}

// Элементы интерфейса
const chatToggle = document.getElementById('ai-chat-toggle');
const chatWindow = document.getElementById('ai-chat-window');
const chatClose = document.getElementById('ai-chat-close');
const chatMessages = document.getElementById('ai-chat-messages');
const chatInput = document.getElementById('ai-chat-input');
const chatSend = document.getElementById('ai-chat-send');

if (!chatToggle || !chatWindow || !chatClose || !chatMessages || !chatInput || !chatSend) {
    console.warn('AI chat widget elements not found on this page');
} else {
// CSRF helper for Django
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return '';
}

const csrfToken = getCookie('csrftoken');

// Открытие/закрытие чата
chatToggle.addEventListener('click', () => {
    chatWindow.style.display = chatWindow.style.display === 'none' ? 'flex' : 'none';
    if (chatWindow.style.display === 'flex') {
        chatInput.focus();
        loadChatHistory();
    }
});

chatClose.addEventListener('click', () => {
    chatWindow.style.display = 'none';
});

// Добавление сообщения в чат
function addMessage(text, isUser = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = isUser ? 'user-message' : 'ai-message';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    if (isUser) {
        contentDiv.textContent = text;
    } else {
        appendTextWithLinks(contentDiv, text);
    }
    
    messageDiv.appendChild(contentDiv);
    chatMessages.appendChild(messageDiv);
    
    // Прокрутка вниз
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendTextWithLinks(container, text) {
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    const trailingPunctRegex = /[).,!?;:\]]+$/;
    let lastIndex = 0;
    let match;
    while ((match = urlRegex.exec(text)) !== null) {
        let url = match[0];
        let trailing = '';
        const trailingMatch = url.match(trailingPunctRegex);
        if (trailingMatch) {
            trailing = trailingMatch[0];
            url = url.slice(0, -trailing.length);
        }
        const start = match.index;
        if (start > lastIndex) {
            container.appendChild(document.createTextNode(text.slice(lastIndex, start)));
        }
        const link = document.createElement('a');
        link.href = url;
        link.textContent = 'Открыть';
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.style.margin = '0 4px';
        container.appendChild(link);
        if (trailing) {
            container.appendChild(document.createTextNode(trailing));
        }
        lastIndex = start + url.length;
    }
    if (lastIndex < text.length) {
        container.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
}

// Отправка сообщения
async function sendMessage() {
    const message = chatInput.value.trim();
    if (!message) return;
    
    // Добавляем сообщение пользователя
    addMessage(message, true);
    chatInput.value = '';
    
    // Показываем индикатор загрузки
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'ai-message';
    loadingDiv.innerHTML = '<div class="message-content typing-indicator"><span></span><span></span><span></span></div>';
    chatMessages.appendChild(loadingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    
    try {
        const response = await fetch('/api/ai-chat/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({
                message: message,
                session_id: sessionId
            })
        });
        
        const data = await response.json();
        
        // Удаляем индикатор загрузки
        loadingDiv.remove();
        
        if (response.ok) {
            // Добавляем ответ ИИ
            addMessage(data.response, false);
            
            // Сохраняем в локальное хранилище
            saveChatToLocal(message, data.response);

            if (data.meta && data.meta.used_fallback) {
                console.warn('AI fallback used:', data.meta.fallback_reason || 'unknown');
                addMessage('⚠️ Включен резервный режим ИИ (см. консоль).', false);
            }
        } else {
            addMessage('Извините, произошла ошибка. Попробуйте еще раз.', false);
        }
    } catch (error) {
        loadingDiv.remove();
        addMessage('Ошибка соединения. Проверьте интернет-подключение.', false);
        console.error('Chat error:', error);
    }
}

// Сохранение чата в локальное хранилище
function saveChatToLocal(userMessage, aiResponse) {
    let history = JSON.parse(localStorage.getItem('ai_chat_history') || '[]');
    history.push({
        user: userMessage,
        ai: aiResponse,
        timestamp: new Date().toISOString()
    });
    // Сохраняем только последние 50 сообщений
    if (history.length > 50) {
        history = history.slice(-50);
    }
    localStorage.setItem('ai_chat_history', JSON.stringify(history));
}

// Загрузка истории чата
function loadChatHistory() {
    const history = JSON.parse(localStorage.getItem('ai_chat_history') || '[]');
    
    // Показываем только последние 10 сообщений
    const recentHistory = history.slice(-10);
    
    // Очищаем чат кроме приветственного сообщения
    while (chatMessages.children.length > 1) {
        chatMessages.removeChild(chatMessages.lastChild);
    }
    
    // Добавляем историю
    recentHistory.forEach(msg => {
        addMessage(msg.user, true);
        addMessage(msg.ai, false);
    });
}

// Очистка истории (можно вызвать из консоли)
function clearChatHistory() {
    localStorage.removeItem('ai_chat_history');
    localStorage.removeItem('ai_chat_session_id');
    sessionId = generateSessionId();
    localStorage.setItem('ai_chat_session_id', sessionId);
    
    // Очищаем чат
    while (chatMessages.children.length > 1) {
        chatMessages.removeChild(chatMessages.lastChild);
    }
}

// События
chatSend.addEventListener('click', sendMessage);

chatInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Автоматическое открытие чата при первом посещении (опционально)
const hasVisited = localStorage.getItem('has_visited_site');
if (!hasVisited) {
    setTimeout(() => {
        chatWindow.style.display = 'flex';
        addMessage('Здравствуйте! 👋 Нужна помощь? Я могу ответить на вопросы о доставке, отслеживании груза и наших услугах.', false);
    }, 3000);
    localStorage.setItem('has_visited_site', 'true');
}

console.log('ИИ-помощник Caromoto Lithuania инициализирован');
console.log('Для очистки истории вызовите: clearChatHistory()');
}


