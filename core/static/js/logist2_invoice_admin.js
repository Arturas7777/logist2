/**
 * Новый JavaScript для создания инвойсов
 * VERSION: 1.0
 */

class InvoiceBuilder {
    constructor() {
        console.log('Создание InvoiceBuilder...');
        this.selectedCars = new Set();
        this.fromEntity = null;
        this.toEntity = null;
        this.serviceType = null;
        this.totalCost = 0;
        this.searchTimer = null;
        
        console.log('Инициализация обработчиков событий...');
        this.initializeEventListeners();
        this.setCurrentDate();
        console.log('InvoiceBuilder инициализирован');
    }
    
    initializeEventListeners() {
        console.log('Настройка обработчиков событий...');
        
        // Обработчики для выбора типа отправителя
        const fromEntityType = document.getElementById('from_entity_type');
        if (fromEntityType) {
            fromEntityType.addEventListener('change', (e) => {
                this.handleEntityTypeChange('from', e.target.value);
            });
            console.log('Обработчик from_entity_type добавлен');
        } else {
            console.error('Элемент from_entity_type не найден!');
        }
        
        // Обработчики для выбора типа получателя
        const toEntityType = document.getElementById('to_entity_type');
        if (toEntityType) {
            toEntityType.addEventListener('change', (e) => {
                this.handleEntityTypeChange('to', e.target.value);
            });
            console.log('Обработчик to_entity_type добавлен');
        } else {
            console.error('Элемент to_entity_type не найден!');
        }
        
        // Обработчик для выбора типа услуг
        const serviceType = document.getElementById('service_type');
        if (serviceType) {
            serviceType.addEventListener('change', (e) => {
                this.handleServiceTypeChange(e.target.value);
            });
            console.log('Обработчик service_type добавлен');
        } else {
            console.error('Элемент service_type не найден!');
        }
        
        // Обработчики поиска отправителя
        const fromEntitySearch = document.getElementById('from_entity_search');
        if (fromEntitySearch) {
            fromEntitySearch.addEventListener('input', (e) => {
                this.handleEntitySearch('from', e.target.value);
            });
            fromEntitySearch.addEventListener('blur', (e) => {
                setTimeout(() => this.clearSearchResults('from'), 200);
            });
            console.log('Обработчики from_entity_search добавлены');
        } else {
            console.error('Элемент from_entity_search не найден!');
        }
        
        // Обработчики поиска получателя
        const toEntitySearch = document.getElementById('to_entity_search');
        if (toEntitySearch) {
            toEntitySearch.addEventListener('input', (e) => {
                this.handleEntitySearch('to', e.target.value);
            });
            toEntitySearch.addEventListener('blur', (e) => {
                setTimeout(() => this.clearSearchResults('to'), 200);
            });
            console.log('Обработчики to_entity_search добавлены');
        } else {
            console.error('Элемент to_entity_search не найден!');
        }
        
        // Обработчик поиска автомобилей
        const carSearch = document.getElementById('car_search');
        if (carSearch) {
            carSearch.addEventListener('input', (e) => {
                this.handleCarSearch(e.target.value);
            });
            console.log('Обработчик car_search добавлен');
        } else {
            console.error('Элемент car_search не найден!');
        }
        
        // Обработчик формы
        const invoiceForm = document.getElementById('invoice_form');
        if (invoiceForm) {
            invoiceForm.addEventListener('submit', (e) => {
                console.log('Форма отправлена!');
                this.handleFormSubmit(e);
            });
            console.log('Обработчик invoice_form добавлен');
        } else {
            console.error('Элемент invoice_form не найден!');
        }
        
        // Обработчик сохранения черновика
        const saveDraftBtn = document.getElementById('save_draft');
        if (saveDraftBtn) {
            saveDraftBtn.addEventListener('click', (e) => {
                console.log('Кнопка "Сохранить черновик" нажата!');
                this.handleSaveDraft(e);
            });
            console.log('Обработчик save_draft добавлен');
        } else {
            console.error('Элемент save_draft не найден!');
        }
        
        // Проверяем кнопку создания инвойса
        const createInvoiceBtn = document.getElementById('create_invoice');
        if (createInvoiceBtn) {
            console.log('Кнопка create_invoice найдена, disabled:', createInvoiceBtn.disabled);
        } else {
            console.error('Элемент create_invoice не найден!');
        }
    }
    
    setCurrentDate() {
        const now = new Date();
        const dateString = now.toLocaleDateString('ru-RU');
        document.getElementById('creation_date').textContent = dateString;
    }
    
    handleEntityTypeChange(entitySide, entityType) {
        const searchInput = document.getElementById(`${entitySide}_entity_search`);
        const resultsDiv = document.getElementById(`${entitySide}_entity_results`);
        const selectedDiv = document.getElementById(`${entitySide}_entity_selected`);
        
        // Очищаем предыдущие результаты
        resultsDiv.innerHTML = '';
        resultsDiv.style.display = 'none';
        selectedDiv.classList.remove('show');
        
        if (entityType) {
            searchInput.disabled = false;
            searchInput.focus();
        } else {
            searchInput.disabled = true;
            searchInput.value = '';
        }
        
        this.checkFormCompletion();
    }
    
    handleServiceTypeChange(serviceType) {
        this.serviceType = serviceType;
        console.log('Выбран тип услуг:', serviceType);
        
        // Обновляем скрытое поле Django
        const serviceTypeField = document.getElementById('id_service_type');
        if (serviceTypeField) {
            serviceTypeField.value = serviceType;
        }
        
        // Пересчитываем стоимость уже выбранных автомобилей
        this.recalculateSelectedCarsCost();
        
        this.checkFormCompletion();
    }
    
    async handleEntitySearch(entitySide, searchQuery) {
        const entityType = document.getElementById(`${entitySide}_entity_type`).value;
        const resultsDiv = document.getElementById(`${entitySide}_entity_results`);
        
        if (!entityType || searchQuery.length < 2) {
            resultsDiv.style.display = 'none';
            return;
        }
        
        // Очищаем предыдущий таймер
        if (this.searchTimer) {
            clearTimeout(this.searchTimer);
        }
        
        // Устанавливаем задержку в 300мс
        this.searchTimer = setTimeout(async () => {
            try {
                const response = await fetch(`/api/search-partners/?entity_type=${entityType}&search=${encodeURIComponent(searchQuery)}`);
                const data = await response.json();
                
                if (data.objects && data.objects.length > 0) {
                    this.displayEntityResults(entitySide, data.objects);
                } else {
                    resultsDiv.innerHTML = '<div class="entity-result">Ничего не найдено</div>';
                    resultsDiv.style.display = 'block';
                }
            } catch (error) {
                console.error('Ошибка поиска партнеров:', error);
                this.showMessage('Ошибка поиска партнеров', 'error');
            }
        }, 300);
    }
    
    displayEntityResults(entitySide, partners) {
        const resultsDiv = document.getElementById(`${entitySide}_entity_results`);
        
        resultsDiv.innerHTML = partners.map(partner => `
            <div class="entity-result" data-id="${partner.id}" data-name="${partner.name}" data-type="${partner.type}">
                <div class="entity-name">${partner.name}</div>
                <div class="entity-type">${this.getEntityTypeDisplay(partner.type)}</div>
            </div>
        `).join('');
        
        // Добавляем обработчики кликов
        resultsDiv.querySelectorAll('.entity-result').forEach(result => {
            result.addEventListener('click', () => {
                this.selectEntity(entitySide, result.dataset);
            });
        });
        
        resultsDiv.style.display = 'block';
    }
    
    clearSearchResults(entitySide) {
        const resultsDiv = document.getElementById(`${entitySide}_entity_results`);
        if (resultsDiv) {
            resultsDiv.style.display = 'none';
        }
    }
    
    selectEntity(entitySide, entityData) {
        const searchInput = document.getElementById(`${entitySide}_entity_search`);
        const resultsDiv = document.getElementById(`${entitySide}_entity_results`);
        const selectedDiv = document.getElementById(`${entitySide}_entity_selected`);
        const displaySpan = document.getElementById(`${entitySide}_entity_display`);
        
        // Сохраняем выбранную сущность
        this[`${entitySide}Entity`] = {
            id: entityData.id,
            name: entityData.name,
            type: entityData.type
        };
        
        // Обновляем UI
        searchInput.value = entityData.name;
        resultsDiv.style.display = 'none';
        displaySpan.textContent = `${entityData.name} (${this.getEntityTypeDisplay(entityData.type)})`;
        selectedDiv.classList.add('show');
        
        // Обновляем скрытые поля Django
        document.getElementById(`id_${entitySide}_entity_type`).value = entityData.type;
        document.getElementById(`id_${entitySide}_entity_id`).value = entityData.id;
        
        this.checkFormCompletion();
        
        // Если выбран отправитель, загружаем доступные автомобили
        if (entitySide === 'from') {
            this.loadInvoiceCars();
        }
    }
    
    getEntityTypeDisplay(entityType) {
        const types = {
            'CLIENT': 'Клиент',
            'WAREHOUSE': 'Склад',
            'LINE': 'Линия',
            'CARRIER': 'Перевозчик',
            'COMPANY': 'Компания'
        };
        return types[entityType] || entityType;
    }
    
    calculateServiceCost(car) {
        if (!this.serviceType) {
            return parseFloat(car.total_cost || 0);
        }
        
        switch (this.serviceType) {
            case 'WAREHOUSE_SERVICES':
                // Услуги склада: только стоимость хранения
                return parseFloat(car.storage_cost || 0);
            case 'LINE_SERVICES':
                // Услуги линий: стоимость перевозки + THS сбор
                return parseFloat(car.ocean_freight || 0) + parseFloat(car.ths || 0);
            case 'CARRIER_SERVICES':
                // Услуги перевозчиков: доставка до склада + перевозка по Казахстану
                return parseFloat(car.delivery_fee || 0) + parseFloat(car.transport_kz || 0);
            case 'TRANSPORT_SERVICES':
                // Транспортные услуги: доставка до склада + перевозка по Казахстану
                return parseFloat(car.delivery_fee || 0) + parseFloat(car.transport_kz || 0);
            default:
                // Прочие услуги: полная стоимость
                return parseFloat(car.total_cost || 0);
        }
    }
    
    recalculateSelectedCarsCost() {
        // Пересчитываем общую стоимость на основе выбранного типа услуг
        this.totalCost = 0;
        
        // Обновляем стоимость в карточках автомобилей
        document.querySelectorAll('.car-card').forEach(card => {
            const carData = JSON.parse(card.dataset.carData);
            const newCost = this.calculateServiceCost(carData);
            card.dataset.cost = newCost;
            card.querySelector('.car-costs').textContent = `${newCost.toFixed(2)} €`;
            
            // Если автомобиль выбран, добавляем его стоимость к общей
            if (this.selectedCars.has(card.dataset.carId)) {
                this.totalCost += newCost;
            }
        });
        
        // Обновляем сводку
        this.updateInvoiceSummary();
    }
    
    async loadInvoiceCars() {
        if (!this.fromEntity) {
            this.showMessage('Сначала выберите отправителя', 'error');
            return;
        }
        
        try {
            let url = `/api/invoice-cars/?from_entity_type=${this.fromEntity.type}&from_entity_id=${this.fromEntity.id}`;
            
            // Добавляем получателя, если он выбран
            if (this.toEntity) {
                url += `&to_entity_type=${this.toEntity.type}&to_entity_id=${this.toEntity.id}`;
            }
            
            const response = await fetch(url);
            const data = await response.json();
            
            if (data.cars && data.cars.length > 0) {
                this.displayAvailableCars(data.cars);
            } else {
                this.displayAvailableCars([]);
            }
        } catch (error) {
            console.error('Ошибка загрузки автомобилей для инвойса:', error);
            this.showMessage('Ошибка загрузки автомобилей для инвойса', 'error');
        }
    }
    
    displayAvailableCars(cars) {
        const carsContainer = document.getElementById('available_cars');
        const carsStep = document.getElementById('cars_step');
        
        if (cars.length === 0) {
            carsContainer.innerHTML = '<div style="text-align: center; color: #6c757d; padding: 40px;">Нет доступных автомобилей</div>';
        } else {
            carsContainer.innerHTML = cars.map(car => {
                // Рассчитываем стоимость в зависимости от типа услуг
                const serviceCost = this.calculateServiceCost(car);
                return `
                    <div class="car-card" data-car-id="${car.id}" data-cost="${serviceCost}" data-car-data='${JSON.stringify(car)}'>
                        <div class="car-vin">${car.vin}</div>
                        <div class="car-details">
                            ${car.brand} ${car.year}<br>
                            Статус: ${car.status}<br>
                            Клиент: ${car.client_name}<br>
                            Склад: ${car.warehouse_name}<br>
                            Дата разгрузки: ${car.unload_date}
                            ${car.transfer_date !== 'Не указана' ? '<br>Дата передачи: ' + car.transfer_date : ''}
                        </div>
                        <div class="car-costs">${serviceCost.toFixed(2)} €</div>
                    </div>
                `;
            }).join('');
            
            // Добавляем обработчики кликов для выбора автомобилей
            carsContainer.querySelectorAll('.car-card').forEach(card => {
                card.addEventListener('click', () => {
                    this.toggleCarSelection(card);
                });
            });
        }
        
        carsStep.style.display = 'block';
    }
    
    toggleCarSelection(carCard) {
        const carId = carCard.dataset.carId;
        const cost = parseFloat(carCard.dataset.cost);
        
        console.log('Переключение выбора автомобиля:', carId, 'стоимость:', cost);
        
        if (this.selectedCars.has(carId)) {
            // Убираем автомобиль
            this.selectedCars.delete(carId);
            this.totalCost -= cost;
            carCard.classList.remove('selected');
            console.log('Автомобиль удален из выбора');
        } else {
            // Добавляем автомобиль
            this.selectedCars.add(carId);
            this.totalCost += cost;
            carCard.classList.add('selected');
            console.log('Автомобиль добавлен в выбор');
        }
        
        console.log('Выбранных автомобилей:', this.selectedCars.size);
        console.log('Общая стоимость:', this.totalCost);
        
        this.updateInvoiceSummary();
        this.updateHiddenCarsField();
        this.checkFormCompletion();
    }
    
    updateInvoiceSummary() {
        document.getElementById('cars_count').textContent = this.selectedCars.size;
        document.getElementById('total_cost').textContent = `${this.totalCost.toFixed(2)} €`;
        
        // Показываем сводку, если есть выбранные автомобили
        const summary = document.getElementById('invoice_summary');
        if (this.selectedCars.size > 0) {
            summary.style.display = 'block';
        } else {
            summary.style.display = 'none';
        }
    }
    
    updateHiddenCarsField() {
        // Ищем поле cars разными способами
        let carsField = document.getElementById('id_cars');
        if (!carsField) {
            // Пробуем найти по name
            carsField = document.querySelector('input[name="cars"]');
        }
        if (!carsField) {
            // Пробуем найти по селектору
            carsField = document.querySelector('select[name="cars"]');
        }
        
        console.log('Поле cars найдено:', carsField);
        console.log('Тип поля:', carsField?.tagName);
        
        if (carsField) {
            const carIds = Array.from(this.selectedCars);
            
            if (carsField.tagName === 'SELECT') {
                // Для select multiple нужно установить selected для опций
                const options = carsField.querySelectorAll('option');
                options.forEach(option => {
                    option.selected = carIds.includes(option.value);
                });
                console.log('Обновлено поле cars (select):', carIds);
            } else {
                // Для input просто устанавливаем value
                carsField.value = carIds.join(',');
                console.log('Обновлено поле cars (input):', carIds, 'значение:', carsField.value);
            }
        } else {
            console.error('Поле cars не найдено!');
        }
    }
    
    async handleCarSearch(searchQuery) {
        if (!this.fromEntity) {
            return;
        }
        
        try {
            let url = `/api/invoice-cars/?from_entity_type=${this.fromEntity.type}&from_entity_id=${this.fromEntity.id}&search=${encodeURIComponent(searchQuery)}`;
            
            // Добавляем получателя, если он выбран
            if (this.toEntity) {
                url += `&to_entity_type=${this.toEntity.type}&to_entity_id=${this.toEntity.id}`;
            }
            
            const response = await fetch(url);
            const data = await response.json();
            
            if (data.cars) {
                this.displayAvailableCars(data.cars);
            }
        } catch (error) {
            console.error('Ошибка поиска автомобилей:', error);
        }
    }
    
    checkFormCompletion() {
        const createButton = document.getElementById('create_invoice');
        const hasFromEntity = this.fromEntity !== null;
        const hasToEntity = this.toEntity !== null;
        const hasServiceType = this.serviceType !== null;
        const hasCars = this.selectedCars.size > 0;
        
        console.log('Проверка завершенности формы:');
        console.log('hasFromEntity:', hasFromEntity);
        console.log('hasToEntity:', hasToEntity);
        console.log('hasServiceType:', hasServiceType);
        console.log('hasCars:', hasCars);
        console.log('Кнопка будет включена:', hasFromEntity && hasToEntity && hasServiceType && hasCars);
        
        createButton.disabled = !(hasFromEntity && hasToEntity && hasServiceType && hasCars);
    }
    
    async handleFormSubmit(e) {
        e.preventDefault();
        console.log('Обработка отправки формы...');
        
        if (!this.validateForm()) {
            console.log('Валидация не прошла');
            return;
        }
        
        console.log('Валидация прошла успешно');
        
        try {
            // Устанавливаем номер инвойса
            const invoiceNumber = this.generateInvoiceNumber();
            const numberField = document.getElementById('id_number');
            if (numberField) {
                numberField.value = invoiceNumber;
            } else {
                console.error('Поле id_number не найдено!');
            }
            
            // Устанавливаем дату выпуска
            const today = new Date().toISOString().split('T')[0];
            const issueDateField = document.getElementById('id_issue_date');
            if (issueDateField) {
                issueDateField.value = today;
            } else {
                console.error('Поле id_issue_date не найдено!');
            }
            
            // Устанавливаем общую сумму
            const totalAmountField = document.getElementById('id_total_amount');
            if (totalAmountField) {
                totalAmountField.value = this.totalCost.toFixed(2);
            } else {
                console.error('Поле id_total_amount не найдено!');
            }
            
            // Устанавливаем статус оплаты
            const paidField = document.getElementById('id_paid');
            if (paidField) {
                paidField.checked = false;
            } else {
                console.error('Поле id_paid не найдено!');
            }
            
            // Устанавливаем направление (исходящий инвойс)
            const isOutgoingField = document.getElementById('id_is_outgoing');
            if (isOutgoingField) {
                isOutgoingField.checked = true;
            } else {
                console.error('Поле id_is_outgoing не найдено!');
            }
            
            // Обновляем поле cars перед отправкой
            this.updateHiddenCarsField();
            
            console.log('Скрытые поля заполнены:');
            console.log('Номер:', invoiceNumber);
            console.log('Дата:', today);
            console.log('Сумма:', this.totalCost.toFixed(2));
            console.log('Выбранные автомобили:', Array.from(this.selectedCars));
            
            // Проверяем, что все поля заполнены
            const fromEntityTypeField = document.getElementById('id_from_entity_type');
            const fromEntityIdField = document.getElementById('id_from_entity_id');
            const toEntityTypeField = document.getElementById('id_to_entity_type');
            const toEntityIdField = document.getElementById('id_to_entity_id');
            const carsField = document.getElementById('id_cars');
            
            console.log('Проверка полей:');
            console.log('numberField.value:', document.getElementById('id_number')?.value);
            console.log('fromEntityTypeField.value:', fromEntityTypeField?.value);
            console.log('fromEntityIdField.value:', fromEntityIdField?.value);
            console.log('toEntityTypeField.value:', toEntityTypeField?.value);
            console.log('toEntityIdField.value:', toEntityIdField?.value);
            console.log('carsField.value:', carsField?.value);
            console.log('totalAmountField.value:', totalAmountField?.value);
            
            // Дополнительная отладка для поля cars
            console.log('Отладка поля cars:');
            console.log('selectedCars:', Array.from(this.selectedCars));
            console.log('carsField элемент:', carsField);
            console.log('carsField тип:', carsField?.tagName);
            console.log('carsField name:', carsField?.name);
            console.log('carsField id:', carsField?.id);
            
            // Устанавливаем общую сумму (повторно для надежности)
            if (totalAmountField) {
                totalAmountField.value = this.totalCost.toFixed(2);
            }
            
            // Отправляем форму
            this.showMessage('Инвойс создается...', 'success');
            
            // Небольшая задержка для показа сообщения
            setTimeout(() => {
                e.target.submit();
            }, 1000);
            
        } catch (error) {
            console.error('Ошибка создания инвойса:', error);
            console.error('Детали ошибки:', error.message);
            console.error('Стек ошибки:', error.stack);
            this.showMessage(`Ошибка создания инвойса: ${error.message}`, 'error');
        }
    }
    
    validateForm() {
        console.log('Проверка валидации:');
        console.log('fromEntity:', this.fromEntity);
        console.log('toEntity:', this.toEntity);
        console.log('selectedCars.size:', this.selectedCars.size);
        
        if (!this.fromEntity) {
            console.log('Ошибка: нет отправителя');
            this.showMessage('Выберите отправителя', 'error');
            return false;
        }
        
        if (!this.toEntity) {
            console.log('Ошибка: нет получателя');
            this.showMessage('Выберите получателя', 'error');
            return false;
        }
        
        if (this.selectedCars.size === 0) {
            console.log('Ошибка: нет выбранных автомобилей');
            this.showMessage('Выберите хотя бы один автомобиль', 'error');
            return false;
        }
        
        console.log('Валидация прошла успешно');
        return true;
    }
    
    generateInvoiceNumber() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        const timestamp = Date.now().toString().slice(-4);
        
        return `INV-${year}${month}${day}-${timestamp}`;
    }
    
    async handleSaveDraft(e) {
        e.preventDefault();
        
        // Обновляем поле cars перед сохранением
        this.updateHiddenCarsField();
        
        // Сохраняем как черновик (paid = false)
        document.getElementById('id_paid').checked = false;
        
        // Устанавливаем минимальные значения
        if (!document.getElementById('id_number').value) {
            document.getElementById('id_number').value = this.generateInvoiceNumber();
        }
        
        if (!document.getElementById('id_issue_date').value) {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('id_issue_date').value = today;
        }
        
        this.showMessage('Черновик сохраняется...', 'success');
        
        // Отправляем форму
        setTimeout(() => {
            document.getElementById('invoice_form').submit();
        }, 1000);
    }
    
    showMessage(message, type) {
        const errorDiv = document.getElementById('error_message');
        const successDiv = document.getElementById('success_message');
        
        if (type === 'error') {
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            successDiv.style.display = 'none';
        } else {
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            errorDiv.style.display = 'none';
        }
        
        // Автоматически скрываем сообщения через 5 секунд
        setTimeout(() => {
            errorDiv.style.display = 'none';
            successDiv.style.display = 'none';
        }, 5000);
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM загружен, инициализируем InvoiceBuilder...');
    
    // Добавляем небольшую задержку для полной загрузки всех элементов
    setTimeout(() => {
        console.log('Проверяем наличие элементов формы...');
        console.log('URL:', window.location.href);
        console.log('Заголовок страницы:', document.title);
        
        // Проверяем различные возможные формы
        const invoiceForm = document.getElementById('invoice_form');
        const changeForm = document.querySelector('form[action*="change"]');
        const addForm = document.querySelector('form[action*="add"]');
        const anyForm = document.querySelector('form');
        
        console.log('invoice_form:', invoiceForm);
        console.log('changeForm:', changeForm);
        console.log('addForm:', addForm);
        console.log('anyForm:', anyForm);
        
        // Проверяем, есть ли блок с классом invoice-builder
        const invoiceBuilder = document.querySelector('.invoice-builder');
        console.log('invoice-builder блок:', invoiceBuilder);
        
        if (invoiceForm) {
            console.log('Форма найдена, создаем InvoiceBuilder...');
            const invoiceBuilder = new InvoiceBuilder();
            console.log('InvoiceBuilder создан:', invoiceBuilder);
        } else if (invoiceBuilder) {
            console.log('Блок invoice-builder найден, но форма invoice_form отсутствует');
            console.log('Содержимое блока:', invoiceBuilder.innerHTML.substring(0, 200) + '...');
        } else {
            console.log('Ни форма, ни блок invoice-builder не найдены');
            console.log('Все формы на странице:', Array.from(document.querySelectorAll('form')).map(f => f.id || f.className || 'без ID'));
        }
    }, 100);
});
