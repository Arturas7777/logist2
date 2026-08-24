"""Кабинет клиента: раздел «Мои счета».

Раньше выставленные клиенту счета были доступны только как файлы внутри
заявки на автовоз — увидеть общую картину по оплатам было нельзя.

Показываем всё, что клиенту действительно выставлено: черновики и отменённые
документы скрыты, входящие серии (FACT / INCBLC — счета контрагентов нам)
сюда по определению не попадают, так как у них не заполнен recipient_client.
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render

from core.mixins import OPEN_INVOICE_STATUSES
from core.models.billing import NewInvoice
from core.models.website import ClientUser

INVOICES_PER_PAGE = 25

# Документы, которых клиент в кабинете видеть не должен: черновик ещё не
# выставлен, отменённый счёт только путает.
HIDDEN_STATUSES = ("DRAFT", "CANCELLED")


def _get_client(request):
    try:
        return request.user.clientuser.client
    except ClientUser.DoesNotExist:
        return None


def _client_invoices(client):
    return (
        NewInvoice.objects.filter(recipient_client=client)
        .exclude(status__in=HIDDEN_STATUSES)
        .prefetch_related("cars")
        .order_by("-date", "-id")
    )


@login_required
def client_invoices(request):
    """Список счетов клиента со сводкой по задолженности."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    invoices = _client_invoices(client)

    only_unpaid = request.GET.get("filter") == "unpaid"
    if only_unpaid:
        invoices = invoices.filter(status__in=OPEN_INVOICE_STATUSES)

    paginator = Paginator(invoices, INVOICES_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    open_debt = client.open_invoices_debt
    total_balance = client.total_balance

    context = {
        "client": client,
        "invoices": page,
        "invoices_page": page,
        "only_unpaid": only_unpaid,
        "open_invoices_debt": open_debt,
        "total_balance": total_balance,
        "amount_due": -total_balance if total_balance < 0 else Decimal("0.00"),
        "prepaid_amount": total_balance if total_balance > 0 else Decimal("0.00"),
        "open_statuses": OPEN_INVOICE_STATUSES,
    }
    return render(request, "website/client_invoices.html", context)


@login_required
def client_invoice_download(request, pk):
    """Отдаёт файл счёта. Доступ — только к собственным документам клиента."""
    client = _get_client(request)
    if client is None:
        return render(request, "website/not_authorized.html", status=403)

    invoice = get_object_or_404(_client_invoices(client), pk=pk)
    if not invoice.attachment:
        raise Http404("У этого счёта нет прикреплённого файла")

    return FileResponse(
        invoice.attachment.open("rb"),
        as_attachment=True,
        filename=f"{invoice.number}{invoice.attachment.name[invoice.attachment.name.rfind('.') :]}",
    )
