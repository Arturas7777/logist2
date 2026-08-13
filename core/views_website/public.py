"""Публичные страницы сайта (home, about, services, contact, news)."""

from django.conf import settings
from django.db.models import F
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import check_for_language
from django.views.decorators.cache import cache_page, never_cache
from django.views.decorators.http import require_GET

from core.models_website import NewsPost


@cache_page(60 * 15)
def website_home(request):
    """Главная страница сайта."""
    latest_news = NewsPost.objects.filter(published=True).order_by("-published_at")[:3]

    context = {
        "latest_news": latest_news,
        "company_name": "Caromoto Lithuania",
    }
    return render(request, "website/home.html", context)


@cache_page(60 * 60)
def about_page(request):
    context = {
        "company_name": "Caromoto Lithuania",
    }
    return render(request, "website/about.html", context)


@cache_page(60 * 60)
def services_page(request):
    return render(request, "website/services.html")


@cache_page(60 * 60)
def contact_page(request):
    return render(request, "website/contact.html")


@cache_page(60 * 15)
def news_list(request):
    news = NewsPost.objects.filter(published=True).order_by("-published_at")
    return render(request, "website/news_list.html", {"news": news})


def news_detail(request, slug):
    """Детальная страница новости.

    Увеличиваем счётчик просмотров атомарным UPDATE на уровне БД, чтобы
    параллельные запросы не теряли инкременты. Локально для рендера
    тоже подбиваем ``post.views += 1`` — иначе шаблон покажет старое
    значение в текущем ответе.
    """
    post = get_object_or_404(NewsPost, slug=slug, published=True)
    NewsPost.objects.filter(pk=post.pk).update(views=F("views") + 1)
    post.views += 1
    return render(request, "website/news_detail.html", {"post": post})


@never_cache
@require_GET
def set_site_language(request):
    """Смена языка сайта без CSRF.

    Главная, «О нас» и другие публичные страницы кэшируются ``@cache_page``,
    поэтому в HTML попадает чужой или протухший csrf-токен. POST на
    ``/i18n/setlang/`` из такого кэша даёт 403 «Форма устарела».
    GET-ссылка CSRF не требует: смена языка не меняет данные пользователя.
    """
    lang_code = (request.GET.get("language") or "").strip()
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"
    if not (lang_code and check_for_language(lang_code)):
        return HttpResponseRedirect(next_url)

    translation.activate(lang_code)
    response = HttpResponseRedirect(next_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        lang_code,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response
