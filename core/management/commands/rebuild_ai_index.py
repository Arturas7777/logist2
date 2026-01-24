from django.core.management.base import BaseCommand

from core.services.ai_rag import build_rag_index, get_default_rag_sources


class Command(BaseCommand):
    help = "Build or rebuild AI RAG index for admin assistant"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-embeddings",
            action="store_true",
            help="Build index without embeddings (keyword-only fallback)",
        )

    def handle(self, *args, **options):
        use_embeddings = not options.get("no_embeddings", False)
        sources = get_default_rag_sources()
        output_path = build_rag_index(sources, use_embeddings=use_embeddings)
        mode = "embeddings" if use_embeddings else "keyword-only"
        self.stdout.write(self.style.SUCCESS(f"AI index built ({mode}): {output_path}"))
