"""
Cœur de l'agent : boucle de conversation avec Ollama + exécution d'outils.

La logique est exposée sous forme d'un générateur d'évènements
(`run_turn_events`) plutôt que de faire des `print()` directement.
Cela permet de brancher n'importe quelle interface par-dessus (CLI texte,
TUI Textual, future interface web...) sans dupliquer la boucle agentique.

Évènements yieldés (dict) :
    {"type": "thinking", "text": str}
    {"type": "content", "text": str}
    {"type": "tool_call", "name": str, "args": dict}
    {"type": "tool_result", "name": str, "result": str}
    {"type": "tool_denied", "name": str}
    {"type": "tool_error", "name": str, "text": str}
    {"type": "connection_error", "text": str}
    {"type": "auto_save_detected", "name": str, "language": str}
    {"type": "done"}

Cas particulier — confirmation d'un outil sensible :
    {"type": "tool_confirm", "name": str, "args": dict}
Ce type d'évènement met le générateur en pause : l'appelant DOIT reprendre
l'itération avec `gen.send(True_ou_False)` (et non `next(gen)`) pour
indiquer si l'exécution est autorisée.

Sauvegarde automatique des blocs de code
-----------------------------------------
Quand le modèle répond avec du texte contenant un bloc ``` ``` dont on
peut déduire un nom de fichier (indiqué avant, ou dans, le bloc), l'agent
appelle automatiquement l'outil `write_file` de la skill filesystem pour
sauvegarder ce contenu sur disque — avec la même confirmation de sécurité
que n'importe quel autre appel d'outil sensible.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, Iterator, List, Optional, Set

import ollama
from ollama import ChatResponse

# ----------------------------------------------------------------------
# Détection des blocs de code + nom de fichier associé
# ----------------------------------------------------------------------

# Un bloc de code markdown : ```lang infos-optionnelles\n...contenu...\n```
_CODE_BLOCK_RE = re.compile(r"```([\w+-]*)[ \t]*([^\n]*)\n(.*?)```", re.DOTALL)

# Un nom de fichier plausible : au moins un caractère, un point, une extension
_FILENAME_RE = re.compile(r"[`*_\"']{0,2}([\w./\\-]+\.[A-Za-z0-9]{1,10})[`*_\"']{0,2}")

# Ligne de commentaire en première ligne d'un bloc de code (# fichier.py, // a.js, <!-- x.html -->)
_COMMENT_FILENAME_RE = re.compile(r"^(?:#|//|--|<!--)\s*([\w./\\-]+\.[A-Za-z0-9]{1,10})")


def extract_code_blocks_with_filenames(text: str) -> List[dict]:
    """Retourne, pour chaque bloc de code markdown trouvé dans `text`, un
    dict {"filename": str|None, "language": str, "code": str}.

    La détection du nom de fichier essaie, dans l'ordre :
      1. les infos sur la ligne d'ouverture du bloc (ex: ```python app.py)
      2. la ligne de texte juste avant le bloc (ex: "Fichier : app.py" ou "`app.py` :")
      3. un commentaire en première ligne du bloc (ex: "# app.py")
    """
    results = []
    for match in _CODE_BLOCK_RE.finditer(text):
        language = match.group(1) or ""
        info_rest = match.group(2).strip()
        code = match.group(3)
        start = match.start()

        filename = None

        if info_rest:
            m = _FILENAME_RE.search(info_rest)
            if m:
                filename = m.group(1)

        if not filename:
            preceding = text[:start].rstrip("\n")
            prev_line = preceding.splitlines()[-1] if preceding.strip() else ""
            m = _FILENAME_RE.search(prev_line)
            if m:
                filename = m.group(1)

        if not filename:
            code_lines = code.splitlines()
            first_line = code_lines[0].strip() if code_lines else ""
            m = _COMMENT_FILENAME_RE.match(first_line)
            if m:
                filename = m.group(1)
                code = "\n".join(code_lines[1:])  # on retire la ligne de commentaire

        results.append(
            {"filename": filename, "language": language, "code": code.strip("\n")}
        )
    return results


class Agent:
    def __init__(
        self,
        model: str,
        skills: list,
        show_thinking: bool = True,
        confirm_tools: Optional[Set[str]] = None,
        workspace_dir: str = ".",
        auto_save_code: bool = True,
        system: Optional[str] = None,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.model = model
        self.skills = skills
        self.show_thinking = show_thinking
        # Noms d'outils nécessitant une confirmation utilisateur avant exécution
        # (ex: shell, écriture de fichiers...).
        self.confirm_tools = confirm_tools or set()
        # Dossier racine dans lequel les fichiers auto-détectés sont sauvegardés.
        self.workspace_dir = workspace_dir
        # Active/désactive la sauvegarde automatique des blocs de code détectés.
        self.auto_save_code = auto_save_code
        # Prompt système (rôle "system") réellement envoyé au modèle. Éditable
        # à tout moment par l'utilisateur (ex: via un dialogue dans le TUI).
        # Si aucun texte n'est fourni, un prompt par défaut est généré à
        # partir des skills actives.
        self.system: str = system if system is not None else self.default_system_prompt()
        self.messages: List[dict] = []

        # Connexion au serveur Ollama — local par défaut (http://localhost:11434
        # ou variable d'environnement OLLAMA_HOST), ou distant si `host` est
        # fourni. Reconfigurable à tout moment via configure_host().
        self.host: Optional[str] = host
        self.api_key: Optional[str] = api_key
        self.timeout: Optional[float] = timeout
        self.client: ollama.Client = self._build_client()

    # ------------------------------------------------------------------
    def _build_client(self) -> ollama.Client:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        return ollama.Client(host=self.host, headers=headers, timeout=self.timeout)

    def configure_host(
        self,
        host: Optional[str],
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Reconfigure la connexion au serveur Ollama (local ou distant) et
        recrée le client sous-jacent. `host=None` revient au comportement
        par défaut du client ollama (variable d'environnement OLLAMA_HOST,
        sinon http://localhost:11434)."""
        self.host = host or None
        self.api_key = api_key or None
        self.timeout = timeout
        self.client = self._build_client()

    def effective_host_label(self) -> str:
        """Libellé lisible du serveur actuellement configuré, pour affichage."""
        if self.host:
            return self.host
        env_host = os.environ.get("OLLAMA_HOST")
        return env_host if env_host else "http://localhost:11434 (par défaut)"

    # ------------------------------------------------------------------
    def enabled_skills(self):
        return [s for s in self.skills if s.enabled]

    def available_functions(self) -> Dict[str, Callable]:
        funcs: Dict[str, Callable] = {}
        for skill in self.enabled_skills():
            funcs.update(skill.functions)
        return funcs

    def tool_list(self):
        tools = []
        for skill in self.enabled_skills():
            tools.extend(skill.tool_list)
        return tools

    def reset(self):
        self.messages = []

    def default_system_prompt(self) -> str:
        """Construit le prompt système par défaut à partir des skills
        actuellement activées. N'affecte pas self.system : sert de base
        à afficher/réinitialiser dans un éditeur de prompt système."""
        skill_desc = "\n".join(
            f"- {s.name}: {s.description}" for s in self.enabled_skills()
        ) or "(aucune skill activée)"
        return (
            "Tu es un assistant IA agentique. Tu disposes des skills "
            "(compétences) suivantes, chacune apportant des outils que tu "
            "peux appeler :\n"
            f"{skill_desc}\n\n"
            "Utilise les outils disponibles chaque fois que c'est pertinent "
            "plutôt que de deviner un résultat (calculs, lecture/écriture de "
            "fichiers, création de répertoires, etc.).\n\n"
            "Quand tu écris le contenu complet d'un fichier dans un bloc de "
            "code, indique clairement son chemin juste avant le bloc, par "
            "exemple : \"Fichier : app.py\" suivi de ```python ... ```. "
            "Le fichier sera alors automatiquement sauvegardé sur disque."
        )

    def add_system_prompt(self):
        """Insère self.system comme message système en tête de la
        conversation. Si un message système est déjà présent en première
        position (ex: après une modification de self.system en cours de
        conversation), il est remplacé plutôt que dupliqué — idempotent,
        utilisable aussi bien au démarrage qu'après une édition du prompt."""
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self.system
        else:
            self.messages.insert(0, {"role": "system", "content": self.system})

    # ------------------------------------------------------------------
    def _resolve_workspace_path(self, filename: str) -> Optional[str]:
        """Résout `filename` (potentiellement relatif) sous `workspace_dir`
        et vérifie qu'il ne s'en échappe pas (protection contre ../../).
        Retourne None si le chemin est jugé non sûr."""
        base = os.path.abspath(self.workspace_dir)
        target = os.path.abspath(os.path.join(base, filename))
        if target != base and not target.startswith(base + os.sep):
            return None
        return target

    def _auto_save_blocks(self, text: str, available_functions: Dict[str, Callable]):
        """Générateur d'évènements : détecte les blocs de code nommés dans
        `text` et les sauvegarde via l'outil write_file (si la skill
        filesystem est active). Peut yield des "tool_confirm" (à envoyer
        via .send() comme pour un tool_call classique)."""
        write_file = available_functions.get("write_file")
        if write_file is None:
            return  # skill filesystem désactivée : rien à faire

        for block in extract_code_blocks_with_filenames(text):
            filename = block["filename"]
            if not filename:
                continue

            safe_path = self._resolve_workspace_path(filename)
            if safe_path is None:
                yield {
                    "type": "tool_error",
                    "name": "write_file",
                    "text": f"Chemin non autorisé (hors de l'espace de travail) : {filename}",
                }
                continue

            yield {"type": "auto_save_detected", "name": filename, "language": block["language"]}

            if "write_file" in self.confirm_tools:
                allowed = yield {
                    "type": "tool_confirm",
                    "name": "write_file",
                    "args": {"path": safe_path},
                }
                if not allowed:
                    yield {"type": "tool_denied", "name": "write_file"}
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_name": "write_file",
                            "content": f"[auto-save] Écriture de '{safe_path}' refusée par l'utilisateur.",
                        }
                    )
                    continue

            # On n'affiche pas le contenu complet dans l'évènement (peut être long)
            yield {"type": "tool_call", "name": "write_file", "args": {"path": safe_path}}
            try:
                result = write_file(path=safe_path, content=block["code"])
            except Exception as exc:
                result = f"Erreur lors de l'écriture: {exc}"
            yield {"type": "tool_result", "name": "write_file", "result": result}

            self.messages.append(
                {
                    "role": "tool",
                    "tool_name": "write_file",
                    "content": f"[auto-save] {result}",
                }
            )

    # ------------------------------------------------------------------
    def run_turn_events(self, user_input: str) -> Iterator[dict]:
        """Générateur d'évènements pour un tour de conversation complet.

        L'appelant itère avec `next()`, sauf sur un évènement de type
        "tool_confirm" où il doit répondre avec `gen.send(bool)`.
        """
        self.messages.append({"role": "user", "content": user_input})
        available_functions = self.available_functions()

        while True:
            try:
                response: ChatResponse = self.client.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=self.tool_list(),
                    think=True,
                )
            except Exception as exc:
                yield {
                    "type": "connection_error",
                    "text": f"Impossible de contacter le serveur Ollama ({self.effective_host_label()}) : {exc}",
                }
                return
            self.messages.append(response.message)

            thinking = getattr(response.message, "thinking", None)
            if self.show_thinking and thinking:
                yield {"type": "thinking", "text": thinking}

            if response.message.content:
                yield {"type": "content", "text": response.message.content}
                if self.auto_save_code:
                    yield from self._auto_save_blocks(response.message.content, available_functions)

            if response.message.tool_calls:
                for tc in response.message.tool_calls:
                    fname = tc.function.name
                    fargs = tc.function.arguments

                    if fname not in available_functions:
                        text = f"Outil inconnu '{fname}'"
                        yield {"type": "tool_error", "name": fname, "text": text}
                        self.messages.append(
                            {"role": "tool", "tool_name": fname, "content": f"Erreur: {text}"}
                        )
                        continue

                    if fname in self.confirm_tools:
                        allowed = yield {"type": "tool_confirm", "name": fname, "args": fargs}
                        if not allowed:
                            result = "Exécution refusée par l'utilisateur."
                            yield {"type": "tool_denied", "name": fname}
                            self.messages.append(
                                {"role": "tool", "tool_name": fname, "content": result}
                            )
                            continue

                    yield {"type": "tool_call", "name": fname, "args": fargs}
                    try:
                        result = available_functions[fname](**fargs)
                    except Exception as exc:  # on renvoie l'erreur au modèle
                        result = f"Erreur lors de l'exécution: {exc}"
                    yield {"type": "tool_result", "name": fname, "result": result}

                    self.messages.append(
                        {"role": "tool", "tool_name": fname, "content": str(result)}
                    )
                # On reboucle pour laisser le modèle exploiter les résultats
                continue
            else:
                break

        yield {"type": "done"}

    # ------------------------------------------------------------------
    def run_turn(self, user_input: str) -> str:
        """Variante bloquante pour usage simple (ex: scripts, tests) :
        consomme le générateur, affiche via print() et renvoie le texte final.
        Répond automatiquement "non" à toute demande de confirmation
        (utiliser run_turn_events directement si une vraie confirmation
        interactive est nécessaire, comme le fait agent_cli.py)."""
        final_text = ""
        gen = self.run_turn_events(user_input)
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
                elif etype == "tool_confirm":
                    send_value = False  # confirmation auto-refusée en mode non interactif
                elif etype == "tool_denied":
                    print(f"\033[91mExécution de '{event['name']}' refusée.\033[0m")
                elif etype == "tool_error":
                    print(f"\033[91m{event['text']}\033[0m")
                elif etype == "connection_error":
                    print(f"\033[91m{event['text']}\033[0m")
        except StopIteration:
            pass
        return final_text
