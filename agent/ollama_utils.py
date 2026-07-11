"""
Utilitaires pour interroger le serveur Ollama local — ici, la liste des
modèles déjà installés (`ollama list` / `ollama pull ...`).
"""

from __future__ import annotations

from typing import List, Optional, TypedDict

import ollama


class ModelInfo(TypedDict):
    name: str
    parameter_size: Optional[str]
    quantization_level: Optional[str]
    size_human: Optional[str]


def _human_size(num_bytes: float) -> str:
    """Convertit un nombre d'octets en chaîne lisible (Ko, Mo, Go...)."""
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "o" else f"{int(size)}{unit}"
        size /= 1024
    return f"{size:.1f}Po"


def list_ollama_models() -> List[ModelInfo]:
    """Interroge le serveur Ollama local et retourne la liste des modèles
    installés, triée par nom.

    Peut lever une exception (ex: `ConnectionError`) si le serveur Ollama
    n'est pas joignable — à l'appelant de gérer ce cas (afficher un message
    d'erreur clair plutôt qu'une trace complète).
    """
    response = ollama.list()
    models: List[ModelInfo] = []
    for m in response.models:
        details = m.details
        size_human = _human_size(m.size) if m.size is not None else None
        models.append(
            {
                "name": m.model or "(sans nom)",
                "parameter_size": details.parameter_size if details else None,
                "quantization_level": details.quantization_level if details else None,
                "size_human": size_human,
            }
        )
    models.sort(key=lambda d: d["name"])
    return models
