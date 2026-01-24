from django.core.management.base import BaseCommand
from django.conf import settings

from core.services.ai_rag import build_rag_index, get_default_rag_sources, is_rag_index_stale


class Command(BaseCommand):
    help = "Rebuild AI RAG index only if it is stale"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-embeddings",
            action="store_true",
            help="Build index without embeddings (keyword-only fallback)",
        )

    def handle(self, *args, **options):
        max_age_hours = int(getattr(settings, "AI_RAG_MAX_AGE_HOURS", 24))
        max_age_seconds = max_age_hours * 3600 if max_age_hours else None
        sources = get_default_rag_sources()
        stale_info = is_rag_index_stale(sources, max_age_seconds=max_age_seconds)
        if stale_info.get("stale") != "true":
            self.stdout.write(self.style.SUCCESS("AI index is fresh, rebuild not required."))
            return

        use_embeddings = not options.get("no_embeddings", False)
        output_path = build_rag_index(sources, use_embeddings=use_embeddings)
        mode = "embeddings" if use_embeddings else "keyword-only"
        reason = stale_info.get("reason", "unknown")
        self.stdout.write(self.style.SUCCESS(
            f"AI index rebuilt ({mode}) due to {reason}: {output_path}"
        ))
