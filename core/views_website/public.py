"""Публичные страницы сайта (home, about, services, contact, news)."""

from django.db.models import F
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import cache_page

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
