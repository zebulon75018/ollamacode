# Agent CLI (Ollama + Skills)

Application CLI qui transforme un modèle Ollama en agent capable
d'appeler des outils, avec un système de **skills** inspiré de Claude Code :
chaque compétence est un dossier autonome contenant sa documentation
(`SKILL.md`) et ses outils Python (`tools.py`).

## Installation

```bash
pip install -r requirements.txt
ollama pull qwen3   # ou tout autre modèle supportant le tool calling
```

## Lancement

Deux interfaces sont disponibles, basées sur exactement la même logique
agentique (`agent/core.py`, `agent/skills_manager.py`) :

### Interface texte simple (REPL)

```bash
python agent_cli.py
```

### Interface TUI (Textual)

```bash
python agent_tui.py
```

L'interface TUI ajoute :
- une zone de chat scrollable,
- un panneau latéral listant les skills — clic ou `Entrée` sur une skill
  pour l'activer/la désactiver, à chaud,
- une boîte de dialogue modale pour confirmer les outils sensibles
  (`run_command`, `write_file`...) au lieu d'un simple prompt texte,
- un **sélecteur de modèle** (`Ctrl+M` ou `/model` sans argument) qui
  interroge Ollama (`ollama.list()`) pour lister les modèles réellement
  installés localement, avec leur taille de paramètres et niveau de
  quantization, et permet d'en choisir un directement dans une liste,
- une **combobox de complétion des commandes** : taper `/` dans l'invite
  affiche la liste des commandes disponibles (filtrée en direct au fil de
  la frappe), juste au-dessus du champ de saisie — pas une fenêtre modale,
- un **historique des invites** : flèche `↑`/`↓` dans le champ de saisie
  navigue dans les messages précédemment envoyés (comme un shell),
- un **éditeur du prompt système** (`Ctrl+S` ou `/system`) : dialogue de
  texte multi-ligne pré-rempli avec le prompt système actuel de l'agent,
  avec boutons Enregistrer / Réinitialiser au défaut / Annuler,
- une **configuration du serveur Ollama** (`Ctrl+O` ou `/host`) : dialogue
  pour basculer entre un serveur local et un serveur distant, avec test de
  connexion intégré (voir la section dédiée ci-dessous).

Raccourcis clavier dans le champ de saisie :
- `/` en début d'invite → ouvre la combobox de commandes
- `↑` / `↓` → navigue dans la combobox si elle est ouverte, sinon dans
  l'historique des invites envoyées (flèche haut = la plus récente d'abord)
- `Entrée` → sélectionne la suggestion surlignée si la combobox est
  ouverte, sinon envoie le message
- `Échap` → ferme la combobox sans modifier le texte saisi

Raccourcis globaux TUI : `Ctrl+Q` quitter, `Ctrl+R` réinitialiser la
conversation, `Ctrl+M` changer de modèle, `Ctrl+S` éditer le prompt système,
`Ctrl+O` configurer le serveur Ollama.

## Prompt système personnalisable

Le prompt système (rôle `"system"`) envoyé au modèle appartient à l'objet
`Agent` via l'attribut `agent.system` — une simple chaîne de caractères,
toujours modifiable :

```python
agent.system = "Tu es un pirate qui répond en argot maritime."
agent.add_system_prompt()  # applique le changement (idempotent, pas de doublon)
```

Si aucun `system` n'est fourni au constructeur, `Agent.default_system_prompt()`
en génère un par défaut à partir de la liste des skills actives.

- **TUI** : `Ctrl+S` ou `/system` ouvre un dialogue de texte multi-ligne
  pré-rempli avec le prompt actuel. "Enregistrer" applique immédiatement
  le nouveau texte à la conversation en cours (sans perdre l'historique
  des messages) ; "Réinitialiser au défaut" recharge le prompt généré
  depuis les skills ; "Annuler" ferme sans rien changer.
- **CLI** : `/system` ouvre `$EDITOR`/`$VISUAL` sur un fichier temporaire
  si disponible (comme `git commit -e`), sinon retombe sur une saisie
  multi-ligne au clavier terminée par une ligne contenant seulement `.`.
- **Au démarrage** : `--system-file <chemin>` charge le prompt système
  depuis un fichier texte (CLI et TUI).

Options communes aux deux interfaces :

```bash
python agent_cli.py --model qwen3 --skills-dir skills --confirm-tools run_command,write_file
python agent_tui.py --model qwen3 --skills-dir skills --confirm-tools run_command,write_file
```

- `--model` : nom du modèle Ollama (doit supporter `tools=` et `think=True`)
- `--skills-dir` : dossier contenant les skills (par défaut `skills/`)
- `--confirm-tools` : outils qui demandent une confirmation avant exécution
- `--workspace` : dossier racine où sont sauvegardés les fichiers (manuellement via `write_file`/`create_directory`, ou automatiquement — voir ci-dessous)
- `--no-auto-save` : désactive la sauvegarde automatique des blocs de code
- `--system-file <chemin>` : charge le prompt système depuis un fichier texte au démarrage
- `--host <url>` : serveur Ollama à contacter (local par défaut, ou distant — voir ci-dessous)
- `--api-key <clé>` : jeton envoyé en `Authorization: Bearer ...` pour un serveur Ollama distant protégé
- `--timeout <secondes>` : timeout réseau pour les requêtes vers le serveur Ollama
- `--no-thinking` (CLI uniquement) : masque le raisonnement interne du modèle

## Serveur Ollama local ou distant

Par défaut, l'agent contacte Ollama en local (`http://localhost:11434`, ou
la variable d'environnement `OLLAMA_HOST` si elle est définie). Il est
possible de le pointer vers un serveur Ollama distant (une autre machine
du réseau, un serveur dans le cloud, etc.) de plusieurs façons :

- **Au démarrage** : `--host http://192.168.1.50:11434` (+ éventuellement
  `--api-key <jeton>` si le serveur distant exige une authentification
  `Authorization: Bearer ...`, et `--timeout <secondes>`).
- **En cours de session** :
  - **TUI** : `Ctrl+O` ou `/host` ouvre un dialogue avec un champ hôte, un
    champ clé API (masqué), un champ timeout, un bouton **Tester** (vérifie
    la connexion dans un thread séparé sans geler l'interface et affiche le
    nombre de modèles trouvés ou l'erreur), **Enregistrer**, et **Local par
    défaut** pour revenir instantanément en local.
  - **CLI** : `/host <url>` change directement le serveur ; `/host` seul
    guide pas à pas (hôte, clé API optionnelle, timeout optionnel, avec
    proposition de tester la connexion) ; `/host local` revient au serveur
    local par défaut.

L'attribut `agent.host` (et `agent.client`, l'instance `ollama.Client`
sous-jacente) est reconfigurable à tout moment via `agent.configure_host(...)`.
Si le serveur configuré devient injoignable en cours de conversation (panne
réseau, mauvaise URL...), l'agent l'indique clairement dans le chat au lieu
de planter, avec un rappel du raccourci pour reconfigurer.

## Sauvegarde automatique des blocs de code

Quand le modèle répond avec du texte contenant un bloc ```` ``` ```` dont on
peut déduire un nom de fichier, l'agent appelle automatiquement l'outil
`write_file` pour le sauvegarder sur disque (dans `--workspace`), avec la
même confirmation de sécurité que pour n'importe quel outil sensible.

Le nom de fichier est déduit, dans l'ordre, à partir de :
1. la ligne d'ouverture du bloc : `` ```python app.py ``
2. le texte juste avant le bloc : `Fichier : \`app.py\`` ou `` \`src/app.py\` : ``
3. un commentaire en première ligne du bloc : `# app.py`, `// app.js`...

Si aucun nom de fichier n'est détectable, le bloc est simplement affiché
sans être sauvegardé (aucune sauvegarde "à l'aveugle"). Les chemins sont
toujours résolus relativement à `--workspace` et une tentative d'évasion
(`../../etc/passwd`) est bloquée.

Désactivable via `--no-auto-save` (CLI/TUI) ou `/autosave off` (en cours de
session), et modifiable via `/workspace <chemin>`.

La skill filesystem expose aussi `create_directory` pour que l'agent
puisse créer des répertoires (avec parents) à la demande, en plus de
`read_file`, `write_file` et `list_directory`.

## Commandes (CLI et TUI)

| Commande | Effet |
|---|---|
| `/help` | Affiche l'aide |
| `/skills` | *(CLI)* liste les skills détectées, activées ou non, et leurs outils |
| `/enable <nom>` | *(CLI)* active une skill |
| `/disable <nom>` | *(CLI)* désactive une skill — en TUI, se fait par clic dans le panneau |
| `/reset` | Réinitialise la conversation (garde les skills chargées) |
| `/model` | Ouvre le sélecteur de modèle (interroge Ollama pour lister les modèles installés) |
| `/model <nom>` | Change directement le modèle utilisé |
| `/system` | Ouvre l'éditeur du prompt système (dialogue TUI, ou `$EDITOR`/saisie multi-ligne en CLI) |
| `/host` | Configure le serveur Ollama (dialogue TUI, ou pas-à-pas en CLI) |
| `/host <url>` | *(CLI)* change directement le serveur ; `/host local` revient au local |
| `/thinking on\|off` | Active/désactive l'affichage du raisonnement |
| `/workspace <chemin>` | Affiche ou change le dossier de sauvegarde des fichiers |
| `/autosave on\|off` | Active/désactive la sauvegarde automatique des blocs de code |
| `/exit`, `/quit` | Quitte |

## Créer une nouvelle skill

Créez un dossier dans `skills/`, par exemple `skills/web_skill/`, avec :

**`skills/web_skill/SKILL.md`**
```markdown
---
name: web
description: Recherche et récupération de pages web
enabled: true
---

# Skill Web
Décrivez ici ce que fait la skill, pour que ce texte serve de contexte
au modèle dans le prompt système.
```

**`skills/web_skill/tools.py`**
```python
def fetch_url(url: str) -> str:
    """Récupère le contenu texte d'une URL.

    Args:
        url: L'URL à récupérer
    Returns:
        Le contenu de la page
    """
    import urllib.request
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="ignore")[:5000]
```

Toute fonction publique de `tools.py`, typée et documentée (comme dans
l'exemple d'origine avec `add`/`multiply`), est automatiquement détectée
et exposée comme outil au modèle — aucune inscription manuelle nécessaire.
Si un outil est sensible (écriture disque, réseau, shell...), ajoutez son
nom à `--confirm-tools` pour exiger une confirmation avant chaque appel.

## Architecture

```
agent_cli.py             # REPL texte, commandes /xxx, point d'entrée CLI
agent_tui.py             # Interface TUI Textual, point d'entrée TUI
agent/
  core.py                 # Boucle agentique (générateur d'évènements) commune au CLI et au TUI
  skills_manager.py        # Découverte et chargement dynamique des skills
  ollama_utils.py          # Interrogation d'Ollama (liste des modèles installés)
skills/
  math_skill/             # add, subtract, multiply, divide
  filesystem_skill/       # read_file, write_file, list_directory
  shell_skill/            # run_command (désactivée par défaut)
```
