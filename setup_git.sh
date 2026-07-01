#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Script d'initialisation du dépôt Git — PV Schaerbeek
# Usage : bash setup_git.sh
# ═══════════════════════════════════════════════════════════════
set -e

echo "═══════════════════════════════════════════════"
echo "  Initialisation dépôt Git — PV Schaerbeek"
echo "═══════════════════════════════════════════════"

# Vérifier qu'aucun .env ne traîne
if find . -name ".env" -not -name ".env.example" | grep -q .; then
  echo "⚠️  ATTENTION : un fichier .env a été détecté."
  echo "   Il ne sera PAS committé (protégé par .gitignore),"
  echo "   mais vérifie qu'il ne contient pas de secret exposé."
fi

# Init
if [ ! -d .git ]; then
  git init
  echo "✅ Dépôt Git initialisé"
else
  echo "ℹ️  Dépôt Git déjà initialisé"
fi

git add .
echo ""
echo "── Fichiers qui seront committés ──"
git status --short

echo ""
echo "── Vérification sécurité : aucun .env ne doit apparaître ci-dessus ──"
if git status --short | grep -E "\.env$" | grep -v ".env.example"; then
  echo "❌ STOP : un .env est sur le point d'être committé ! Annule avec 'git reset'."
  exit 1
else
  echo "✅ Aucun secret détecté dans le commit"
fi

echo ""
echo "── Prochaines étapes ──"
echo "  git commit -m 'Prototype Q&R PV Schaerbeek'"
echo ""
echo "  # Avec GitHub CLI :"
echo "  # Avec GitHub CLI (crée le repo AVEC description) :"
echo "  gh repo create pv-explorer --public --source=. --push \\"
echo "    --description \"Outil citoyen pour interroger les procès-verbaux du Conseil communal de Schaerbeek. RAG + statistiques, réponses citées.\""
echo ""
echo "  # Puis ajouter les topics (mots-clés de découvrabilité) :"
echo "  gh repo edit --add-topic rag,civic-tech,fastapi,pinecone,claude,schaerbeek,open-data,french"
echo ""
echo "  # Ou manuellement :"
echo "  git remote add origin https://github.com/TON-PSEUDO/pv-explorer.git"
echo "  git branch -M main"
echo "  git push -u origin main"
