"""Outil d'exécution de commandes shell (soumis à confirmation dans le CLI)."""

import subprocess


def run_command(command: str) -> str:
    """Exécute une commande shell et retourne sa sortie.

    Args:
        command: La commande shell à exécuter
    Returns:
        La sortie standard (et erreur) de la commande
    """
    try:
        completed = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = completed.stdout
        if completed.stderr:
            output += "\n[stderr] " + completed.stderr
        return output.strip() or "(aucune sortie)"
    except subprocess.TimeoutExpired:
        return "Erreur: la commande a dépassé le délai de 30 secondes."
