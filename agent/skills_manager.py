"""
Gestion des "skills" (compétences), inspirée du fonctionnement de Claude Code.

Chaque skill est un dossier placé dans skills/ contenant :
  - SKILL.md : métadonnées (frontmatter) + description en langage naturel
  - tools.py : fonctions Python exposées comme outils à l'IA (typées + docstring)

Frontmatter attendu en tête de SKILL.md :

    ---
    name: nom_du_skill
    description: courte description utilisée dans le prompt système
    enabled: true
    ---

Toute fonction publique (ne commençant pas par _) définie dans tools.py
est automatiquement exposée comme outil disponible pour le modèle.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List


@dataclass
class Skill:
    name: str
    description: str
    path: str
    enabled: bool = True
    functions: Dict[str, Callable] = field(default_factory=dict)

    @property
    def tool_list(self) -> List[Callable]:
        return list(self.functions.values())


def _parse_frontmatter(md_text: str) -> dict:
    """Parse un frontmatter simple `--- key: value ---` sans dépendance externe."""
    meta: dict = {}
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            block = parts[1]
            for line in block.strip().splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def _load_tools_module(skill_dir: str, skill_entry_name: str):
    """Importe dynamiquement tools.py d'une skill et retourne ses fonctions publiques."""
    tools_path = os.path.join(skill_dir, "tools.py")
    if not os.path.isfile(tools_path):
        return {}

    module_name = f"skill_tools_{skill_entry_name}"
    spec = importlib.util.spec_from_file_location(module_name, tools_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    funcs = {}
    for name, obj in inspect.getmembers(module):
        if (
            inspect.isfunction(obj)
            and not name.startswith("_")
            and obj.__module__ == module.__name__
        ):
            funcs[name] = obj
    return funcs


def discover_skills(skills_dir: str) -> List[Skill]:
    """Parcourt le dossier skills/ et retourne la liste des skills détectées."""
    skills: List[Skill] = []
    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        skill_dir = os.path.join(skills_dir, entry)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isdir(skill_dir) or not os.path.isfile(skill_md):
            continue

        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()
        meta = _parse_frontmatter(content)

        name = meta.get("name", entry)
        description = meta.get("description", "")
        enabled = meta.get("enabled", "true").lower() != "false"

        functions = _load_tools_module(skill_dir, entry)

        skills.append(
            Skill(
                name=name,
                description=description,
                path=skill_dir,
                enabled=enabled,
                functions=functions,
            )
        )
    return skills
