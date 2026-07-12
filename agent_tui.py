#!/usr/bin/env python3
"""
Agent TUI — interface texte (Textual) pour l'agent agentique Ollama.

Reprend exactement la même logique agentique que agent_cli.py
(agent/core.py, agent/skills_manager.py), mais avec :
  - une zone de chat scrollable,
  - un panneau latéral listant les skills (clic/Entrée pour activer/désactiver),
  - une boîte de dialogue modale pour confirmer les outils sensibles,
  - un champ de saisie en bas.

Comme `ollama.chat()` est bloquant, chaque tour de conversation est exécuté
dans un worker thread Textual ; les mises à jour d'interface sont renvoyées
sur le thread principal via `call_from_thread`.

Usage:
    python agent_tui.py [--model qwen3] [--skills-dir skills]
"""

from __future__ import annotations

import argparse
import os
import re
import threading
from functools import partial
from typing import List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RichLog,
    TextArea,
)
from textual.widgets.option_list import Option

from agent.core import Agent
from agent.skills_manager import discover_skills
from agent.ollama_utils import list_ollama_models

# Commandes disponibles, utilisées à la fois par /help et par la combobox
# de complétion qui s'affiche quand l'utilisateur tape "/" dans l'invite.
COMMANDS: List[Tuple[str, str]] = [
    ("/help", "Affiche l'aide"),
    ("/reset", "Réinitialise la conversation"),
    ("/model", "Ouvre le sélecteur de modèle (interroge Ollama)"),
    ("/system", "Ouvre l'éditeur du prompt système (rôle 'system')"),
    ("/host", "Ouvre la configuration du serveur Ollama (local ou distant)"),
    ("/thinking", "Active/désactive l'affichage du raisonnement (on|off)"),
    ("/workspace", "Change le dossier de sauvegarde des fichiers"),
    ("/autosave", "Active/désactive la sauvegarde automatique des blocs de code (on|off)"),
    ("/exit", "Quitte l'application"),
    ("/quit", "Quitte l'application"),
]

HELP_TEXT = """[bold]Commandes :[/bold]
  /help              Affiche cette aide
  /reset             Réinitialise la conversation
  /model             Ouvre le sélecteur de modèle (interroge Ollama)
  /model <nom>       Change directement le modèle utilisé
  /system            Ouvre l'éditeur du prompt système (rôle "system")
  /host              Ouvre la configuration du serveur Ollama (local/distant)
  /thinking on|off   Active/désactive l'affichage du raisonnement
  /workspace <chemin> Affiche ou change le dossier de sauvegarde des fichiers
  /autosave on|off   Active/désactive la sauvegarde automatique des blocs de code
  /exit, /quit       Quitte l'application

[bold]Astuce :[/bold] cliquez (ou Entrée) sur une skill dans le panneau de
gauche pour l'activer / la désactiver. Ctrl+M ouvre le sélecteur de modèle,
Ctrl+S ouvre l'éditeur du prompt système, Ctrl+O configure le serveur Ollama.
Tapez "/" dans l'invite pour voir la liste des commandes ; flèches ↑/↓ pour
naviguer dans les suggestions (ou dans l'historique des messages envoyés)."""


class ConfirmModal(ModalScreen[bool]):
    """Boîte de dialogue modale demandant confirmation avant un appel d'outil sensible."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #confirm-dialog Label {
        width: 100%;
        content-align: center middle;
        padding-bottom: 1;
    }
    #confirm-buttons {
        align: center middle;
        height: auto;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, tool_name: str, tool_args: dict):
        super().__init__()
        self.tool_name = tool_name
        self.tool_args = tool_args

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(
                f"⚠ L'agent veut exécuter '{self.tool_name}' avec {self.tool_args}"
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Confirmer", id="yes", variant="error")
                yield Button("Annuler", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ModelPickerModal(ModalScreen[Optional[str]]):
    """Boîte de dialogue modale listant les modèles Ollama installés,
    récupérés via `ollama.list()`, pour en choisir un rapidement."""

    DEFAULT_CSS = """
    ModelPickerModal {
        align: center middle;
    }
    #model-picker-dialog {
        width: 74;
        height: auto;
        max-height: 26;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #model-picker-dialog > Label {
        padding-bottom: 1;
        text-style: bold;
    }
    #model-list {
        height: auto;
        max-height: 16;
    }
    #model-picker-buttons {
        align: center middle;
        height: auto;
        padding-top: 1;
    }
    """

    def __init__(self, models: list, current_model: str):
        super().__init__()
        self.models = models
        self.current_model = current_model

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-dialog"):
            yield Label("Choisissez un modèle Ollama (modèles installés localement)")
            yield ListView(id="model-list")
            with Horizontal(id="model-picker-buttons"):
                yield Button("Annuler", id="cancel", variant="primary")

    def on_mount(self) -> None:
        list_view = self.query_one("#model-list", ListView)
        for m in self.models:
            marker = "→ " if m["name"] == self.current_model else "  "
            details = [
                d
                for d in (m.get("parameter_size"), m.get("quantization_level"), m.get("size_human"))
                if d
            ]
            suffix = f"  ({', '.join(details)})" if details else ""
            item = ListItem(Label(f"{marker}{m['name']}{suffix}"))
            item.model_name = m["name"]
            list_view.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(getattr(event.item, "model_name", None))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)


class SystemPromptModal(ModalScreen[Optional[str]]):
    """Dialogue d'édition du prompt système (rôle "system") utilisé par
    l'agent. Pré-rempli avec le texte actuel de `agent.system` ; permet de
    l'éditer librement, de revenir au prompt par défaut généré à partir des
    skills actives, d'enregistrer ou d'annuler."""

    DEFAULT_CSS = """
    SystemPromptModal {
        align: center middle;
    }
    #system-prompt-dialog {
        width: 90%;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #system-prompt-dialog > Label {
        text-style: bold;
        padding-bottom: 1;
    }
    #system-prompt-editor {
        height: 1fr;
        border: solid $accent;
    }
    #system-prompt-buttons {
        align: center middle;
        height: auto;
        padding-top: 1;
    }
    #system-prompt-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, current_text: str, default_text: str):
        super().__init__()
        self.current_text = current_text
        self.default_text = default_text

    def compose(self) -> ComposeResult:
        with Vertical(id="system-prompt-dialog"):
            yield Label("Prompt système (rôle \"system\") envoyé au modèle")
            yield TextArea(self.current_text, id="system-prompt-editor", soft_wrap=True)
            with Horizontal(id="system-prompt-buttons"):
                yield Button("Enregistrer", id="save", variant="success")
                yield Button("Réinitialiser au défaut", id="default", variant="warning")
                yield Button("Annuler", id="cancel", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#system-prompt-editor", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        editor = self.query_one("#system-prompt-editor", TextArea)
        if event.button.id == "save":
            self.dismiss(editor.text)
        elif event.button.id == "default":
            editor.text = self.default_text
            editor.focus()
        elif event.button.id == "cancel":
            self.dismiss(None)


class HostConfigModal(ModalScreen[Optional[dict]]):
    """Dialogue de configuration du serveur Ollama : hôte local (par
    défaut) ou distant (URL + clé API optionnelle), avec un bouton pour
    tester la connexion avant de valider."""

    DEFAULT_CSS = """
    HostConfigModal {
        align: center middle;
    }
    #host-dialog {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #host-dialog > Label {
        padding-bottom: 1;
    }
    #host-dialog > Label.title {
        text-style: bold;
    }
    #host-status {
        padding: 1 0;
        height: auto;
    }
    #host-buttons {
        align: center middle;
        height: auto;
        padding-top: 1;
    }
    #host-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        current_host: Optional[str],
        current_api_key: Optional[str],
        current_timeout: Optional[float],
    ):
        super().__init__()
        self.current_host = current_host or ""
        self.current_api_key = current_api_key or ""
        self.current_timeout = str(current_timeout) if current_timeout else ""

    def compose(self) -> ComposeResult:
        with Vertical(id="host-dialog"):
            yield Label("Serveur Ollama (local ou distant)", classes="title")
            yield Label("Hôte — ex: http://192.168.1.50:11434 (vide = local par défaut)")
            yield Input(value=self.current_host, placeholder="http://localhost:11434", id="host-input")
            yield Label("Clé API / jeton (optionnel — envoyé en Authorization: Bearer ...)")
            yield Input(value=self.current_api_key, placeholder="(optionnel)", password=True, id="host-apikey-input")
            yield Label("Timeout en secondes (optionnel)")
            yield Input(value=self.current_timeout, placeholder="(optionnel)", id="host-timeout-input")
            yield Label("", id="host-status")
            with Horizontal(id="host-buttons"):
                yield Button("Tester", id="test")
                yield Button("Enregistrer", id="save", variant="success")
                yield Button("Local par défaut", id="local", variant="warning")
                yield Button("Annuler", id="cancel", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#host-input", Input).focus()

    def _collect_values(self):
        host = self.query_one("#host-input", Input).value.strip() or None
        api_key = self.query_one("#host-apikey-input", Input).value.strip() or None
        timeout_str = self.query_one("#host-timeout-input", Input).value.strip()
        timeout: Optional[float] = None
        if timeout_str:
            try:
                timeout = float(timeout_str)
            except ValueError:
                timeout = None
        return host, api_key, timeout

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "local":
            self.query_one("#host-input", Input).value = ""
            self.query_one("#host-apikey-input", Input).value = ""
            self.query_one("#host-timeout-input", Input).value = ""
            self.query_one("#host-status", Label).update("")
        elif event.button.id == "save":
            host, api_key, timeout = self._collect_values()
            self.dismiss({"host": host, "api_key": api_key, "timeout": timeout})
        elif event.button.id == "test":
            self._test_connection()

    def _test_connection(self) -> None:
        host, api_key, timeout = self._collect_values()
        self.query_one("#host-status", Label).update("[dim]Test de connexion en cours...[/dim]")
        self.app.run_worker(
            partial(self._do_test, host, api_key, timeout),
            thread=True,
            exclusive=True,
            group="host_test",
        )

    def _do_test(self, host: Optional[str], api_key: Optional[str], timeout: Optional[float]) -> None:
        import ollama as ollama_module

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        try:
            client = ollama_module.Client(host=host, headers=headers, timeout=timeout or 5.0)
            response = client.list()
            n = len(response.models)
            label = host or "localhost (par défaut)"
            self.app.call_from_thread(
                self._update_status, f"[green]✓ Connexion réussie à {label} ({n} modèle(s))[/green]"
            )
        except Exception as exc:
            self.app.call_from_thread(self._update_status, f"[red]✗ Échec de connexion : {exc}[/red]")

    def _update_status(self, text: str) -> None:
        self.query_one("#host-status", Label).update(text)


class PromptInput(Input):
    """Champ de saisie personnalisé : gère la navigation clavier vers la
    combobox de commandes (/xxx) et l'historique des invites, en déléguant
    la logique à l'App (self.app) qui possède l'état partagé."""

    async def action_submit(self) -> None:
        # Si la combobox de commandes est ouverte, Entrée sélectionne la
        # suggestion surlignée au lieu d'envoyer le message.
        if self.app.command_dropdown_visible:
            self.app.accept_command_suggestion()
            return
        await super().action_submit()

    def action_history_prev(self) -> None:
        self.app.prompt_history_prev()

    def action_history_next(self) -> None:
        self.app.prompt_history_next()

    def action_dismiss_dropdown(self) -> None:
        self.app.hide_command_dropdown()

    BINDINGS = [
        Binding("up", "history_prev", show=False),
        Binding("down", "history_next", show=False),
        Binding("escape", "dismiss_dropdown", show=False),
    ]


class SkillItem(ListItem):
    """Item de liste représentant une skill, avec une référence vers l'objet Skill."""

    def __init__(self, skill):
        self.skill = skill
        super().__init__(Label(self._label_text()))

    def _label_text(self) -> str:
        status = "✅" if self.skill.enabled else "⛔"
        return f"{status} {self.skill.name}"

    def refresh_label(self):
        self.query_one(Label).update(self._label_text())


class AgentTUI(App):
    """Application Textual principale."""

    CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 32;
        border-right: solid $accent;
        padding: 1;
    }
    #sidebar-title {
        text-style: bold;
        padding-bottom: 1;
    }
    #chat-area {
        width: 1fr;
    }
    #chat-log {
        height: 1fr;
        padding: 1;
    }
    #command-list {
        display: none;
        height: auto;
        max-height: 10;
        border: round $accent;
        background: $panel;
    }
    #chat-input {
        dock: bottom;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quitter"),
        Binding("ctrl+r", "do_reset", "Réinitialiser"),
        Binding("ctrl+m", "pick_model", "Changer modèle"),
        Binding("ctrl+s", "edit_system_prompt", "Prompt système"),
        Binding("ctrl+o", "configure_host", "Serveur Ollama"),
    ]

    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent
        # Historique des invites envoyées (messages ET commandes /xxx)
        self._history: List[str] = []
        self._history_index: Optional[int] = None  # None = pas en navigation
        self._draft_before_history: str = ""
        self.command_dropdown_visible: bool = False

    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="sidebar"):
            yield Label("Skills", id="sidebar-title")
            yield ListView(id="skills-list")
        with Vertical(id="chat-area"):
            yield RichLog(id="chat-log", wrap=True, markup=True, highlight=False)
            yield OptionList(id="command-list")
            yield PromptInput(placeholder="Tapez votre message (ou /help)...", id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        self._update_title()
        self._refresh_skills_list()
        log = self.query_one("#chat-log", RichLog)
        log.write("[bold cyan]Agent TUI prêt.[/bold cyan] Tapez /help pour l'aide.")
        log.write(f"[dim]Serveur Ollama : {self.agent.effective_host_label()}[/dim]")
        self.query_one("#chat-input", Input).focus()

    def _update_title(self) -> None:
        self.title = f"Agent TUI — modèle: {self.agent.model}"
        self.sub_title = f"Ollama: {self.agent.effective_host_label()}"

    # ------------------------------------------------------------------
    def _refresh_skills_list(self) -> None:
        list_view = self.query_one("#skills-list", ListView)
        list_view.clear()
        for skill in self.agent.skills:
            list_view.append(SkillItem(skill))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, SkillItem):
            item.skill.enabled = not item.skill.enabled
            item.refresh_label()
            log = self.query_one("#chat-log", RichLog)
            state = "activée" if item.skill.enabled else "désactivée"
            log.write(f"[yellow]Skill '{item.skill.name}' {state}.[/yellow]")

    # ------------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "chat-input":
            self._update_command_dropdown(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self.hide_command_dropdown()
        self._history_index = None
        self._draft_before_history = ""

        if not text:
            return

        # Historique : on évite les doublons consécutifs
        if not self._history or self._history[-1] != text:
            self._history.append(text)

        if text.startswith("/"):
            self._handle_command(text)
            return

        log = self.query_one("#chat-log", RichLog)
        log.write(f"[bold green]Vous:[/bold green] {text}")
        self.run_worker(partial(self._process_turn, text), thread=True, exclusive=True)

    # ------------------------------------------------------------------
    # Combobox de commandes (affichée quand l'invite commence par "/")
    # ------------------------------------------------------------------
    def _update_command_dropdown(self, value: str) -> None:
        match = re.match(r"^/(\w*)$", value)
        if not match:
            self.hide_command_dropdown()
            return

        prefix = match.group(1).lower()
        matches = [c for c in COMMANDS if c[0][1:].lower().startswith(prefix)]
        if not matches:
            self.hide_command_dropdown()
            return

        option_list = self.query_one("#command-list", OptionList)
        option_list.clear_options()
        for cmd_name, description in matches:
            option_list.add_option(Option(f"{cmd_name} — {description}", id=cmd_name))
        option_list.highlighted = 0
        option_list.display = True
        self.command_dropdown_visible = True

    def hide_command_dropdown(self) -> None:
        option_list = self.query_one("#command-list", OptionList)
        option_list.display = False
        self.command_dropdown_visible = False

    def accept_command_suggestion(self) -> None:
        if not self.command_dropdown_visible:
            return
        option_list = self.query_one("#command-list", OptionList)
        index = option_list.highlighted
        if index is None:
            return
        option = option_list.get_option_at_index(index)
        if option is None or option.id is None:
            return

        chat_input = self.query_one("#chat-input", PromptInput)
        chat_input.value = f"{option.id} "
        chat_input.cursor_position = len(chat_input.value)
        self.hide_command_dropdown()
        chat_input.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Sélection à la souris (clic) dans la combobox de commandes.
        if event.option_list.id == "command-list":
            self.accept_command_suggestion()

    # ------------------------------------------------------------------
    # Historique des invites (flèches ↑ / ↓ quand la combobox n'est pas ouverte)
    # ------------------------------------------------------------------
    def prompt_history_prev(self) -> None:
        if self.command_dropdown_visible:
            self.query_one("#command-list", OptionList).action_cursor_up()
            return

        if not self._history:
            return

        chat_input = self.query_one("#chat-input", PromptInput)
        if self._history_index is None:
            self._draft_before_history = chat_input.value
            self._history_index = len(self._history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)

        chat_input.value = self._history[self._history_index]
        chat_input.cursor_position = len(chat_input.value)

    def prompt_history_next(self) -> None:
        if self.command_dropdown_visible:
            self.query_one("#command-list", OptionList).action_cursor_down()
            return

        if self._history_index is None:
            return

        chat_input = self.query_one("#chat-input", PromptInput)
        self._history_index += 1
        if self._history_index >= len(self._history):
            self._history_index = None
            chat_input.value = self._draft_before_history
        else:
            chat_input.value = self._history[self._history_index]
        chat_input.cursor_position = len(chat_input.value)

    def _handle_command(self, cmd: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        parts = cmd.split(maxsplit=1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if name == "/help":
            log.write(HELP_TEXT)
        elif name == "/reset":
            self.action_do_reset()
        elif name == "/model":
            if arg:
                self.agent.model = arg
                self._update_title()
                log.write(f"[yellow]Modèle changé pour : {arg}[/yellow]")
            else:
                self.action_pick_model()
        elif name == "/system":
            self.action_edit_system_prompt()
        elif name == "/host":
            self.action_configure_host()
        elif name == "/thinking":
            if arg.lower() in ("on", "off"):
                self.agent.show_thinking = arg.lower() == "on"
                log.write(f"[yellow]Affichage du raisonnement : {self.agent.show_thinking}[/yellow]")
            else:
                log.write("Usage: /thinking on|off")
        elif name == "/workspace":
            if arg:
                self.agent.workspace_dir = arg
                log.write(f"[yellow]Dossier de travail changé pour : {arg}[/yellow]")
            else:
                log.write(f"Dossier de travail actuel : {self.agent.workspace_dir}")
        elif name == "/autosave":
            if arg.lower() in ("on", "off"):
                self.agent.auto_save_code = arg.lower() == "on"
                log.write(f"[yellow]Sauvegarde automatique des blocs de code : {self.agent.auto_save_code}[/yellow]")
            else:
                log.write("Usage: /autosave on|off")
        elif name in ("/exit", "/quit"):
            self.exit()
        else:
            log.write(f"[red]Commande inconnue : {name}[/red]")

    def action_do_reset(self) -> None:
        self.agent.reset()
        self.agent.add_system_prompt()
        self.query_one("#chat-log", RichLog).write("[yellow]Conversation réinitialisée.[/yellow]")

    # ------------------------------------------------------------------
    # Éditeur du prompt système (rôle "system") de l'agent.
    # ------------------------------------------------------------------
    def action_edit_system_prompt(self) -> None:
        def callback(new_text: Optional[str]) -> None:
            if new_text is not None and new_text != self.agent.system:
                self.agent.system = new_text
                self.agent.add_system_prompt()  # idempotent : remplace le message système existant
                self._log("[yellow]Prompt système mis à jour et appliqué.[/yellow]")

        self.push_screen(
            SystemPromptModal(self.agent.system, self.agent.default_system_prompt()),
            callback,
        )

    # ------------------------------------------------------------------
    # Configuration du serveur Ollama (local ou distant).
    # ------------------------------------------------------------------
    def action_configure_host(self) -> None:
        def callback(result: Optional[dict]) -> None:
            if result is not None:
                self.agent.configure_host(
                    result["host"], api_key=result["api_key"], timeout=result["timeout"]
                )
                self._update_title()
                self._log(
                    f"[yellow]Serveur Ollama configuré : {self.agent.effective_host_label()}[/yellow]"
                )

        self.push_screen(
            HostConfigModal(self.agent.host, self.agent.api_key, self.agent.timeout),
            callback,
        )

    # ------------------------------------------------------------------
    # Sélecteur de modèle : interroge Ollama (ollama.list()) dans un thread
    # worker (appel réseau bloquant), puis affiche un modal de sélection.
    # ------------------------------------------------------------------
    def action_pick_model(self) -> None:
        self._log("[dim]Recherche des modèles Ollama disponibles...[/dim]")
        self.run_worker(self._fetch_models, thread=True, exclusive=False, group="model_fetch")

    def _fetch_models(self) -> None:
        try:
            models = list_ollama_models(client=self.agent.client)
        except Exception as exc:
            self.call_from_thread(
                self._log,
                f"[red]Impossible d'interroger Ollama ({exc}). "
                "Vérifiez que le serveur Ollama tourne bien localement.[/red]",
            )
            return

        if not models:
            self.call_from_thread(
                self._log,
                "[yellow]Aucun modèle installé. Utilisez `ollama pull <modele>` "
                "puis réessayez.[/yellow]",
            )
            return

        self.call_from_thread(self._show_model_picker, models)

    def _show_model_picker(self, models: list) -> None:
        def callback(selected: Optional[str]) -> None:
            if selected:
                self.agent.model = selected
                self._update_title()
                self._log(f"[yellow]Modèle changé pour : {selected}[/yellow]")

        self.push_screen(ModelPickerModal(models, self.agent.model), callback)

    # ------------------------------------------------------------------
    # Exécution du tour agentique dans un thread worker (ollama.chat est bloquant)
    # ------------------------------------------------------------------
    def _process_turn(self, user_input: str) -> None:
        gen = self.agent.run_turn_events(user_input)
        send_value = None
        try:
            while True:
                event = gen.send(send_value) if send_value is not None else next(gen)
                send_value = None
                send_value = self._handle_agent_event(event)
        except StopIteration:
            pass
        except Exception as exc:  # affiche toute erreur inattendue dans le chat
            self.call_from_thread(self._log, f"[red]Erreur: {exc}[/red]")

    def _handle_agent_event(self, event: dict):
        """Traite un évènement émis par l'agent (thread worker). Retourne la
        valeur à renvoyer via gen.send() pour les évènements de confirmation."""
        etype = event["type"]

        if etype == "thinking":
            self.call_from_thread(self._log, f"[dim italic]réflexion : {event['text']}[/dim italic]")
        elif etype == "content":
            self.call_from_thread(self._log, f"[bold cyan]Assistant:[/bold cyan] {event['text']}")
        elif etype == "auto_save_detected":
            self.call_from_thread(
                self._log, f"[magenta]💾 fichier détecté : {event['name']}[/magenta]"
            )
        elif etype == "tool_call":
            self.call_from_thread(
                self._log, f"[grey58]→ appel {event['name']}({event['args']})[/grey58]"
            )
        elif etype == "tool_result":
            self.call_from_thread(self._log, f"[grey58]← résultat : {event['result']}[/grey58]")
        elif etype == "tool_denied":
            self.call_from_thread(self._log, f"[red]Exécution de '{event['name']}' refusée.[/red]")
        elif etype == "tool_error":
            self.call_from_thread(self._log, f"[red]{event['text']}[/red]")
        elif etype == "connection_error":
            self.call_from_thread(
                self._log,
                f"[red]{event['text']}[/red]\n[dim]Astuce : Ctrl+O pour vérifier/changer le serveur Ollama.[/dim]",
            )
        elif etype == "tool_confirm":
            return self._ask_confirmation_blocking(event["name"], event["args"])
        return None

    def _log(self, text: str) -> None:
        self.query_one("#chat-log", RichLog).write(text)

    def _ask_confirmation_blocking(self, name: str, args: dict) -> bool:
        """Affiche le modal de confirmation depuis le thread worker et
        bloque CE thread (pas la boucle d'évènements Textual) jusqu'à
        ce que l'utilisateur réponde."""
        done = threading.Event()
        answer_box = {"value": False}

        def show_modal() -> None:
            def callback(result: bool | None) -> None:
                answer_box["value"] = bool(result)
                done.set()

            self.push_screen(ConfirmModal(name, args), callback)

        self.call_from_thread(show_modal)
        done.wait()
        return answer_box["value"]


def main():
    parser = argparse.ArgumentParser(description="Agent TUI (Textual) agentique basé sur Ollama")
    parser.add_argument("--model", default="qwen3", help="Nom du modèle Ollama à utiliser")
    parser.add_argument(
        "--skills-dir",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills"),
        help="Dossier contenant les skills",
    )
    parser.add_argument(
        "--confirm-tools",
        default="run_command,write_file",
        help="Noms d'outils (séparés par des virgules) nécessitant confirmation avant exécution",
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
        confirm_tools=confirm_tools,
        workspace_dir=args.workspace,
        auto_save_code=not args.no_auto_save,
        system=system_text,
        host=args.host,
        api_key=args.api_key,
        timeout=args.timeout,
    )
    agent.add_system_prompt()

    app = AgentTUI(agent)
    app.run()


if __name__ == "__main__":
    main()
