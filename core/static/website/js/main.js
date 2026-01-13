/**
 * Caromoto Lithuania - Main JavaScript
 * Основная функциональность сайта
 */

// Плавная прокрутка для якорных ссылок
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// Анимация элементов при прокрутке
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('fade-in');
            observer.unobserve(entry.target);
        }
    });
}, observerOptions);

// Наблюдаем за карточками
document.querySelectorAll('.card, .feature-card, .step-card').forEach(el => {
    observer.observe(el);
});

// Показать/скрыть кнопку "Наверх"
window.addEventListener('scroll', () => {
    const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;
    const scrollButton = document.getElementById('scroll-to-top');
    
    if (scrollButton) {
        if (scrollTop > 300) {
            scrollButton.style.display = 'block';
        } else {
            scrollButton.style.display = 'none';
        }
    }
});

// Форматирование номеров телефонов
function formatPhoneNumber(input) {
    const value = input.value.replace(/\D/g, '');
    let formatted = '';
    
    if (value.length > 0) {
        formatted = '+' + value.substring(0, 12);
    }
    
    input.value = formatted;
}

// Добавляем форматирование к полям телефона
document.querySelectorAll('input[type="tel"]').forEach(input => {
    input.addEventListener('input', () => formatPhoneNumber(input));
});

// Валидация форм
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

// Toast уведомления
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toast-container') || createToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    toastContainer.appendChild(toast);
    
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
    
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed top-0 end-0 p-3';
    container.style.zIndex = '9999';
    document.body.appendChild(container);
    return container;
}

// Копирование в буфер обмена
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('Скопировано в буфер обмена!', 'success');
    }).catch(err => {
        console.error('Ошибка копирования:', err);
        showToast('Ошибка при копировании', 'danger');
    });
}

// Добавляем возможность копирования VIN по клику
document.querySelectorAll('code').forEach(code => {
    code.style.cursor = 'pointer';
    code.title = 'Нажмите чтобы скопировать';
    code.addEventListener('click', () => {
        copyToClipboard(code.textContent);
    });
});

// Предзагрузка изображений
function preloadImages(urls) {
    urls.forEach(url => {
        const img = new Image();
        img.src = url;
    });
}

// Lazy loading для изображений
if ('IntersectionObserver' in window) {
    const imageObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target;
                img.src = img.dataset.src;
                img.classList.remove('lazy');
                imageObserver.unobserve(img);
            }
        });
    });
    
    document.querySelectorAll('img[data-src]').forEach(img => {
        imageObserver.observe(img);
    });
}

// Защита от спама в формах (простая honeypot)
document.querySelectorAll('form').forEach(form => {
    // Добавляем скрытое поле-ловушку для ботов
    const honeypot = document.createElement('input');
    honeypot.type = 'text';
    honeypot.name = 'website';
    honeypot.style.display = 'none';
    honeypot.tabIndex = -1;
    honeypot.autocomplete = 'off';
    form.appendChild(honeypot);
    
    // Проверяем при отправке
    form.addEventListener('submit', (e) => {
        if (honeypot.value !== '') {
            e.preventDefault();
            console.warn('Spam detected');
            return false;
        }
    });
});

// Время на странице (для аналитики)
let pageStartTime = Date.now();

window.addEventListener('beforeunload', () => {
    const timeSpent = Math.round((Date.now() - pageStartTime) / 1000);
    // Можно отправить на сервер для аналитики
    console.log('Time spent on page:', timeSpent, 'seconds');
});

// Debug режим
const isDebug = new URLSearchParams(window.location.search).has('debug');
if (isDebug) {
    console.log('Debug mode enabled');
    console.log('Page loaded at:', new Date().toISOString());
}

// Инициализация всплывающих подсказок Bootstrap
const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));

// Инициализация popovers Bootstrap
const popoverTriggerList = document.querySelectorAll('[data-bs-toggle="popover"]');
const popoverList = [...popoverTriggerList].map(popoverTriggerEl => new bootstrap.Popover(popoverTriggerEl));

console.log('Caromoto Lithuania - сайт загружен успешно');


