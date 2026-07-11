"""Outils de manipulation de fichiers et répertoires locaux exposés à l'agent."""

import os


def read_file(path: str) -> str:
    """Lit le contenu d'un fichier texte.

    Args:
        path: Chemin du fichier à lire
    Returns:
        Le contenu du fichier sous forme de texte
    """
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """Écrit du texte dans un fichier (le crée ou l'écrase). Crée
    automatiquement les répertoires parents si nécessaire.

    Args:
        path: Chemin du fichier à écrire
        content: Contenu à écrire dans le fichier
    Returns:
        Un message confirmant l'écriture
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Fichier '{path}' écrit ({len(content)} caractères)."


def create_directory(path: str) -> str:
    """Crée un répertoire (et ses répertoires parents si besoin). Ne fait
    rien si le répertoire existe déjà.

    Args:
        path: Chemin du répertoire à créer
    Returns:
        Un message confirmant la création
    """
    os.makedirs(path, exist_ok=True)
    return f"Répertoire '{path}' créé (ou déjà existant)."


def list_directory(path: str = ".") -> str:
    """Liste le contenu d'un dossier.

    Args:
        path: Chemin du dossier à lister (par défaut le dossier courant)
    Returns:
        La liste des fichiers et dossiers, séparés par des retours à la ligne
    """
    entries = os.listdir(path)
    return "\n".join(sorted(entries)) if entries else "(dossier vide)"
