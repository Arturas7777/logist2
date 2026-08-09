"""Формы кабинета клиента: регистрация, документы, декларации, заявки на автовоз."""

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Exists, OuterRef

from core.models import Car
from core.models.website import ClientDocument, DeclarationRequest, TransportRequest

# Максимальный размер загружаемого клиентом файла (20 МБ).
MAX_UPLOAD_SIZE = 20 * 1024 * 1024


class ClientRegistrationForm(forms.Form):
    """Самостоятельная регистрация клиента на сайте.

    Создаёт User + новый Client + ClientUser(is_verified=False).
    ВАЖНО: к существующему клиенту CRM по email НЕ привязываем автоматически —
    иначе любой, кто знает email клиента, получил бы доступ к его данным.
    Привязку к реальному клиенту делает сотрудник в админке.
    """

    name = forms.CharField(max_length=100, label="Имя / название компании")
    email = forms.EmailField(label="Email")
    phone = forms.CharField(max_length=20, required=False, label="Телефон")
    username = forms.CharField(max_length=150, label="Имя пользователя (логин)")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Пароль")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Пароль (ещё раз)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Пользователь с таким логином уже существует.")
        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Пароли не совпадают.")
        elif password1:
            validate_password(password1)
        return cleaned


class ClientDocumentForm(forms.ModelForm):
    """Загрузка документа клиентом (привязка к своему авто опциональна)."""

    class Meta:
        model = ClientDocument
        fields = ["car", "document_type", "file", "comment"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["car"].queryset = Car.objects.filter(client=client).order_by("-id")
        self.fields["car"].required = False
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css)

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > MAX_UPLOAD_SIZE:
            raise forms.ValidationError("Файл слишком большой (максимум 20 МБ).")
        return file


class DeclarationRequestForm(forms.ModelForm):
    """Заявка на оформление декларации по конкретному авто."""

    class Meta:
        model = DeclarationRequest
        fields = [
            "car",
            "declaration_type",
            "buyer_name",
            "buyer_code",
            "buyer_country",
            "buyer_address",
            "destination_country",
            "destination_city",
            "invoice_value",
            "currency",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["car"].queryset = Car.objects.filter(client=client).order_by("-id")
        for field in self.fields.values():
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css)


class TransportRequestForm(forms.ModelForm):
    """Заявка с данными автовоза, который заберёт автомобили клиента."""

    class Meta:
        model = TransportRequest
        fields = [
            "cars",
            "carrier_name",
            "carrier_eori",
            "truck_number",
            "trailer_number",
            "driver_name",
            "driver_phone",
            "border_crossing",
            "planned_loading_date",
            "comment",
        ]
        widgets = {
            "cars": forms.CheckboxSelectMultiple,
            "planned_loading_date": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Забрать автовозом можно только машины, которые ещё не переданы
        # и не заблокированы (is_important — блокировка добавления в автовоз).
        # Авто, уже состоящее в другой активной заявке (не «Оформлена»),
        # выбрать нельзя. При редактировании — плюс машины этой заявки.
        in_other_active_request = TransportRequest.objects.filter(cars=OuterRef("pk")).exclude(
            status__in=TransportRequest.INACTIVE_STATUSES
        )
        if self.instance.pk:
            in_other_active_request = in_other_active_request.exclude(pk=self.instance.pk)
        cars_qs = (
            Car.objects.filter(client=client)
            .exclude(status="TRANSFERRED")
            .filter(is_important=False)
            .filter(~Exists(in_other_active_request))
            .order_by("-id")
        )
        if self.instance.pk:
            cars_qs = (cars_qs | self.instance.cars.all()).distinct().order_by("-id")
        self.fields["cars"].queryset = cars_qs
        # Перевозчик и его EORI обязательны для оформления.
        self.fields["carrier_name"].required = True
        self.fields["carrier_eori"].required = True
        # Автокомплит браузера мешает выпадающему списку перевозчиков.
        self.fields["carrier_name"].widget.attrs["autocomplete"] = "off"
        for name, field in self.fields.items():
            if name == "cars":
                continue
            css = "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            field.widget.attrs.setdefault("class", css)
