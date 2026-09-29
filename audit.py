import platform
import subprocess
import psutil
import json
import os
import re
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime

# ==============================================================================
# MODULES DE COLLECTE DE DONNÉES
# ==============================================================================

def check_powershell(cmd):
    """Exécute une commande PowerShell et retourne la sortie nettoyée."""
    try:
        res = subprocess.run(f'powershell -Command "{cmd}"', capture_output=True, text=True, shell=True)
        return res.stdout.strip()
    except Exception:
        return ""

def get_progress_bar(percent, length=15):
    """Génère une barre de progression ASCII propre."""
    try:
        pct = float(percent)
    except (ValueError, TypeError):
        pct = 0.0
    filled = int(length * pct // 100)
    return f"[{'█' * filled}{'░' * (length - filled)}] {pct:.1f}%"

def parse_wmi_date(date_str):
    """Nettoie et convertit les dates WMI/JSON en YYYY-MM-DD."""
    if not date_str or date_str == "Inconnu":
        return "Inconnu"
    match = re.search(r'/Date\((\d+)\)/', str(date_str))
    if match:
        ts = int(match.group(1)) / 1000.0
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    return str(date_str).split("T")[0]

def get_cpu_virtualization():
    val = check_powershell("Get-CimInstance -ClassName Win32_Processor | Select-Object -ExpandProperty VirtualizationFirmwareEnabled")
    return val.lower() == 'true'

def get_tpm_status():
    val = check_powershell("Get-Tpm | Select-Object -ExpandProperty TpmPresent")
    return val.lower() == 'true'

def get_bitlocker_status():
    val = check_powershell("Get-BitLockerVolume -MountPoint C: | Select-Object -ExpandProperty ProtectionStatus")
    return val == "1" or val.lower() == "protectionon"

def get_secure_boot_status():
    val = check_powershell("Confirm-SecureBootUEFI")
    return val.lower() == 'true'

def get_bios_info():
    cmd = "Get-CimInstance -ClassName Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, ReleaseDate | ConvertTo-Json"
    out = check_powershell(cmd)
    try:
        data = json.loads(out)
        return {
            "vendor": data.get("Manufacturer", "Inconnu"),
            "version": data.get("SMBIOSBIOSVersion", "Inconnu"),
            "date": parse_wmi_date(data.get("ReleaseDate", ""))
        }
    except Exception:
        return {"vendor": "Inconnu", "version": "Inconnu", "date": "Inconnu"}

def get_gpu_info():
    cmd = "Get-CimInstance -ClassName Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion | ConvertTo-Json"
    out = check_powershell(cmd)
    gpus = []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            vram_mb = (item.get("AdapterRAM") or 0) / (1024 ** 2)
            gpus.append({
                "name": item.get("Name", "GPU Inconnu"),
                "vram_mb": round(vram_mb, 2),
                "driver": item.get("DriverVersion", "Inconnu")
            })
    except Exception:
        pass
    return gpus

def get_battery_advanced():
    xml_path = "battery_report.xml"
    design_cap, full_cap, wear_level = 0, 0, 0
    percent = 0

    batt_psutil = psutil.sensors_battery()
    if batt_psutil:
        percent = round(batt_psutil.percent, 1)

    try:
        subprocess.run(f"powercfg /batteryreport /xml /output \"{xml_path}\"", shell=True, capture_output=True)
        if os.path.exists(xml_path):
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag == "DesignCapacity" and elem.text and elem.text.isdigit():
                    design_cap = int(elem.text)
                elif tag == "FullChargeCapacity" and elem.text and elem.text.isdigit():
                    full_cap = int(elem.text)

            if design_cap > 0 and full_cap > 0:
                wear_level = round((1 - (full_cap / design_cap)) * 100, 2)

            os.remove(xml_path)
    except Exception:
        if os.path.exists(xml_path):
            os.remove(xml_path)

    if not batt_psutil and design_cap == 0:
        return None

    return {
        "percent": percent,
        "design_capacity": design_cap,
        "full_capacity": full_cap,
        "wear_level": max(0, wear_level)
    }

def get_network_adapters():
    cmd = "Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress | ConvertTo-Json"
    out = check_powershell(cmd)
    adapters = []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            adapters.append({
                "name": item.get("Name", ""),
                "description": item.get("InterfaceDescription", ""),
                "status": item.get("Status", "Inconnu"),
                "speed": item.get("LinkSpeed", "N/A"),
                "mac": item.get("MacAddress", "N/A")
            })
    except Exception:
        pass
    return adapters

def get_usb_devices():
    cmd = "Get-CimInstance -ClassName Win32_PnPEntity | Where-Object {$_.PNPClass -eq 'USB' -or $_.Service -eq 'USBSTOR'} | Select-Object Name, Status | ConvertTo-Json"
    out = check_powershell(cmd)
    devices = []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for item in data:
            if item.get("Name"):
                devices.append({
                    "name": item.get("Name"),
                    "status": item.get("Status", "OK")
                })
    except Exception:
        pass
    return devices

def run_full_audit():
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    
    disks = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            disks.append({
                "mount": partition.mountpoint,
                "fstype": partition.fstype,
                "total": round(usage.total / (1024 ** 3), 2),
                "used": round(usage.used / (1024 ** 3), 2),
                "free": round(usage.free / (1024 ** 3), 2),
                "percent": usage.percent
            })
        except PermissionError:
            continue

    sec_tpm = get_tpm_status()
    sec_bitlocker = get_bitlocker_status()
    sec_sb = get_secure_boot_status()
    cpu_virt = get_cpu_virtualization()

    passed_checks = sum([sec_tpm, sec_bitlocker, sec_sb, cpu_virt])
    score_pct = (passed_checks / 4) * 100

    return {
        "audit_meta": {
            "tool": "Hardware-Sec-Audit",
            "author": "Sylvanus ZOADAN",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "compliance_score": score_pct
        },
        "system": {
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()} (Build {platform.version()})",
            "bios": get_bios_info()
        },
        "cpu": {
            "model": platform.processor(),
            "arch": platform.machine(),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "usage_percent": cpu_usage,
            "virtualization": cpu_virt
        },
        "ram": {
            "total_gb": round(ram.total / (1024 ** 3), 2),
            "used_gb": round(ram.used / (1024 ** 3), 2),
            "available_gb": round(ram.available / (1024 ** 3), 2),
            "percent": ram.percent,
            "swap_total_gb": round(swap.total / (1024 ** 3), 2),
            "swap_used_gb": round(swap.swap_used / (1024 ** 3), 2) if hasattr(swap, 'swap_used') else round(swap.used / (1024 ** 3), 2),
            "swap_percent": swap.percent
        },
        "disks": disks,
        "gpus": get_gpu_info(),
        "network": get_network_adapters(),
        "usb": get_usb_devices(),
        "battery": get_battery_advanced(),
        "security": {
            "tpm_2_0": sec_tpm,
            "bitlocker_c": sec_bitlocker,
            "secure_boot": sec_sb,
            "hardware_virtualization": cpu_virt
        }
    }

# ==============================================================================
# GENERATEURS DE RAPPORTS
# ==============================================================================

def export_json(data, filename="audit_report.json"):
    """Génère un export JSON structuré."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f"[+] Rapport JSON généré : {filename}")

def export_markdown(data, filename="audit_report.md"):
    """Génère le rapport Markdown standard."""
    md = []
    md.append("# RAPPORT D'AUDIT MATÉRIEL ET D'INFRASTRUCTURE SÉCURITÉ")
    md.append(f"**Machine / Hôte :** {data['system']['hostname']}")
    md.append(f"**Système d'Exploitation :** {data['system']['os']}")
    md.append(f"**Horodatage de l'Audit :** {data['audit_meta']['timestamp']}")
    md.append("\n---\n")

    # CARACTÉRISTIQUES PRINCIPALES
    md.append("## 📌 CARACTÉRISTIQUES PRINCIPALES DU SYSTÈME\n")
    gpu_main = data['gpus'][0]['name'] if data['gpus'] else "Intégré"
    md.append(f"- **Microprocesseur :** {data['cpu']['model']} ({data['cpu']['physical_cores']} Cœurs Physiques / {data['cpu']['logical_cores']} Threads)")
    md.append(f"- **Mémoire Vive (RAM) :** {data['ram']['total_gb']} Go DDR")
    md.append(f"- **Stockage Système (C:) :** {data['disks'][0]['total']} Go ({data['disks'][0]['fstype']})")
    md.append(f"- **Processeur Graphique :** {gpu_main}")
    md.append(f"- **Carte Mère / Firmware :** BIOS {data['system']['bios']['vendor']} v{data['system']['bios']['version']} ({data['system']['bios']['date']})")
    md.append("\n---\n")

    # 1. Posture de Sécurité
    md.append("## 1. POSTURE DE SÉCURITÉ MATÉRIELLE\n")
    md.append("| Contrôle de Sécurité | État | Analyse & Impact Opérationnel |")
    md.append("| :--- | :---: | :--- |")
    
    tpm_st = "CONFORME" if data['security']['tpm_2_0'] else "NON CONFORME"
    tpm_exp = "Puce TPM 2.0 active. Ancre de confiance matérielle fonctionnelle." if data['security']['tpm_2_0'] else "Absence de TPM 2.0. Risque sur le stockage sécurisé des clés."
    md.append(f"| Puce TPM 2.0 | **{tpm_st}** | {tpm_exp} |")
    
    bit_st = "CONFORME" if data['security']['bitlocker_c'] else "NON CONFORME"
    bit_exp = "Chiffrement BitLocker actif sur C:. Protection des données au repos validée." if data['security']['bitlocker_c'] else "BitLocker INACTIF. Risque de fuite de données en cas de vol du disque."
    md.append(f"| Chiffrement BitLocker (C:) | **{bit_st}** | {bit_exp} |")
    
    sb_st = "CONFORME" if data['security']['secure_boot'] else "NON CONFORME"
    sb_exp = "Secure Boot actif. Protection contre la mise en place de rootkits au boot." if data['security']['secure_boot'] else "Secure Boot désactivé. Risque d'altération de la chaîne d'amorçage."
    md.append(f"| Démarrage Sécurisé (Secure Boot) | **{sb_st}** | {sb_exp} |")
    
    virt_st = "ACTIVÉE" if data['security']['hardware_virtualization'] else "DÉSACTIVÉE"
    virt_exp = "Virtualisation matérielle active dans l'UEFI." if data['security']['hardware_virtualization'] else "Virtualisation DÉSACTIVÉE. Bloque l'isolation du noyau (HVCI / VBS) et VirtualBox/Hyper-V."
    md.append(f"| Virtualisation Matérielle | **{virt_st}** | {virt_exp} |")
    md.append("\n")

    # 2. Firmware et BIOS
    md.append("## 2. FIRMWARE ET BIOS/UEFI\n")
    md.append(f"- **Constructeur BIOS :** {data['system']['bios']['vendor']}")
    md.append(f"- **Version SMBIOS :** {data['system']['bios']['version']}")
    md.append(f"- **Date de Mise à Jour :** {data['system']['bios']['date']}")
    md.append(f"- **Analyse :** Firmware répertorié au {data['system']['bios']['date']}. Assure le support adéquat des microcodes processeur.")
    md.append("\n")

    # 3. Microprocesseur & GPU
    md.append("## 3. PROCESSEUR ET UNITÉS GRAPHIQUES\n")
    md.append(f"- **Modèle CPU :** {data['cpu']['model']}")
    md.append(f"- **Architecture :** {data['cpu']['arch']}")
    md.append(f"- **Cœurs Physiques / Logiques :** {data['cpu']['physical_cores']} / {data['cpu']['logical_cores']}")
    md.append(f"- **Charge Actuelle :** {get_progress_bar(data['cpu']['usage_percent'])}\n")
    
    md.append("### Contrôleurs Graphiques (GPU)")
    if data['gpus']:
        md.append("| Modèle GPU | VRAM Dédiée | Version Pilote | État |")
        md.append("| :--- | :---: | :---: | :---: |")
        for gpu in data['gpus']:
            md.append(f"| {gpu['name']} | {gpu['vram_mb']} Mo | {gpu['driver']} | **Opérationnel** |")
    md.append("\n")

    # 4. Mémoire RAM et Stockage
    md.append("## 4. MÉMOIRE RAM ET STOCKAGE\n")
    md.append(f"- **RAM Totale :** {data['ram']['total_gb']} Go")
    md.append(f"- **RAM Utilisée :** {data['ram']['used_gb']} Go {get_progress_bar(data['ram']['percent'])}")
    md.append(f"- **RAM Disponible :** {data['ram']['available_gb']} Go")
    md.append(f"- **Utilisation SWAP :** {data['ram']['swap_used_gb']} Go / {data['ram']['swap_total_gb']} Go {get_progress_bar(data['ram']['swap_percent'])}\n")
    
    if data['ram']['percent'] > 85:
        md.append("> ⚠️ **Alerte Mémoire :** La RAM est saturée à plus de 85%. Le système compense via le fichier d'échange (SWAP), ce qui dégrade les performances I/O.")

    md.append("\n### Volumes de Stockage Physique")
    md.append("| Lecteur | Système de fichiers | Capacité Totale | Espace Libre | Taux d'utilisation |")
    md.append("| :---: | :---: | :---: | :---: | :--- |")
    for disk in data['disks']:
        md.append(f"| {disk['mount']} | {disk['fstype']} | {disk['total']} Go | {disk['free']} Go | {get_progress_bar(disk['percent'])} |")
    md.append("\n")

    # 5. Connectivité Réseau & Périphériques USB
    md.append("## 5. INTERFACES RÉSEAU ET PÉRIPHÉRIQUES USB\n")
    md.append("### Cartes Réseau (NIC)")
    if data['network']:
        md.append("| Nom | Description | État | Vitesse | Adresse MAC |")
        md.append("| :--- | :--- | :---: | :---: | :---: |")
        for net in data['network']:
            md.append(f"| {net['name']} | {net['description']} | {net['status']} | {net['speed']} | `{net['mac']}` |")
    
    md.append("\n### Contrôleurs & Bus USB")
    if data['usb']:
        md.append(f"- **Périphériques USB Détectés :** {len(data['usb'])} bus/périphériques actifs.")
        for u in data['usb'][:5]:
            md.append(f"  - {u['name']} (Statut: {u['status']})")
    md.append("\n")

    # 6. Gestion de l'Énergie & Batterie
    md.append("## 6. GESTION DE L'ÉNERGIE ET BATTERIE\n")
    if data['battery']:
        md.append(f"- **Niveau de Charge Actuel :** {get_progress_bar(data['battery']['percent'])}")
        if data['battery']['design_capacity'] > 0:
            md.append(f"- **Capacité Constructeur (Usine) :** {data['battery']['design_capacity']} mWh")
            md.append(f"- **Capacité Maximale Actuelle :** {data['battery']['full_capacity']} mWh")
            md.append(f"- **Niveau d'Usure (Wear Level) :** {get_progress_bar(data['battery']['wear_level'])}")
            if data['battery']['wear_level'] < 20:
                md.append("- **Analyse Batterie :** Excellent état de santé. La perte de capacité reste minime.")
            else:
                md.append("- **Analyse Batterie :** Usure notable détectée. Autonomie dégradée par rapport aux spécifications d'usine.")
        else:
            md.append("- **Analyse Batterie :** Rapport généré. Capacité mesurée via l'API système.")
    else:
        md.append("- **Statut Énergie :** Poste fixe détecté (aucune batterie présente).")
    md.append("\n")

    # 7. Évaluation Détaillée & Recommandations
    md.append("## 7. EVALUATION DÉTAILLÉE ET RECOMMANDATIONS\n")
    
    if not data['security']['hardware_virtualization']:
        md.append("### ❌ Virtualisation Matérielle (DÉSACTIVÉE)")
        md.append("- **Pourquoi c'est un problème :** L'absence de VT-x/AMD-V empêche le fonctionnement d'Hyper-V, des machines virtuelles (VirtualBox) et des fonctionnalités de sécurité Windows d'isolation du noyau (HVCI).")
        md.append("- **Action Recommandée :** Accéder au BIOS/UEFI au démarrage (touche F2 sur Dell) > Onglet *Virtualization Support* > Activer *Intel Virtualization Technology*.\n")
    
    if not data['security']['bitlocker_c']:
        md.append("### ❌ Chiffrement du Disque C: (INACTIF)")
        md.append("- **Pourquoi c'est un problème :** Les données enregistrées sur le lecteur système ne sont pas chiffrées à froid. Un accès physique au disque permet de lire l'intégralité des fichiers.")
        md.append("- **Action Recommandée :** Ouvrir la console de gestion BitLocker sous Windows et activer le chiffrement du lecteur C: avec la puce TPM 2.0.\n")
    
    if data['ram']['percent'] > 85:
        md.append("### ❌ Pression Mémoire Critique (RAM Saturated)")
        md.append(f"- **Pourquoi c'est un problème :** La mémoire physique est occupée à {data['ram']['percent']}%, forçant l'utilisation du fichier SWAP ({data['ram']['swap_used_gb']} Go sollicités). Cela provoque des latences d'I/O et des risques d'instabilité.")
        md.append("- **Action Recommandée :** Étendre la mémoire RAM physique du poste à 16 Go DDR pour assurer une exécution fluide des tâches d'administration et d'audit.\n")

    md.append("---\n")
    md.append("## 📋 RÉCAPITULATIF SYNTHÉTIQUE DE L'AUDIT\n")
    score_pct = data['audit_meta']['compliance_score']
    md.append(f"- **Score de Conformité Sécurité Matérielle :** {score_pct:.0f}%")
    md.append(f"- **Santé Matérielle Globale :** {'⚠️ ATTENTION (Ressources / Chiffrement)' if score_pct < 100 or data['ram']['percent'] > 85 else '🟢 OPTIMALE'}")
    md.append(f"- **Actions prioritaires :** Activer BitLocker sur C:, activer la virtualisation dans l'UEFI, et augmenter la mémoire RAM à 16 Go.")

    md.append(f"\n---\n*Rapport d'audit généré automatiquement par l'outil Hardware-Sec-Audit made with Love by {data['audit_meta']['author']}.*")

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[+] Rapport Markdown généré : {filename}")

def export_html(data, filename="audit_report.html"):
    """Génère un rapport HTML interactif autonome avec recommandations par section,
    plan d'action final, bascule Thème Sombre/Clair et export PDF direct via html2pdf.js."""
    
    score = data['audit_meta']['compliance_score']
    score_color = "#10b981" if score == 100 else "#f59e0b" if score >= 50 else "#ef4444"

    # --- 1. RECOMMANDATIONS & COMMENTAIRES PAR SECTION ---
    sec_comments = []
    if not data['security']['tpm_2_0']:
        sec_comments.append("❌ <strong>TPM 2.0 absent/désactivé :</strong> Limite l'utilisation du chiffrement matériel et les fonctionnalités avancées de Windows 11.")
    if not data['security']['bitlocker_c']:
        sec_comments.append("❌ <strong>BitLocker inactif :</strong> Risque élevé d'extraction de données en cas de vol ou perte du support physique.")
    if not data['security']['secure_boot']:
        sec_comments.append("⚠️ <strong>Secure Boot désactivé :</strong> Vulnérabilité face aux bootkits/rootkits lors de l'amorçage.")
    if not data['security']['hardware_virtualization']:
        sec_comments.append("⚠️ <strong>Virtualisation désactivée :</strong> Empêche l'isolation du noyau (HVCI/VBS) et l'exécution de conteneurs/VMs.")
    
    if not sec_comments:
        sec_reco_html = "<div class='reco-box reco-success'><strong>Avis Global Sécurité :</strong> Conforme aux exigences de sécurité matérielle standard.</div>"
    else:
        sec_reco_html = "<div class='reco-box reco-warning'><strong>Recommandations Sécurité :</strong><ul>" + "".join([f"<li>{c}</li>" for c in sec_comments]) + "</ul></div>"

    # Section CPU / RAM
    ram_reco_html = ""
    if data['ram']['percent'] > 85:
        ram_reco_html = f"<div class='reco-box reco-danger'><strong>Alerte Pression Mémoire :</strong> RAM occupée à {data['ram']['percent']}%. Le système utilise {data['ram']['swap_used_gb']} Go de SWAP. <em>Action conseillée : Étendre la RAM à 16 Go ou fermer les applications gourmandes.</em></div>"
    else:
        ram_reco_html = "<div class='reco-box reco-success'><strong>Analyse Mémoire :</strong> Utilisation de la mémoire vive optimale pour le profil d'utilisation actuel.</div>"

    # Section Disques
    disk_comments = []
    disks_html = ""
    for d in data['disks']:
        disks_html += f"""
        <tr>
            <td><strong>{d['mount']}</strong></td>
            <td>{d['fstype']}</td>
            <td>{d['total']} Go</td>
            <td>{d['free']} Go</td>
            <td>
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" style="width: {d['percent']}%;"></div>
                </div>
                <small>{d['percent']}%</small>
            </td>
        </tr>
        """
        if d['percent'] > 85:
            disk_comments.append(f"Lecteur <strong>{d['mount']}</strong> saturé à {d['percent']}% ({d['free']} Go libres). Risque d'impact sur les performances.")
    
    if disk_comments:
        disk_reco_html = "<div class='reco-box reco-warning'><strong>Commentaire Stockage :</strong><ul>" + "".join([f"<li>{c}</li>" for c in disk_comments]) + "</ul></div>"
    else:
        disk_reco_html = "<div class='reco-box reco-success'><strong>Commentaire Stockage :</strong> Espace disque suffisant sur l'ensemble des volumes.</div>"

    # --- 2. RÉCAPITULATIF DES ACTIONS À FAIRE (PLAN D'ACTION FINAL) ---
    todo_list = []
    if not data['security']['bitlocker_c']:
        todo_list.append("<strong>[P1 - Priorité Haute]</strong> Activer le chiffrement BitLocker sur le lecteur système (C:).")
    if not data['security']['hardware_virtualization']:
        todo_list.append("<strong>[P2 - Priorité Moyenne]</strong> Activer Intel VT-x / AMD-V dans le BIOS/UEFI pour activer l'isolation du noyau.")
    if not data['security']['secure_boot']:
        todo_list.append("<strong>[P2 - Priorité Moyenne]</strong> Activer le Secure Boot dans l'UEFI.")
    if data['ram']['percent'] > 85:
        todo_list.append("<strong>[P3 - Planification]</strong> Envisager une mise à niveau matérielle (RAM) pour réduire la dépendance au fichier SWAP.")

    if not todo_list:
        todo_html = "<div class='reco-box reco-success'><strong>Aucune action corrective requise.</strong> L'infrastructure matérielle respecte la politique de conformité.</div>"
    else:
        todo_html = "<ol class='todo-list'>" + "".join([f"<li>{item}</li>" for item in todo_list]) + "</ol>"

    # --- 3. CONTEXTUALISATION RÉSEAU ---
    net_html = ""
    for n in data['network']:
        status_badge = f'<span class="badge badge-success">{n["status"]}</span>' if n["status"].lower() in ["up", "connected"] else f'<span class="badge badge-secondary">{n["status"]}</span>'
        net_html += f"""
        <tr>
            <td><strong>{n['name']}</strong></td>
            <td>{n['description']}</td>
            <td>{status_badge}</td>
            <td>{n['speed']}</td>
            <td><code>{n['mac']}</code></td>
        </tr>
        """

    # --- 4. STRUCTURE HTML / CSS / JS ---
    html_content = f"""<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Audit Sécurité Matérielle - {data['system']['hostname']}</title>
    <!-- Script de conversion HTML -> PDF direct -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --text-muted: #94a3b8;
            --border-color: #334155;
            --accent-blue: #38bdf8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --reco-bg: rgba(56, 189, 248, 0.1);
        }}

        [data-theme="light"] {{
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-color: #0f172a;
            --text-muted: #64748b;
            --border-color: #e2e8f0;
            --accent-blue: #0284c7;
            --reco-bg: #f0f9ff;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
            line-height: 1.6;
            transition: background-color 0.3s, color 0.3s;
        }}

        .container {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 10px;
        }}

        header {{
            background: var(--card-bg);
            padding: 24px;
            border-radius: 12px;
            border: 1px solid var(--border-color);
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .action-bar {{
            display: flex;
            justify-content: flex-end;
            gap: 12px;
            margin-bottom: 20px;
        }}

        .btn {{
            background-color: var(--card-bg);
            color: var(--text-color);
            border: 1px solid var(--border-color);
            padding: 10px 18px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }}

        .btn:hover {{
            border-color: var(--accent-blue);
            color: var(--accent-blue);
        }}

        .btn-primary {{
            background-color: var(--accent-blue);
            color: #fff;
            border: none;
        }}

        .btn-primary:hover {{
            opacity: 0.9;
            color: #fff;
        }}

        h1 {{ margin: 0; font-size: 1.5rem; color: var(--accent-blue); }}
        .meta-info {{ color: var(--text-muted); font-size: 0.9rem; margin-top: 5px; }}

        .score-box {{
            text-align: center;
            padding: 15px 25px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 10px;
            border: 1px solid var(--border-color);
        }}

        .score-val {{ font-size: 2rem; font-weight: bold; color: {score_color}; }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }}

        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }}

        .card h2 {{
            margin-top: 0;
            font-size: 1.1rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 10px;
            color: var(--accent-blue);
        }}

        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9rem; }}
        th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid var(--border-color); }}
        th {{ color: var(--text-muted); font-weight: 600; }}

        .badge {{ padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: bold; display: inline-block; }}
        .badge-success {{ background: rgba(16, 185, 129, 0.2); color: var(--success); }}
        .badge-danger {{ background: rgba(239, 68, 68, 0.2); color: var(--danger); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.2); color: var(--warning); }}
        .badge-secondary {{ background: rgba(148, 163, 184, 0.2); color: var(--text-muted); }}

        .progress-bar-bg {{
            background: var(--border-color);
            height: 8px;
            border-radius: 4px;
            overflow: hidden;
            display: inline-block;
            width: 80px;
            vertical-align: middle;
        }}
        .progress-bar-fill {{ background: var(--accent-blue); height: 100%; }}

        .reco-box {{
            margin-top: 15px;
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 0.88rem;
            background: var(--reco-bg);
            border-left: 4px solid var(--accent-blue);
        }}
        .reco-box ul {{ margin: 5px 0 0 18px; padding: 0; }}

        .todo-card {{
            border-left: 5px solid var(--accent-blue);
            background: var(--card-bg);
        }}
        .todo-list {{ padding-left: 20px; }}
        .todo-list li {{ margin-bottom: 10px; }}

        footer {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
        }}

        /* Style spécifique pour la génération du PDF */
        .pdf-mode {{
            background-color: #ffffff !important;
            color: #000000 !important;
        }}
        .pdf-mode .card, .pdf-mode header {{
            background: #ffffff !important;
            border: 1px solid #e2e8f0 !important;
            color: #000000 !important;
        }}
        .pdf-mode h1, .pdf-mode h2, .pdf-mode .score-val {{
            color: #0f172a !important;
        }}
        .pdf-mode .reco-box {{
            background: #f1f5f9 !important;
            color: #0f172a !important;
            border-left-color: #0284c7 !important;
        }}
    </style>
</head>
<body>
    <div class="container" id="reportContent">
        <!-- BARRE D'ACTIONS -->
        <div class="action-bar" data-html2canvas-ignore="true">
            <button class="btn" onclick="toggleTheme()" id="themeBtn">🌓 Mode Clair</button>
            <button class="btn btn-primary" onclick="downloadPDF()" id="pdfBtn">📄 Exporter en PDF</button>
        </div>

        <header>
            <div>
                <h1>RAPPORT D'AUDIT SÉCURITÉ & MATÉRIEL</h1>
                <div class="meta-info">
                    Hôte : <strong>{data['system']['hostname']}</strong> | OS : {data['system']['os']}<br>
                    Horodatage : {data['audit_meta']['timestamp']}
                </div>
            </div>
            <div class="score-box">
                <div class="score-val">{score:.0f}%</div>
                <small style="color: var(--text-muted);">Conformité Sécurité</small>
            </div>
        </header>

        <!-- 1. POSTURE DE SÉCURITÉ -->
        <div class="card">
            <h2>1. POSTURE DE SÉCURITÉ MATÉRIELLE</h2>
            <table>
                <thead>
                    <tr>
                        <th>Contrôle de Sécurité</th>
                        <th>État</th>
                        <th>Analyse Opérationnelle</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Puce TPM 2.0</td>
                        <td>{'<span class="badge badge-success">CONFORME</span>' if data['security']['tpm_2_0'] else '<span class="badge badge-danger">NON CONFORME</span>'}</td>
                        <td>Ancre de confiance matérielle fonctionnelle.</td>
                    </tr>
                    <tr>
                        <td>Chiffrement BitLocker (C:)</td>
                        <td>{'<span class="badge badge-success">CONFORME</span>' if data['security']['bitlocker_c'] else '<span class="badge badge-danger">NON CONFORME</span>'}</td>
                        <td>{'Protection des données au repos activée.' if data['security']['bitlocker_c'] else 'BitLocker INACTIF. Risque de fuite de données en cas de vol.'}</td>
                    </tr>
                    <tr>
                        <td>Secure Boot</td>
                        <td>{'<span class="badge badge-success">CONFORME</span>' if data['security']['secure_boot'] else '<span class="badge badge-danger">NON CONFORME</span>'}</td>
                        <td>Protection contre l\'altération de la chaîne d\'amorçage.</td>
                    </tr>
                    <tr>
                        <td>Virtualisation Matérielle</td>
                        <td>{'<span class="badge badge-success">ACTIVÉE</span>' if data['security']['hardware_virtualization'] else '<span class="badge badge-danger">DÉSACTIVÉE</span>'}</td>
                        <td>{'Support VT-x/AMD-V actif.' if data['security']['hardware_virtualization'] else 'Bloque l\'isolation du noyau (HVCI/VBS) et les VM.'}</td>
                    </tr>
                </tbody>
            </table>
            {sec_reco_html}
        </div>

        <!-- 2. CPU & RAM / FIRMWARE -->
        <div class="grid">
            <div class="card" style="margin-bottom:0;">
                <h2>2. PROCESSEUR & RAM</h2>
                <p><strong>Processeur :</strong> {data['cpu']['model']}</p>
                <p><strong>Cœurs :</strong> {data['cpu']['physical_cores']} Cœurs Physiques / {data['cpu']['logical_cores']} Threads</p>
                <p><strong>Utilisation RAM :</strong> {data['ram']['used_gb']} / {data['ram']['total_gb']} Go ({data['ram']['percent']}%)</p>
                <div class="progress-bar-bg" style="width: 100%; height: 12px;">
                    <div class="progress-bar-fill" style="width: {data['ram']['percent']}%; background: {'var(--danger)' if data['ram']['percent'] > 85 else 'var(--accent-blue)'};"></div>
                </div>
                {ram_reco_html}
            </div>

            <div class="card" style="margin-bottom:0;">
                <h2>3. FIRMWARE & ENERGIE</h2>
                <p><strong>BIOS :</strong> {data['system']['bios']['vendor']} v{data['system']['bios']['version']}</p>
                <p><strong>Date BIOS :</strong> {data['system']['bios']['date']}</p>
                {f"<p><strong>Niveau Batterie :</strong> {data['battery']['percent']}% (Usure: {data['battery']['wear_level']}%)</p>" if data['battery'] else "<p><strong>Alimentation :</strong> Secteur / PC Fixe</p>"}
                <div class="reco-box">
                    <strong>Analyse Firmware :</strong> BIOS identifié au {data['system']['bios']['date']}. S'assurer régulièrement d'appliquer les mises à jour constructeur.
                </div>
            </div>
        </div>

        <!-- 3. VOLUMES DE STOCKAGE -->
        <div class="card" style="margin-top: 24px;">
            <h2>4. VOLUMES DE STOCKAGE</h2>
            <table>
                <thead>
                    <tr>
                        <th>Lecteur</th>
                        <th>Système de fichiers</th>
                        <th>Capacité</th>
                        <th>Libre</th>
                        <th>Utilisation</th>
                    </tr>
                </thead>
                <tbody>
                    {disks_html}
                </tbody>
            </table>
            {disk_reco_html}
        </div>

        <!-- 4. INTERFACES RÉSEAU -->
        <div class="card">
            <h2>5. INTERFACES RÉSEAU</h2>
            <table>
                <thead>
                    <tr>
                        <th>Interface</th>
                        <th>Description</th>
                        <th>État</th>
                        <th>Vitesse</th>
                        <th>Adresse MAC</th>
                    </tr>
                </thead>
                <tbody>
                    {net_html}
                </tbody>
            </table>
        </div>

        <!-- 5. PLAN D'ACTION / RÉCAPITULATIF FINAL -->
        <div class="card todo-card">
            <h2>📋 PLAN D'ACTION ET RECOMMANDATIONS PRIORITAIRES</h2>
            {todo_html}
        </div>

        <footer>
            Rapport d'audit généré automatiquement par l'outil <strong>{data['audit_meta']['tool']}</strong> made with Love by {data['audit_meta']['author']}.
        </footer>
    </div>

    <script>
        function toggleTheme() {{
            const html = document.documentElement;
            const btn = document.getElementById('themeBtn');
            if (html.getAttribute('data-theme') === 'dark') {{
                html.setAttribute('data-theme', 'light');
                btn.innerHTML = '🌙 Mode Sombre';
            }} else {{
                html.setAttribute('data-theme', 'dark');
                btn.innerHTML = '🌓 Mode Clair';
            }}
        }}

        function downloadPDF() {{
            const element = document.getElementById('reportContent');
            const pdfBtn = document.getElementById('pdfBtn');
            
            // Indication de chargement
            pdfBtn.innerHTML = '⏳ Génération du PDF...';
            pdfBtn.disabled = true;

            // Options de génération du PDF
            const opt = {{
                margin:       [10, 10, 10, 10],
                filename:     `Audit_Sec_{data['system']['hostname']}.pdf`,
                image:        {{ type: 'jpeg', quality: 0.98 }},
                html2canvas:  {{ scale: 2, useCORS: true, logging: false }},
                jsPDF:        {{ unit: 'mm', format: 'a4', orientation: 'portrait' }}
            }};

            // Exécution du téléchargement
            html2pdf().set(opt).from(element).save().then(() => {{
                pdfBtn.innerHTML = '📄 Exporter en PDF';
                pdfBtn.disabled = false;
            }}).catch(err => {{
                console.error('Erreur génération PDF:', err);
                pdfBtn.innerHTML = '📄 Exporter en PDF';
                pdfBtn.disabled = false;
            }});
        }}
    </script>
</body>
</html>
    """

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[+] Rapport HTML automatique généré par l'outil {filename} by Sylvanus ZOADAN")

# ==============================================================================
# POINT D'ENTRÉE CLI (ARGPARSE)
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Hardware-Sec-Audit: Outil CLI d'audit matériel et de sécurité infrastructure."
    )
    parser.add_argument("--json", action="store_true", help="Générer un export JSON structuré")
    parser.add_argument("--md", action="store_true", help="Générer un rapport Markdown")
    parser.add_argument("--html", action="store_true", help="Générer un rapport HTML interactif")
    parser.add_argument("--all", action="store_true", help="Générer tous les formats de rapport disponibles")
    
    args = parser.parse_args()

    # Si aucun argument spécifié, on génère par défaut le Markdown
    if not (args.json or args.md or args.html or args.all):
        args.md = True

    print("[+] Lancement de l'audit matériel et sécurité...")
    audit_data = run_full_audit()

    if args.json or args.all:
        export_json(audit_data)
    if args.md or args.all:
        export_markdown(audit_data)
    if args.html or args.all:
        export_html(audit_data)

    print("[+] Audit terminé avec succès.")