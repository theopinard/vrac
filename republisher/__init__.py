"""Shared infrastructure for the static article republisher."""

from .catalog import ArticleSpec
from .rendering import GeneratedArticle

__all__ = ["ArticleSpec", "GeneratedArticle"]
