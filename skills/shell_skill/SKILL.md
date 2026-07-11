---
name: shell
description: Exécution de commandes shell (toujours soumise à confirmation utilisateur)
enabled: false
---

# Skill Shell

Donne à l'agent la capacité d'exécuter des commandes shell arbitraires
sur la machine locale. Cette skill est **désactivée par défaut** car
elle est potentiellement dangereuse (l'agent pourrait exécuter des
commandes destructrices si le modèle se trompe).

Activez-la uniquement si vous faites confiance au contexte d'utilisation,
avec `/enable shell` dans le CLI. Chaque exécution demande de toute façon
une confirmation explicite ("o/N") avant de lancer la commande.
