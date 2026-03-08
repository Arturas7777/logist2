/**
 * Новый JavaScript для создания инвойсов
 * VERSION: 1.0
 */

class InvoiceBuilder {
    constructor() {
        this.selectedCars = new Set();
        this.fromEntity = null;
        this.toEntity = null;
        this.serviceType = null;
        this.totalCost = 0;
        this.searchTimer = null;
        
        this.initializeEventListeners();
        this.setCurrentDate();
    }
    
    initializeEventListeners() {
        
        // Обработчики для выбора типа отправителя
        const fromEntityType = document.getElementById('from_entity_type');
        if (fromEntityType) {
            fromEntityType.addEventListener('change', (e) => {
                this.handleEntityTypeChange('from', e.target.value);
            });
        } else {
            console.error('Элемент from_entity_type не найден!');
        }
        
        // Обработчики для выбора типа получателя
        const toEntityType = document.getElementById('to_entity_type');
        if (toEntityType) {
            toEntityType.addEventListener('change', (e) => {
                this.handleEntityTypeChange('to', e.target.value);
            });
        } else {
            console.error('Элемент to_entity_type не найден!');
        }
        
        // Обработчик для выбора типа услуг
        const serviceType = document.getElementById('service_type');
        if (serviceType) {
            serviceType.addEventListener('change', (e) => {
                this.handleServiceTypeChange(e.target.value);
            });
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
        } else {
            console.error('Элемент to_entity_search не найден!');
        }
        
        // Обработчик поиска автомобилей
        const carSearch = document.getElementById('car_search');
        if (carSearch) {
            carSearch.addEventListener('input', (e) => {
                this.handleCarSearch(e.target.value);
            });
        } else {
            console.error('Элемент car_search не найден!');
        }
        
        // Обработчик формы
        const invoiceForm = document.getElementById('invoice_form');
        if (invoiceForm) {
            invoiceForm.addEventListener('submit', (e) => {
                this.handleFormSubmit(e);
            });
        } else {
            console.error('Элемент invoice_form не найден!');
        }
        
        // Обработчик сохранения черновика
        const saveDraftBtn = document.getElementById('save_draft');
        if (saveDraftBtn) {
            saveDraftBtn.addEventListener('click', (e) => {
                this.handleSaveDraft(e);
            });
        } else {
            console.error('Элемент save_draft не найден!');
        }
        
        // Проверяем кнопку создания инвойса
        const createInvoiceBtn = document.getElementById('create_invoice');
        if (createInvoiceBtn) {
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
        
        resultsDiv.innerHTML = '';
        partners.forEach(partner => {
            const div = document.createElement('div');
            div.className = 'entity-result';
            div.dataset.id = partner.id;
            div.dataset.name = partner.name;
            div.dataset.type = partner.type;
            const nameDiv = document.createElement('div');
            nameDiv.className = 'entity-name';
            nameDiv.textContent = partner.name;
            const typeDiv = document.createElement('div');
            typeDiv.className = 'entity-type';
            typeDiv.textContent = this.getEntityTypeDisplay(partner.type);
            div.appendChild(nameDiv);
            div.appendChild(typeDiv);
            resultsDiv.appendChild(div);
        });
        
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
            carsContainer.innerHTML = '';
            cars.forEach(car => {
                const serviceCost = this.calculateServiceCost(car);
                const card = document.createElement('div');
                card.className = 'car-card';
                card.dataset.carId = car.id;
                card.dataset.cost = serviceCost;
                card.dataset.carData = JSON.stringify(car);

                const vinDiv = document.createElement('div');
                vinDiv.className = 'car-vin';
                vinDiv.textContent = car.vin;

                const detailsDiv = document.createElement('div');
                detailsDiv.className = 'car-details';
                let details = `${car.brand} ${car.year}\nСтатус: ${car.status}\nКлиент: ${car.client_name}\nСклад: ${car.warehouse_name}\nДата разгрузки: ${car.unload_date}`;
                if (car.transfer_date !== 'Не указана') details += `\nДата передачи: ${car.transfer_date}`;
                detailsDiv.style.whiteSpace = 'pre-line';
                detailsDiv.textContent = details;

                const costsDiv = document.createElement('div');
                costsDiv.className = 'car-costs';
                costsDiv.textContent = `${serviceCost.toFixed(2)} €`;

                card.appendChild(vinDiv);
                card.appendChild(detailsDiv);
                card.appendChild(costsDiv);
                carsContainer.appendChild(card);
            });
            
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
        
        
        if (this.selectedCars.has(carId)) {
            // Убираем автомобиль
            this.selectedCars.delete(carId);
            this.totalCost -= cost;
            carCard.classList.remove('selected');
        } else {
            // Добавляем автомобиль
            this.selectedCars.add(carId);
            this.totalCost += cost;
            carCard.classList.add('selected');
        }
        
        
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
        
        
        if (carsField) {
            const carIds = Array.from(this.selectedCars);
            
            if (carsField.tagName === 'SELECT') {
                // Для select multiple нужно установить selected для опций
                const options = carsField.querySelectorAll('option');
                options.forEach(option => {
                    option.selected = carIds.includes(option.value);
                });
            } else {
                // Для input просто устанавливаем value
                carsField.value = carIds.join(',');
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
        
        
        createButton.disabled = !(hasFromEntity && hasToEntity && hasServiceType && hasCars);
    }
    
    async handleFormSubmit(e) {
        e.preventDefault();
        
        if (!this.validateForm()) {
            return;
        }
        
        
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
            
            
            // Проверяем, что все поля заполнены
            const fromEntityTypeField = document.getElementById('id_from_entity_type');
            const fromEntityIdField = document.getElementById('id_from_entity_id');
            const toEntityTypeField = document.getElementById('id_to_entity_type');
            const toEntityIdField = document.getElementById('id_to_entity_id');
            const carsField = document.getElementById('id_cars');
            
            
            // Дополнительная отладка для поля cars
            
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
        
        if (!this.fromEntity) {
            this.showMessage('Выберите отправителя', 'error');
            return false;
        }
        
        if (!this.toEntity) {
            this.showMessage('Выберите получателя', 'error');
            return false;
        }
        
        if (this.selectedCars.size === 0) {
            this.showMessage('Выберите хотя бы один автомобиль', 'error');
            return false;
        }
        
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
    
    // Добавляем небольшую задержку для полной загрузки всех элементов
    setTimeout(() => {
        
        // Проверяем различные возможные формы
        const invoiceForm = document.getElementById('invoice_form');
        const changeForm = document.querySelector('form[action*="change"]');
        const addForm = document.querySelector('form[action*="add"]');
        const anyForm = document.querySelector('form');
        
        
        // Проверяем, есть ли блок с классом invoice-builder
        const invoiceBuilder = document.querySelector('.invoice-builder');
        
        if (invoiceForm) {
            const invoiceBuilder = new InvoiceBuilder();
        } else if (invoiceBuilder) {
        } else {
        }
    }, 100);
});
