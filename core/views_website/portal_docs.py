"""Кабинет клиента: документы и заявки на оформление декларации."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models.website import ClientDocument, ClientUser, DeclarationRequest

from .forms import ClientDocumentForm, DeclarationRequestForm


def _get_client(request):
    """Клиент текущего пользователя портала или None (нет доступа)."""
    try:
        return request.user.clientuser.client
    except ClientUser.DoesNotExist:
        return None


@login_required
def client_documents(request):
    """Страница «Документы»: загруженные файлы + заявки на декларации."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    documents = ClientDocument.objects.filter(client=client).select_related("car")
    declarations = DeclarationRequest.objects.filter(client=client).select_related("car")

    context = {
        "client": client,
        "documents": documents,
        "declarations": declarations,
        "document_form": ClientDocumentForm(client=client),
        "declaration_form": DeclarationRequestForm(client=client),
    }
    return render(request, "website/client_documents.html", context)


@login_required
@require_POST
def upload_document(request):
    """Приём загруженного клиентом документа."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    form = ClientDocumentForm(request.POST, request.FILES, client=client)
    if form.is_valid():
        document = form.save(commit=False)
        document.client = client
        document.uploaded_by = request.user
        document.save()
        messages.success(request, "Документ загружен. Мы проверим его в ближайшее время.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("website:documents")


@login_required
@require_POST
def create_declaration_request(request):
    """Создание заявки на оформление декларации."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    form = DeclarationRequestForm(request.POST, client=client)
    if form.is_valid():
        declaration = form.save(commit=False)
        declaration.client = client
        declaration.created_by = request.user
        declaration.save()
        messages.success(
            request,
            f"Заявка на декларацию {declaration.number} создана. Печатная форма доступна в списке заявок.",
        )
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("website:documents")


@login_required
def declaration_print(request, pk):
    """Печатная форма заявки на декларацию (для передачи брокеру / в таможню)."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    declaration = get_object_or_404(
        DeclarationRequest.objects.select_related("car", "car__container", "car__warehouse", "client"),
        pk=pk,
        client=client,
    )
    documents = ClientDocument.objects.filter(client=client, car=declaration.car)
    return render(
        request,
        "website/declaration_print.html",
        {"declaration": declaration, "car": declaration.car, "client": client, "documents": documents},
    )
