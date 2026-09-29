# Hardware & Security Audit Tool

Outil d'audit matériel et de posture de sécurité sous Windows 11, générant un rapport HTML/PDF interactif avec calcul dynamique du score de conformité.

## Fonctionnalités

- Analyse de la sécurité matérielle : Détection de la présence et du statut du module TPM 2.0, du chiffrement BitLocker, du Secure Boot et de la virtualisation matérielle.
- Inventaire système et matériel : Modèle et charge CPU, utilisation de la mémoire RAM, firmware BIOS, état de la batterie.
- Stockage et réseau : Analyse des espaces disques, systèmes de fichiers et interfaces réseau (vitesses, statut, adresses MAC).
- Moteur de recommandations : Calcul automatique du score de conformité et génération d'un plan d'action priorisé.
- Rapport HTML et PDF autonome :
  - Interface utilisateur moderne et responsive.
  - Bascule entre mode sombre et mode clair.
  - Export PDF direct côté client via html2pdf.js sans dépendance serveur.

## Prérequis et Installation

1. Windows 10/11 avec Python 3.8 ou supérieur.
2. Cloner le dépôt :
   \\\ash
   git clone https://github.com/TON_NOM_UTILISATEUR/Hardware-Sec-Audit.git
   cd Hardware-Sec-Audit
   \\\
3. Installer les dépendances :
   \\\ash
   pip install -r requirements.txt
   \\\

## Utilisation

Pour permettre la lecture des métriques de sécurité matérielle (TPM, BitLocker, WMI), exécutez le script dans un terminal avec les privilèges d'administrateur :

\\\ash
python audit.py --all
\\\

Le script génère les rapports dans le dossier courant (dont audit_report.html). Ouvrez-le dans un navigateur puis cliquez sur "Exporter en PDF" pour générer la version imprimable du bilan.

## Licence

Ce projet est distribué sous licence MIT.
