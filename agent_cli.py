#!/usr/bin/env python3
"""
Agent CLI — agent IA agentique en ligne de commande basé sur Ollama,
avec système de skills façon Claude Code.

Usage:
    python agent_cli.py [--model qwen3] [--skills-dir skills]
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from agent.core import Agent
from agent.skills_manager import discover_skills
from agent.ollama_utils import list_ollama_models

BANNER = r"""
+==========================================+
|        Agent CLI (Ollama + Skills)        |
+==========================================+
Tapez /help pour la liste des commandes.
"""

HELP = """
Commandes disponibles :
  /help              Affiche cette aide
  /skills            Liste les skills détectées et leur statut
  /enable <nom>      Active une skill
  /disable <nom>     Désactive une skill
  /reset             Réinitialise la conversation
  /model             Interroge Ollama et liste les modèles installés (numérotés)
  /model <nom>       Change directement le modèle utilisé
  /system            Édite le prompt système (ouvre $EDITOR, ou saisie multi-ligne)
  /host              Affiche/configure le serveur Ollama (local ou distant)
  /thinking on|off   Active/désactive l'affichage du raisonnement du modèle
  /workspace <chemin> Affiche ou change le dossier de sauvegarde des fichiers
  /autosave on|off   Active/désactive la sauvegarde automatique des blocs de code
  /exit ou /quit     Quitte le programme
"""


def main():
    parser = argparse.ArgumentParser(description="Agent CLI agentique basé sur Ollama")
    parser.add_argument("--model", default="qwen3", help="Nom du modèle Ollama à utiliser")
    parser.add_argument(
        "--skills-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills"),
        help="Dossier contenant les skills",
    )
    parser.add_argument(
        "--confirm-tools",
        default="run_command,write_file",
        help=(
            "Liste (séparée par des virgules) des noms d'outils nécessitant "
            "une confirmation utilisateur avant exécution"
        ),
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Ne pas afficher le raisonnement interne du modèle",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Dossier racine où sauvegarder les fichiers (manuellement ou automatiquement)",
    )
    parser.add_argument(
        "--no-auto-save",
        action="store_true",
        help="Désactive la sauvegarde automatique des blocs de code nommés détectés dans les réponses",
    )
    parser.add_argument(
        "--system-file",
        default=None,
        help="Fichier texte contenant le prompt système à utiliser (sinon, prompt par défaut généré depuis les skills)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "URL du serveur Ollama (ex: http://192.168.1.50:11434 pour un serveur "
            "distant). Par défaut : variable d'environnement OLLAMA_HOST, sinon "
            "http://localhost:11434"
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Clé API / jeton envoyé en en-tête Authorization: Bearer ... (pour un serveur Ollama distant protégé)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout réseau en secondes pour les requêtes vers le serveur Ollama",
    )
    args = parser.parse_args()

    skills = discover_skills(args.skills_dir)
    confirm_tools = {t.strip() for t in args.confirm_tools.split(",") if t.strip()}

    system_text = None
    if args.system_file:
        with open(args.system_file, "r", encoding="utf-8") as f:
            system_text = f.read().strip()

    agent = Agent(
        model=args.model,
        skills=skills,
        show_thinking=not args.no_thinking,
        confirm_tools=confirm_tools,
        workspace_dir=args.workspace,
        auto_save_code=not args.no_auto_save,
        system=system_text,
        host=args.host,
        api_key=args.api_key,
        timeout=args.timeout,
    )
    agent.add_system_prompt()

    print(BANNER)
    print(f"Serveur Ollama : {agent.effective_host_label()}")
    print(f"Modèle : {agent.model}\n")
    if not skills:
        print(f"⚠ Aucune skill détectée dans {args.skills_dir}")
    else:
        print("Skills chargées :")
        for s in skills:
            status = "✅" if s.enabled else "⛔"
            print(f"  {status} {s.name} — {s.description} ({len(s.functions)} outil(s))")
    print()

    while True:
        try:
            user_input = input("\033[92mVous:\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAu revoir !")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            should_exit = handle_command(user_input, agent)
            if should_exit:
                break
            continue

        try:
            run_turn_interactive(agent, user_input)
        except Exception as exc:
            print(f"\033[91mErreur: {exc}\033[0m")


def run_turn_interactive(agent: Agent, user_input: str) -> str:
    """Consomme agent.run_turn_events en affichant chaque évènement et en
    demandant une vraie confirmation (input()) quand l'agent le requiert."""
    final_text = ""
    gen = agent.run_turn_events(user_input)
    send_value = None
    try:
        while True:
            event = gen.send(send_value) if send_value is not None else next(gen)
            send_value = None
            etype = event["type"]

            if etype == "thinking":
                print(f"\033[2m[réflexion] {event['text']}\033[0m")
            elif etype == "content":
                final_text = event["text"]
                print(f"\033[96mAssistant:\033[0m {final_text}")
            elif etype == "auto_save_detected":
                print(f"\033[95m[auto-save] fichier détecté : {event['name']}\033[0m")
            elif etype == "tool_call":
                print(f"\033[90m→ appel {event['name']}({event['args']})\033[0m")
            elif etype == "tool_result":
                print(f"\033[90m← résultat: {event['result']}\033[0m")
            elif etype == "tool_denied":
                print(f"\033[91mExécution de '{event['name']}' refusée.\033[0m")
            elif etype == "tool_error":
                print(f"\033[91m{event['text']}\033[0m")
            elif etype == "connection_error":
                print(f"\033[91m{event['text']}\033[0m")
                print("\033[2mAstuce : /host pour vérifier ou changer le serveur Ollama.\033[0m")
            elif etype == "tool_confirm":
                answer = input(
                    f"\033[93m⚠ L'agent veut exécuter '{event['name']}' avec "
                    f"{event['args']}. Confirmer ? (o/N) \033[0m"
                )
                send_value = answer.strip().lower() in ("o", "oui", "y", "yes")
    except StopIteration:
        pass
    return final_text



def handle_command(cmd: str, agent: Agent) -> bool:
    """Traite une commande /xxx. Retourne True si le programme doit s'arrêter."""
    parts = cmd.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name == "/help":
        print(HELP)
    elif name == "/skills":
        for s in agent.skills:
            status = "activée ✅" if s.enabled else "désactivée ⛔"
            print(f"- {s.name} ({status}): {s.description}")
            for fname in s.functions:
                print(f"    • {fname}")
    elif name == "/enable":
        _toggle_skill(agent, arg, True)
    elif name == "/disable":
        _toggle_skill(agent, arg, False)
    elif name == "/reset":
        agent.reset()
        agent.add_system_prompt()
        print("Conversation réinitialisée.")
    elif name == "/model":
        if arg:
            agent.model = arg
            print(f"Modèle changé pour : {arg}")
        else:
            _pick_model_interactive(agent)
    elif name == "/system":
        _edit_system_prompt_interactive(agent)
    elif name == "/host":
        _configure_host_interactive(agent, arg)
    elif name == "/thinking":
        if arg.lower() in ("on", "off"):
            agent.show_thinking = arg.lower() == "on"
            print(f"Affichage du raisonnement : {agent.show_thinking}")
        else:
            print("Usage: /thinking on|off")
    elif name == "/workspace":
        if arg:
            agent.workspace_dir = arg
            print(f"Dossier de travail changé pour : {arg}")
        else:
            print(f"Dossier de travail actuel : {agent.workspace_dir}")
    elif name == "/autosave":
        if arg.lower() in ("on", "off"):
            agent.auto_save_code = arg.lower() == "on"
            print(f"Sauvegarde automatique des blocs de code : {agent.auto_save_code}")
        else:
            print("Usage: /autosave on|off")
    elif name in ("/exit", "/quit"):
        print("Au revoir !")
        return True
    else:
        print(f"Commande inconnue : {name}. Tapez /help.")
    return False


def _pick_model_interactive(agent: Agent) -> None:
    """Interroge Ollama pour lister les modèles installés et laisse
    l'utilisateur en choisir un par numéro."""
    print("Recherche des modèles Ollama disponibles...")
    try:
        models = list_ollama_models(client=agent.client)
    except Exception as exc:
        print(
            f"\033[91mImpossible d'interroger Ollama ({exc}). "
            "Vérifiez que le serveur Ollama tourne bien localement.\033[0m"
        )
        return

    if not models:
        print("\033[93mAucun modèle installé. Utilisez `ollama pull <modele>` puis réessayez.\033[0m")
        return

    print(f"Modèle actuel : {agent.model}\n")
    for i, m in enumerate(models, start=1):
        details = [d for d in (m["parameter_size"], m["quantization_level"], m["size_human"]) if d]
        suffix = f" ({', '.join(details)})" if details else ""
        marker = "→" if m["name"] == agent.model else " "
        print(f" {marker} [{i}] {m['name']}{suffix}")

    choice = input("\nChoisissez un numéro (Entrée pour annuler) : ").strip()
    if not choice:
        print("Annulé.")
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(models)):
        print("Choix invalide.")
        return

    selected = models[int(choice) - 1]["name"]
    agent.model = selected
    print(f"Modèle changé pour : {selected}")


def _configure_host_interactive(agent: Agent, arg: str) -> None:
    """Affiche/configure le serveur Ollama (local ou distant). Avec un
    argument direct (`/host <url>` ou `/host local`), applique tout de
    suite ; sans argument, guide l'utilisateur pas à pas (hôte, clé API
    optionnelle, timeout optionnel, test de connexion optionnel)."""
    print(f"Serveur Ollama actuel : {agent.effective_host_label()}")

    if arg:
        if arg.lower() in ("local", "localhost", "default"):
            agent.configure_host(None)
            print("Serveur remis en local (par défaut).")
        else:
            agent.configure_host(arg)
            print(f"Serveur changé pour : {agent.effective_host_label()}")
        return

    new_host = input(
        "Nouvel hôte (ex: http://192.168.1.50:11434 ; 'local' pour revenir au "
        "défaut ; Entrée pour ne rien changer) : "
    ).strip()
    if not new_host:
        print("Inchangé.")
        return
    if new_host.lower() in ("local", "localhost", "default"):
        agent.configure_host(None)
        print("Serveur remis en local (par défaut).")
        return

    api_key = input("Clé API / jeton (optionnel, Entrée pour aucun) : ").strip() or None
    timeout_str = input("Timeout en secondes (optionnel, Entrée pour aucun) : ").strip()
    timeout: Optional[float] = None
    if timeout_str:
        try:
            timeout = float(timeout_str)
        except ValueError:
            print("Timeout invalide, ignoré.")

    agent.configure_host(new_host, api_key=api_key, timeout=timeout)
    print(f"Serveur changé pour : {agent.effective_host_label()}")

    test = input("Tester la connexion maintenant ? (o/N) ").strip().lower()
    if test in ("o", "oui", "y", "yes"):
        try:
            models = list_ollama_models(client=agent.client)
            print(f"\033[92m✓ Connexion réussie ({len(models)} modèle(s) trouvé(s)).\033[0m")
        except Exception as exc:
            print(f"\033[91m✗ Échec de connexion : {exc}\033[0m")


def _edit_system_prompt_interactive(agent: Agent) -> None:
    """Édite agent.system : ouvre $EDITOR sur un fichier temporaire si
    disponible (comme `git commit -e`), sinon retombe sur une saisie
    multi-ligne au clavier (terminée par une ligne contenant seul ".")."""
    print(f"Prompt système actuel ({len(agent.system)} caractères) :\n")
    print(agent.system)
    print()

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor and sys.stdin.isatty():
        new_text = _edit_with_external_editor(editor, agent.system)
        if new_text is None:
            print("Édition annulée (erreur avec l'éditeur externe).")
            return
    else:
        print(
            "Aucun $EDITOR détecté (ou entrée non interactive) : saisie "
            "multi-ligne. Terminez par une ligne contenant seulement un point (.)\n"
            "Ligne vide pour conserver le prompt actuel."
        )
        lines = []
        try:
            while True:
                line = input()
                if line.strip() == ".":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            print("\nÉdition annulée.")
            return
        new_text = "\n".join(lines).strip()
        if not new_text:
            print("Saisie vide : prompt système inchangé.")
            return

    agent.system = new_text
    agent.add_system_prompt()  # idempotent : met à jour la conversation en cours
    print(f"\nPrompt système mis à jour et appliqué ({len(new_text)} caractères).")


def _edit_with_external_editor(editor: str, initial_text: str) -> Optional[str]:
    """Ouvre `editor` sur un fichier temporaire pré-rempli avec `initial_text`
    et retourne son contenu après fermeture, ou None en cas d'erreur."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(initial_text)
        tmp_path = tmp.name

    try:
        result = subprocess.run([editor, tmp_path])
        if result.returncode != 0:
            return None
        with open(tmp_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as exc:
        print(f"Erreur lors du lancement de l'éditeur '{editor}' : {exc}")
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _toggle_skill(agent: Agent, arg: str, enabled: bool):
    if not arg:
        print("Usage: /enable <nom> ou /disable <nom>")
        return
    found = False
    for s in agent.skills:
        if s.name == arg:
            s.enabled = enabled
            found = True
            print(f"Skill '{arg}' {'activée' if enabled else 'désactivée'}.")
    if not found:
        print(f"Skill '{arg}' introuvable. Tapez /skills pour la liste.")


if __name__ == "__main__":
    sys.exit(main())
