from flask import Flask, render_template_string, jsonify
import requests
from bs4 import BeautifulSoup
import sqlite3
import re
from datetime import datetime, timedelta

app = Flask(__name__)
DB_PATH = "veille.db"

ALERTES_LEGAL = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["paiement électronique", "paiements électroniques", "HPS", "HPS Switch", "SWAM", "Switch Al Maghrib", "réseau bancaire", "réseau VISA", "MasterCard", "paiement electronique", "paiements electroniques"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["fraude", "financement", "données privées", "données personnelles", "donnees personnelles", "donnees privees"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["finance", "économie", "economie"]}
}
ALERTES_REGL = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["paiement électronique", "paiements électroniques", "HPS", "HPS Switch", "SWAM", "Switch Al Maghrib", "réseau bancaire", "réseau VISA", "MasterCard", "paiement electronique", "paiements electroniques", "système de paiement", "moyen de paiement", "interchange", "monétique", "monetique", "mobile payment", "virement", "SRBM", "SIMT"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["fraude", "financement", "données privées", "données personnelles", "donnees personnelles", "établissement de crédit", "agrément", "surveillance", "blanchiment"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["finance", "économie", "economie", "bancaire", "crédit"]}
}
ALERTES_CYBER = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["données personnelles", "donnees personnelles", "violation", "breach", "cyber", "système d'information", "sécurité des systèmes", "paiement électronique", "SWAM", "HPS"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["données", "traitement", "autorisation", "déclaration", "conformité", "télécommunications", "interopérabilité"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["numérique", "digital", "informatique", "réseau"]}
}

def detecter_alerte(texte, alertes):
    t = texte.lower()
    for niveau in ["eleve", "moyen", "faible"]:
        found = [m for m in alertes[niveau]["mots"] if m.lower() in t]
        if found:
            return niveau, list(dict.fromkeys(found))[:3]
    return None, []

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        titre TEXT NOT NULL, url TEXT UNIQUE,
        statut TEXT, source_id TEXT, source_nom TEXT, onglet TEXT,
        alerte_niveau TEXT, alerte_mots TEXT, date_pub TEXT, date_scrape TEXT
    )""")
    conn.commit(); conn.close()

def save_item(c, titre, url, statut, source_id, source_nom, onglet, alertes, date_pub=""):
    niveau, mots = detecter_alerte(titre, alertes)
    try:
        c.execute("""INSERT OR IGNORE INTO items (titre,url,statut,source_id,source_nom,onglet,alerte_niveau,alerte_mots,date_pub,date_scrape) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (titre, url, statut, source_id, source_nom, onglet, niveau, ", ".join(mots) if mots else "", date_pub, datetime.now().strftime("%Y-%m-%d %H:%M")))
        return c.rowcount > 0
    except: return False

def scrape_chambre():
    urls = [
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/projets-de-loi", "statut": "Projet de loi"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/textes-votes-chambre-representants", "statut": "Texte adopté"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/lois-transferts-bureau", "statut": "Déposé au Bureau"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/textes-en-cours-detude-commission", "statut": "En commission"},
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base = "https://www.chambredesrepresentants.ma"
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    titres_exclus = {"projet de loi organique","proposition de loi organique","projet de décret-loi","proposition de loi","projet de loi","recherche dans l'archive","textes finalisés"}
    for src in urls:
        try:
            resp = requests.get(src["url"], headers=headers, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre_raw = a.get_text(strip=True); href = a.get('href', '')
                if not href or len(titre_raw) < 10: continue
                titre = titre_raw
                if "En cours d" in titre:
                    idx = titre.find("Projet")
                    if idx < 0: idx = titre.find("Proposition")
                    if idx > 0: titre = titre[idx:]
                if titre.strip().lower() in titres_exclus: continue
                if not re.search(r'N[°º]\s*\d', titre): continue
                url_item = href if href.startswith('http') else base + href
                if save_item(c, titre, url_item, src["statut"], "chambre", "Chambre des Représentants", "legal", ALERTES_LEGAL, datetime.now().strftime("%d/%m/%Y")): nouveaux += 1
        except Exception as e: print(f"  Erreur Chambre: {e}")
    limite = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='chambre' AND date_scrape < ?", (limite,))
    conn.commit(); conn.close()
    return nouveaux

def scrape_sgg():
    base = "https://www.sgg.gov.ma"; url = f"{base}/Legislation.aspx"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_cibles = ["projet de loi","avant-projet","avant projet","loi organique","dahir","décret","decret"]
    try:
        resp = requests.get(url, headers=headers, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href', '')
            if len(titre) < 10: continue
            t = titre.lower()
            if not (any(m in t for m in mots_cibles) or '.pdf' in href.lower()): continue
            url_item = href if href.startswith('http') else base + "/" + href.lstrip("/")
            if "avant-projet" in t or "avant projet" in t: statut = "Avant-projet de loi"
            elif "projet de loi" in t: statut = "Projet de loi"
            elif "loi organique" in t: statut = "Loi organique"
            else: statut = "Texte législatif"
            if save_item(c, titre, url_item, statut, "sgg", "SGG", "legal", ALERTES_LEGAL): nouveaux += 1
    except Exception as e: print(f"  Erreur SGG: {e}")
    conn.commit(); conn.close()
    return nouveaux

def scrape_bo():
    base = "https://www.bulletinofficiel.ma"; url = f"{base}/fr/derniers-bulletins"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_paiement = ["paiement","monétique","monetique","bancaire","crédit","bank","financier","dahir","décret"]
    try:
        resp = requests.get(url, headers=headers, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href', '')
            if len(titre) < 10: continue
            if not any(m in titre.lower() for m in mots_paiement): continue
            url_item = href if href.startswith('http') else base + href
            if save_item(c, titre, url_item, "Bulletin Officiel", "bo", "Bulletin Officiel", "legal", ALERTES_LEGAL): nouveaux += 1
    except Exception as e: print(f"  Erreur BO: {e}")
    conn.commit(); conn.close()
    return nouveaux

def scrape_dgssi():
    base = "https://www.dgssi.gov.ma"; url = f"{base}/fr/textes-legislatifs-et-reglementaires/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["arrêté","arrete","circulaire","loi","décret","decret","dahir","instruction","directive","ordonnance"]
    try:
        resp = requests.get(url, headers=headers, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href', '')
            if len(titre) < 10 or not href: continue
            t = titre.lower()
            if not (any(m in t for m in mots_doc) or '.pdf' in href.lower()): continue
            url_item = href if href.startswith('http') else base + href
            if "arrêté" in t or "arrete" in t: statut = "Arrêté"
            elif "circulaire" in t: statut = "Circulaire"
            elif "dahir" in t or "loi" in t: statut = "Loi / Dahir"
            elif "décret" in t or "decret" in t: statut = "Décret"
            else: statut = "Document réglementaire"
            if save_item(c, titre, url_item, statut, "dgssi", "DGSSI", "cyber", ALERTES_CYBER): nouveaux += 1
    except Exception as e: print(f"  Erreur DGSSI: {e}")
    conn.commit(); conn.close()
    return nouveaux

def scrape_bam():
    base = "https://www.bkam.ma"
    url = f"{base}/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["circulaire","lettre circulaire","décision réglementaire","décision reglementaire","décision règlementaire","directive","instruction","note de service","dahir"]
    try:
        resp = requests.get(url, headers=headers, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href', '')
            if len(titre) < 10 or titre in seen: continue
            t = titre.lower(); h = href.lower()
            if not ('/content/download/' in h or any(m in t for m in mots_doc)): continue
            seen.add(titre)
            url_item = href if href.startswith('http') else base + href
            if "circulaire du wali" in t: statut = "Circulaire du Wali"
            elif "lettre circulaire" in t: statut = "Lettre circulaire"
            elif "circulaire" in t: statut = "Circulaire BAM"
            elif any(x in t for x in ["décision réglementaire","décision règlementaire","décision reglementaire"]): statut = "Décision réglementaire"
            elif "directive" in t: statut = "Directive BAM"
            elif "instruction" in t: statut = "Instruction BAM"
            else: statut = "Document BAM"
            if save_item(c, titre, url_item, statut, "bam_paiement", "Bank Al-Maghrib", "reglementaire", ALERTES_REGL): nouveaux += 1
    except Exception as e: print(f"  Erreur BAM: {e}")
    conn.commit(); conn.close()
    return nouveaux

def get_items(onglet, source_id=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
    if source_id:
        items = c.execute("SELECT * FROM items WHERE onglet=? AND source_id=? ORDER BY date_scrape DESC", (onglet, source_id)).fetchall()
    else:
        items = c.execute("SELECT * FROM items WHERE onglet=? ORDER BY date_scrape DESC", (onglet,)).fetchall()
    conn.close(); return items

def get_stats(lst):
    return {"total": len(lst), "eleve": sum(1 for x in lst if x["alerte_niveau"]=="eleve"), "moyen": sum(1 for x in lst if x["alerte_niveau"]=="moyen"), "faible": sum(1 for x in lst if x["alerte_niveau"]=="faible")}

@app.route("/")
def dashboard():
    chambre = get_items("legal","chambre"); sgg = get_items("legal","sgg"); bo = get_items("legal","bo")
    bam = get_items("reglementaire","bam_paiement"); dgssi = get_items("cyber","dgssi")
    return render_template_string(HTML,
        chambre=chambre, sgg=sgg, bo=bo, bam=bam, dgssi=dgssi,
        stats_legal=get_stats(list(chambre)+list(sgg)+list(bo)),
        stats_regl=get_stats(list(bam)),
        stats_cyber=get_stats(list(dgssi)),
        alertes_legal=ALERTES_LEGAL, alertes_regl=ALERTES_REGL, alertes_cyber=ALERTES_CYBER,
        last_update=datetime.now().strftime("%d/%m/%Y %H:%M"))

@app.route("/api/scrape")
def api_scrape():
    n1=scrape_chambre(); n2=scrape_sgg(); n3=scrape_bo(); n4=scrape_bam(); n5=scrape_dgssi()
    total = n1+n2+n3+n4+n5
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    eleves = c.execute("SELECT COUNT(*) FROM items WHERE alerte_niveau='eleve'").fetchone()[0]
    conn.close()
    return jsonify({"status":"ok","nouveaux":total,"detail":{"chambre":n1,"sgg":n2,"bo":n3,"bam":n4,"dgssi":n5},"alertes_elevees":eleves})

@app.route("/api/demo")
def api_demo():
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    demo=[
        ("Projet de loi N°103.22 relatif aux paiements électroniques et au réseau HPS Switch","https://www.chambredesrepresentants.ma/fr/loi10322","Projet de loi","chambre","Chambre des Représentants","legal"),
        ("Projet de loi N°61.25 modifiant la loi N°103.14 portant création de l'ANSS","https://www.chambredesrepresentants.ma/fr/loi6125","En commission","chambre","Chambre des Représentants","legal"),
        ("Projet de loi N°55.19 relatif à la protection des données personnelles","https://www.chambredesrepresentants.ma/fr/loi5519","Texte adopté","chambre","Chambre des Représentants","legal"),
        ("Avant-projet de loi sur les services de paiement électronique et le financement participatif","https://www.sgg.gov.ma/avant-projet-paiement.pdf","Avant-projet de loi","sgg","SGG","legal"),
        ("Dahir n° 1-05-178 portant promulgation de la loi n° 103-12 relative aux établissements de crédit","https://www.bulletinofficiel.ma/fr/bo/6024","Bulletin Officiel","bo","Bulletin Officiel","legal"),
        ("Décision réglementaire N°392/W/2018 relative au paiement mobile domestique","https://www.bkam.ma/content/download/612250/Decision392.pdf","Décision réglementaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Lettre circulaire N° LC/BKAM/2018/70 relative au paiement mobile domestique","https://www.bkam.ma/content/download/612251/LC-BKAM-2018-70.pdf","Lettre circulaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Circulaire N° 14/G/06 relative à la mise en place du SRBM — Système des Règlements Bruts du Maroc","https://www.bkam.ma/content/download/498845/CIRCULAIRE_SRBM.pdf","Circulaire BAM","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Décision règlementaire relative aux frais d'interchange monétique domestique","https://www.bkam.ma/content/download/834939/Decision-interchange.pdf","Décision réglementaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Arrêté du Chef du Gouvernement relatif à la sécurité des systèmes d'information","https://www.dgssi.gov.ma/arrete-ssi.pdf","Arrêté","dgssi","DGSSI","cyber"),
        ("Loi n° 43-20 relative aux services de confiance pour les transactions électroniques","https://www.dgssi.gov.ma/loi-43-20.pdf","Loi / Dahir","dgssi","DGSSI","cyber"),
    ]
    inserted=0
    for d in demo:
        titre,url,statut,source_id,source_nom,onglet=d
        alertes=ALERTES_LEGAL if onglet=="legal" else (ALERTES_REGL if onglet=="reglementaire" else ALERTES_CYBER)
        if save_item(c,titre,url,statut,source_id,source_nom,onglet,alertes): inserted+=1
    conn.commit(); conn.close()
    return jsonify({"status":"ok","inseres":inserted})

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SWAM — Radar Réglementaire</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--cyan:#00BCD4;--violet:#7B2D8B;--cyan-light:#E0F7FA;--violet-light:#F3E5F5;--cyan-mid:rgba(0,188,212,.12);--grad:linear-gradient(135deg,#00BCD4,#7B2D8B);--bg:#F4F6F9;--surface:#FFFFFF;--surface2:#F8F9FB;--border:#E2E8F0;--border2:#CBD5E0;--text:#1A202C;--text2:#4A5568;--text3:#A0AEC0;--eleve:#E53E3E;--moyen:#DD6B20;--faible:#B7791F;--eleve-bg:#FFF5F5;--eleve-bd:#FED7D7;--moyen-bg:#FFFAF0;--moyen-bd:#FEEBC8;--faible-bg:#FFFFF0;--faible-bd:#FEFCBF;--sw:240px;--th:60px}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Outfit',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.layout{display:grid;grid-template-columns:var(--sw) 1fr;grid-template-rows:var(--th) 1fr;min-height:100vh}
.topbar{grid-column:1/-1;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 1.5rem;gap:1rem;position:sticky;top:0;z-index:100;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.logo-block{display:flex;align-items:center;gap:10px;margin-right:auto}
.logo-svg{height:30px;flex-shrink:0}
.logo-sep{width:1px;height:24px;background:var(--border2)}
.logo-text{font-size:.72rem;color:var(--text3);font-weight:500;letter-spacing:.05em;line-height:1.4}
.logo-text strong{display:block;color:var(--text);font-weight:700;font-size:.82rem;letter-spacing:.02em}
.topbar-meta{font-family:'JetBrains Mono',monospace;font-size:.65rem;color:var(--text3);background:var(--surface2);border:1px solid var(--border);padding:4px 10px;border-radius:6px}
.btn-refresh{display:flex;align-items:center;gap:6px;background:var(--grad);color:#fff;border:none;padding:8px 16px;border-radius:8px;font-family:'Outfit',sans-serif;font-size:.78rem;font-weight:600;cursor:pointer;transition:opacity .2s,transform .15s;letter-spacing:.02em}
.btn-refresh:hover{opacity:.88;transform:translateY(-1px)}.btn-refresh:disabled{opacity:.4;cursor:wait}
.sidebar{background:var(--surface);border-right:1px solid var(--border);padding:1.25rem 0;position:sticky;top:var(--th);height:calc(100vh - var(--th));overflow-y:auto;display:flex;flex-direction:column}
.sb-sec{padding:.5rem 1rem .2rem;font-size:.6rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.12em;margin-top:.5rem}
.nav-btn{display:flex;align-items:center;gap:9px;padding:.55rem 1.1rem;font-size:.8rem;font-weight:500;color:var(--text2);background:none;border:none;border-left:2px solid transparent;width:100%;text-align:left;cursor:pointer;font-family:'Outfit',sans-serif;transition:all .15s;text-decoration:none}
.nav-btn:hover{color:var(--text);background:var(--surface2)}.nav-btn.actif{color:var(--cyan);background:var(--cyan-mid);border-left-color:var(--cyan);font-weight:600}
.nav-ico{width:16px;height:16px;flex-shrink:0;opacity:.65}.nav-btn.actif .nav-ico{opacity:1}
.nav-cnt{margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:.65rem;font-weight:600;padding:1px 7px;border-radius:10px;background:var(--surface2);color:var(--text3);border:1px solid var(--border)}
.nav-btn.actif .nav-cnt{background:var(--cyan-light);color:var(--cyan);border-color:rgba(0,188,212,.3)}
.sb-alerts{margin:1rem 1rem 0;padding:.75rem;background:var(--surface2);border:1px solid var(--border);border-radius:8px}
.sb-row{display:flex;align-items:center;justify-content:space-between;padding:3px 0;font-size:.73rem}
.sb-dot{width:7px;height:7px;border-radius:50%;margin-right:7px;flex-shrink:0}.sb-dot.e{background:var(--eleve)}.sb-dot.m{background:var(--moyen)}.sb-dot.f{background:var(--faible)}
.sb-lbl{display:flex;align-items:center;color:var(--text2)}.sb-n{font-weight:700;font-family:'JetBrains Mono',monospace;font-size:.72rem}.sb-n.e{color:var(--eleve)}.sb-n.m{color:var(--moyen)}.sb-n.f{color:var(--faible)}
.main{padding:1.75rem 2rem;overflow-y:auto}
.tab-panel{display:none;animation:fi .2s ease}.tab-panel.actif{display:block}
@keyframes fi{from{opacity:.5;transform:translateY(4px)}to{opacity:1;transform:none}}
.page-hd{margin-bottom:1.5rem;display:flex;align-items:flex-end;justify-content:space-between;gap:1rem}
.page-title{font-size:1.4rem;font-weight:800;letter-spacing:-.02em;color:var(--text)}
.page-title .acc{background:var(--grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.page-sub{font-size:.78rem;color:var(--text3);margin-top:.2rem;font-weight:400}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.75rem;margin-bottom:1.5rem}
.sc{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.9rem 1rem;position:relative;overflow:hidden;transition:box-shadow .2s}
.sc:hover{box-shadow:0 2px 12px rgba(0,0,0,.06)}.sc::after{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.sc.t::after{background:var(--grad)}.sc.e::after{background:var(--eleve)}.sc.m::after{background:var(--moyen)}.sc.f::after{background:var(--faible)}
.sc-lbl{font-size:.62rem;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;font-weight:600;margin-bottom:.35rem}
.sc-num{font-size:1.75rem;font-weight:800;font-family:'JetBrains Mono',monospace;line-height:1}
.sc.t .sc-num{color:var(--cyan)}.sc.e .sc-num{color:var(--eleve)}.sc.m .sc-num{color:var(--moyen)}.sc.f .sc-num{color:var(--faible)}
.filter-bar{display:flex;gap:.5rem;align-items:center;margin-bottom:1.25rem;flex-wrap:wrap}
.fl{font-size:.63rem;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;font-weight:700;margin-right:.2rem}
.pill{display:flex;align-items:center;gap:5px;padding:5px 12px;border-radius:20px;font-size:.72rem;font-weight:600;cursor:pointer;border:1px solid;background:transparent;font-family:'Outfit',sans-serif;transition:all .15s}
.pill.tous{color:var(--cyan);border-color:rgba(0,188,212,.4)}.pill.eleve{color:var(--eleve);border-color:rgba(229,62,62,.4)}.pill.moyen{color:var(--moyen);border-color:rgba(221,107,32,.4)}.pill.faible{color:var(--faible);border-color:rgba(183,121,31,.4)}
.pill.tous.actif,.pill.tous:hover{background:var(--cyan-light);border-color:var(--cyan)}.pill.eleve.actif,.pill.eleve:hover{background:var(--eleve-bg);border-color:var(--eleve)}.pill.moyen.actif,.pill.moyen:hover{background:var(--moyen-bg);border-color:var(--moyen)}.pill.faible.actif,.pill.faible:hover{background:var(--faible-bg);border-color:var(--faible)}
.pn{font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700}
.source-group{margin-bottom:1.75rem}
.source-hd{display:flex;align-items:center;gap:.6rem;padding:.6rem .85rem;margin-bottom:.6rem;background:var(--surface2);border:1px solid var(--border);border-radius:8px}
.src-ico{font-size:13px}.src-name{font-size:.75rem;font-weight:700;color:var(--text);text-transform:uppercase;letter-spacing:.05em}.src-cnt{font-size:.65rem;color:var(--text3);font-family:'JetBrains Mono',monospace}
.src-link{margin-left:auto;font-size:.65rem;color:var(--cyan);text-decoration:none;font-weight:600;opacity:.8;transition:opacity .15s}.src-link:hover{opacity:1;text-decoration:underline}
.feed{display:flex;flex-direction:column;gap:.45rem}
.item{display:grid;grid-template-columns:3px 1fr auto;gap:.7rem;align-items:start;background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:.85rem .95rem;transition:border-color .15s,box-shadow .15s}
.item:hover{border-color:var(--border2);box-shadow:0 2px 8px rgba(0,0,0,.05)}
.ibar{border-radius:3px;align-self:stretch;min-height:28px}.ibar.eleve{background:var(--eleve)}.ibar.moyen{background:var(--moyen)}.ibar.faible{background:var(--faible)}.ibar.none{background:var(--border2)}
.ititre{font-size:.84rem;font-weight:500;line-height:1.5;color:var(--text);margin-bottom:.35rem}
.ititre a{color:inherit;text-decoration:none}.ititre a:hover{color:var(--cyan)}
.itags{display:flex;gap:.35rem;flex-wrap:wrap;align-items:center}
.tag{font-size:.62rem;font-weight:700;padding:2px 8px;border-radius:4px;letter-spacing:.03em;border:1px solid}
.tag.eleve{background:var(--eleve-bg);color:var(--eleve);border-color:var(--eleve-bd)}.tag.moyen{background:var(--moyen-bg);color:var(--moyen);border-color:var(--moyen-bd)}.tag.faible{background:var(--faible-bg);color:var(--faible);border-color:var(--faible-bd)}
.tag.st{background:var(--surface2);color:var(--text2);border-color:var(--border);font-weight:500;font-size:.63rem;letter-spacing:0;text-transform:none}
.tag.kw{background:transparent;border-color:transparent;color:var(--text3);font-style:italic;font-weight:400;font-size:.62rem;letter-spacing:0;text-transform:none;padding-left:0}
.idate{font-family:'JetBrains Mono',monospace;font-size:.6rem;color:var(--text3);white-space:nowrap;padding-top:2px}
.extern{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:.95rem 1.1rem;display:flex;align-items:center;gap:1rem;margin-bottom:.6rem}
.extern.c{border-left:3px solid var(--cyan)}.extern.v{border-left:3px solid var(--violet)}
.ebd{flex:1}.etitle{font-size:.85rem;font-weight:700;color:var(--text);margin-bottom:.2rem}.edesc{font-size:.74rem;color:var(--text2);line-height:1.5}
.ebtn{background:var(--surface2);color:var(--cyan);border:1px solid rgba(0,188,212,.3);padding:6px 13px;border-radius:6px;font-size:.7rem;font-weight:700;text-decoration:none;white-space:nowrap;transition:all .15s;font-family:'Outfit',sans-serif}
.ebtn:hover{background:var(--cyan-light);border-color:var(--cyan)}
.empty{text-align:center;padding:2rem;background:var(--surface);border:1px dashed var(--border2);border-radius:9px;color:var(--text3);font-size:.8rem}
.empty strong{display:block;color:var(--text2);font-size:.88rem;margin-bottom:.3rem;font-weight:700}
.placeholder{background:var(--surface);border:1px dashed var(--border2);border-radius:9px;padding:1.5rem;text-align:center;color:var(--text3);font-size:.78rem}
.placeholder strong{display:block;color:var(--text2);font-size:.85rem;margin-bottom:.3rem;font-weight:700}
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--surface);color:var(--text);padding:.8rem 1.2rem;border-radius:10px;border:1px solid var(--border);border-top:2px solid var(--cyan);font-size:.78rem;font-weight:500;box-shadow:0 8px 24px rgba(0,0,0,.12);transform:translateY(70px);opacity:0;transition:all .3s cubic-bezier(.175,.885,.32,1.275);z-index:999;max-width:280px}
.toast.show{transform:translateY(0);opacity:1}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:4px}
</style>
</head>
<body>
<div class="layout">

<header class="topbar">
  <div class="logo-block">
    <svg class="logo-svg" viewBox="0 0 130 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <text x="1" y="28" font-family="'Outfit',sans-serif" font-size="30" font-weight="800" fill="#00BCD4" letter-spacing="-1">sw</text>
      <text x="43" y="28" font-family="'Outfit',sans-serif" font-size="30" font-weight="800" fill="#7B2D8B" letter-spacing="-1">am</text>
      <text x="1" y="38" font-family="'Outfit',sans-serif" font-size="7" font-weight="500" fill="#718096" letter-spacing="2.2">SWITCH AL MAGHRIB</text>
    </svg>
    <div class="logo-sep"></div>
    <div class="logo-text"><strong>Radar Réglementaire</strong>Veille Légale · Réglementaire · Cyber</div>
  </div>
  <span class="topbar-meta">Mise à jour : {{ last_update }}</span>
  <button class="btn-refresh" onclick="lancerScrape(this)">↻ Actualiser</button>
</header>

<nav class="sidebar">
  <div class="sb-sec">Modules</div>
  <button class="nav-btn actif" onclick="changerOnglet('legal',this)">
    <svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1L3 5v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V5L12 1z"/></svg>
    Veille Légale<span class="nav-cnt">{{ stats_legal.total }}</span>
  </button>
  <button class="nav-btn" onclick="changerOnglet('reglementaire',this)">
    <svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
    Veille Réglementaire<span class="nav-cnt">{{ stats_regl.total }}</span>
  </button>
  <button class="nav-btn" onclick="changerOnglet('cyber',this)">
    <svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
    Cyber &amp; Données<span class="nav-cnt">{{ stats_cyber.total }}</span>
  </button>
  <button class="nav-btn" onclick="changerOnglet('normative',this)">
    <svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 4 10 15 15 0 0 1-4 10 15 15 0 0 1-4-10 15 15 0 0 1 4-10z"/></svg>
    Normative Internationale<span class="nav-cnt">—</span>
  </button>

  <div class="sb-sec" style="margin-top:1rem">Alertes globales</div>
  <div class="sb-alerts">
    <div class="sb-row"><span class="sb-lbl"><span class="sb-dot e"></span>Élevé</span><span class="sb-n e">{{ stats_legal.eleve + stats_regl.eleve + stats_cyber.eleve }}</span></div>
    <div class="sb-row"><span class="sb-lbl"><span class="sb-dot m"></span>Moyen</span><span class="sb-n m">{{ stats_legal.moyen + stats_regl.moyen + stats_cyber.moyen }}</span></div>
    <div class="sb-row"><span class="sb-lbl"><span class="sb-dot f"></span>Faible</span><span class="sb-n f">{{ stats_legal.faible + stats_regl.faible + stats_cyber.faible }}</span></div>
  </div>

  <div class="sb-sec" style="margin-top:1rem">Sources</div>
  <a class="nav-btn" href="https://www.chambredesrepresentants.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Chambre des Reps.<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.sgg.gov.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>SGG<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.bulletinofficiel.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>Bulletin Officiel<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.bkam.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>Bank Al-Maghrib<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.cndp.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>CNDP<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.dgssi.gov.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>DGSSI<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.anrt.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 6l5 5m0 0l5-5m-5 5V2M23 18l-5-5m0 0l-5 5m5-5v8"/></svg>ANRT<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.oc.gov.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>Office des Changes<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://conseil-concurrence.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Conseil Concurrence<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
</nav>

<main class="main">

  <!-- LÉGAL -->
  <div class="tab-panel actif" id="tab-legal">
    <div class="page-hd"><div><h1 class="page-title">Veille <span class="acc">Légale</span></h1><p class="page-sub">Chambre des Représentants · SGG · Bulletin Officiel</p></div></div>
    <div class="stats-grid">
      <div class="sc t"><div class="sc-lbl">Total</div><div class="sc-num">{{ stats_legal.total }}</div></div>
      <div class="sc e"><div class="sc-lbl">Élevé</div><div class="sc-num">{{ stats_legal.eleve }}</div></div>
      <div class="sc m"><div class="sc-lbl">Moyen</div><div class="sc-num">{{ stats_legal.moyen }}</div></div>
      <div class="sc f"><div class="sc-lbl">Faible</div><div class="sc-num">{{ stats_legal.faible }}</div></div>
    </div>
    <div class="filter-bar">
      <span class="fl">Filtre</span>
      <button class="pill tous actif" onclick="filtrer('legal','tous',this)"><span class="pn">{{ stats_legal.total }}</span> Tous</button>
      <button class="pill eleve" onclick="filtrer('legal','eleve',this)"><span class="pn">{{ stats_legal.eleve }}</span> Élevé</button>
      <button class="pill moyen" onclick="filtrer('legal','moyen',this)"><span class="pn">{{ stats_legal.moyen }}</span> Moyen</button>
      <button class="pill faible" onclick="filtrer('legal','faible',this)"><span class="pn">{{ stats_legal.faible }}</span> Faible</button>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">⚖️</span><span class="src-name">Chambre des Représentants</span><span class="src-cnt">{{ chambre|length }} doc.</span><a href="https://www.chambredesrepresentants.ma/fr/action-legislative" target="_blank" class="src-link">↗ Accéder</a></div>
      {% if chambre %}<div class="feed" id="legal-chambre">{% for item in chambre %}<div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}"><div class="ibar {{ item.alerte_niveau or 'none' }}"></div><div class="item-body"><div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div><div class="itags">{% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}<span class="tag st">{{ item.statut }}</span>{% if item.alerte_mots %}<span class="tag kw">{{ item.alerte_mots[:60] }}</span>{% endif %}</div></div><div class="idate">{{ item.date_pub or item.date_scrape[:10] }}</div></div>{% endfor %}</div>
      {% else %}<div class="empty"><strong>Aucun projet de loi</strong>Clique sur ↻ Actualiser</div>{% endif %}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📋</span><span class="src-name">SGG — Secrétariat Général du Gouvernement</span><span class="src-cnt">{{ sgg|length }} doc.</span><a href="https://www.sgg.gov.ma/Legislation.aspx" target="_blank" class="src-link">↗ Accéder</a></div>
      {% if sgg %}<div class="feed" id="legal-sgg">{% for item in sgg %}<div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}"><div class="ibar {{ item.alerte_niveau or 'none' }}"></div><div class="item-body"><div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div><div class="itags">{% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}<span class="tag st">{{ item.statut }}</span></div></div><div class="idate">{{ item.date_scrape[:10] }}</div></div>{% endfor %}</div>
      {% else %}<div class="empty"><strong>Aucun texte SGG</strong>Clique sur ↻ Actualiser</div>{% endif %}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📰</span><span class="src-name">Bulletin Officiel</span><span class="src-cnt">{{ bo|length }} doc.</span><a href="https://www.bulletinofficiel.ma" target="_blank" class="src-link">↗ Accéder</a></div>
      {% if bo %}<div class="feed" id="legal-bo">{% for item in bo %}<div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}"><div class="ibar {{ item.alerte_niveau or 'none' }}"></div><div class="item-body"><div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div><div class="itags">{% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}<span class="tag st">{{ item.statut }}</span></div></div><div class="idate">{{ item.date_scrape[:10] }}</div></div>{% endfor %}</div>
      {% else %}<div class="empty"><strong>Aucun texte Bulletin Officiel</strong>Clique sur ↻ Actualiser</div>{% endif %}
    </div>
  </div>

  <!-- RÉGLEMENTAIRE -->
  <div class="tab-panel" id="tab-reglementaire">
    <div class="page-hd"><div><h1 class="page-title">Veille <span class="acc">Réglementaire</span></h1><p class="page-sub">Bank Al-Maghrib · Conseil de la Concurrence · Office des Changes</p></div></div>
    <div class="stats-grid">
      <div class="sc t"><div class="sc-lbl">Total</div><div class="sc-num">{{ stats_regl.total }}</div></div>
      <div class="sc e"><div class="sc-lbl">Élevé</div><div class="sc-num">{{ stats_regl.eleve }}</div></div>
      <div class="sc m"><div class="sc-lbl">Moyen</div><div class="sc-num">{{ stats_regl.moyen }}</div></div>
      <div class="sc f"><div class="sc-lbl">Faible</div><div class="sc-num">{{ stats_regl.faible }}</div></div>
    </div>
    <div class="filter-bar">
      <span class="fl">Filtre</span>
      <button class="pill tous actif" onclick="filtrer('reglementaire','tous',this)"><span class="pn">{{ stats_regl.total }}</span> Tous</button>
      <button class="pill eleve" onclick="filtrer('reglementaire','eleve',this)"><span class="pn">{{ stats_regl.eleve }}</span> Élevé</button>
      <button class="pill moyen" onclick="filtrer('reglementaire','moyen',this)"><span class="pn">{{ stats_regl.moyen }}</span> Moyen</button>
      <button class="pill faible" onclick="filtrer('reglementaire','faible',this)"><span class="pn">{{ stats_regl.faible }}</span> Faible</button>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🏦</span><span class="src-name">Bank Al-Maghrib — Systèmes de paiement</span><span class="src-cnt">{{ bam|length }} doc.</span><a href="https://www.bkam.ma/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement" target="_blank" class="src-link">↗ Accéder</a></div>
      {% if bam %}<div class="feed" id="regl-bam">{% for item in bam %}<div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}"><div class="ibar {{ item.alerte_niveau or 'none' }}"></div><div class="item-body"><div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div><div class="itags">{% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_regl[item.alerte_niveau].label }}</span>{% endif %}<span class="tag st">{{ item.statut }}</span>{% if item.alerte_mots %}<span class="tag kw">{{ item.alerte_mots[:60] }}</span>{% endif %}</div></div><div class="idate">{{ item.date_scrape[:10] }}</div></div>{% endfor %}</div>
      {% else %}<div class="empty"><strong>Aucune circulaire BAM</strong>Clique sur ↻ Actualiser</div>{% endif %}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">⚖️</span><span class="src-name">Conseil de la Concurrence</span></div>
      <div class="extern c"><div class="ebd"><div class="etitle">Conseil de la Concurrence — Maroc</div><div class="edesc">Décisions sur le marché de l'acquisition (CMI, EDP). Décision N°152/D/2024 en cours de suivi jusqu'au 1er novembre 2025.</div></div><a href="https://conseil-concurrence.ma/" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">💱</span><span class="src-name">Office des Changes</span></div>
      <div class="extern v"><div class="ebd"><div class="etitle">Office des Changes — Réglementation des changes</div><div class="edesc">Instructions générales des opérations de change (IGOC 2024). Applicable aux transactions transfrontalières via SWAM.</div></div><a href="https://www.oc.gov.ma/fr/reglementation" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
  </div>

  <!-- CYBER & DONNÉES -->
  <div class="tab-panel" id="tab-cyber">
    <div class="page-hd"><div><h1 class="page-title">Cyber <span class="acc">&amp; Données</span></h1><p class="page-sub">DGSSI · CNDP · ANRT</p></div></div>
    <div class="stats-grid">
      <div class="sc t"><div class="sc-lbl">Total</div><div class="sc-num">{{ stats_cyber.total }}</div></div>
      <div class="sc e"><div class="sc-lbl">Élevé</div><div class="sc-num">{{ stats_cyber.eleve }}</div></div>
      <div class="sc m"><div class="sc-lbl">Moyen</div><div class="sc-num">{{ stats_cyber.moyen }}</div></div>
      <div class="sc f"><div class="sc-lbl">Faible</div><div class="sc-num">{{ stats_cyber.faible }}</div></div>
    </div>
    <div class="filter-bar">
      <span class="fl">Filtre</span>
      <button class="pill tous actif" onclick="filtrer('cyber','tous',this)"><span class="pn">{{ stats_cyber.total }}</span> Tous</button>
      <button class="pill eleve" onclick="filtrer('cyber','eleve',this)"><span class="pn">{{ stats_cyber.eleve }}</span> Élevé</button>
      <button class="pill moyen" onclick="filtrer('cyber','moyen',this)"><span class="pn">{{ stats_cyber.moyen }}</span> Moyen</button>
      <button class="pill faible" onclick="filtrer('cyber','faible',this)"><span class="pn">{{ stats_cyber.faible }}</span> Faible</button>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🔒</span><span class="src-name">DGSSI — Sécurité des Systèmes d'Information</span><span class="src-cnt">{{ dgssi|length }} doc.</span><a href="https://www.dgssi.gov.ma/fr/textes-legislatifs-et-reglementaires/" target="_blank" class="src-link">↗ Accéder</a></div>
      {% if dgssi %}<div class="feed" id="cyber-dgssi">{% for item in dgssi %}<div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}"><div class="ibar {{ item.alerte_niveau or 'none' }}"></div><div class="item-body"><div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div><div class="itags">{% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_cyber[item.alerte_niveau].label }}</span>{% endif %}<span class="tag st">{{ item.statut }}</span></div></div><div class="idate">{{ item.date_scrape[:10] }}</div></div>{% endfor %}</div>
      {% else %}<div class="empty"><strong>Aucun document DGSSI</strong>Clique sur ↻ Actualiser</div>{% endif %}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🛡️</span><span class="src-name">CNDP — Protection des Données Personnelles</span></div>
      <div class="extern c"><div class="ebd"><div class="etitle">CNDP — Commission Nationale de contrôle de la Protection des Données</div><div class="edesc">Loi 09-08. Campagnes sectorielles actives depuis février 2025. Sanctions jusqu'à 300 000 MAD. Applicable au traitement des données clients SWAM.</div></div><a href="https://www.cndp.ma" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📡</span><span class="src-name">ANRT — Agence Nationale de Réglementation des Télécommunications</span></div>
      <div class="extern v"><div class="ebd"><div class="etitle">ANRT — Réglementation Télécoms</div><div class="edesc">Interopérabilité des réseaux, licences opérateurs mobiles, 5G lancée en juillet 2025. Impact sur les canaux de paiement mobile transitant par SWAM.</div></div><a href="https://www.anrt.ma" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
  </div>

  <!-- NORMATIVE -->
  <div class="tab-panel" id="tab-normative">
    <div class="page-hd"><div><h1 class="page-title">Normative <span class="acc">Internationale</span></h1><p class="page-sub">BIS-CPMI · ISO · PCI-DSS · SWIFT CSP</p></div></div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🌐</span><span class="src-name">BIS — CPMI</span></div>
      <div class="extern c"><div class="ebd"><div class="etitle">Committee on Payments and Market Infrastructures</div><div class="edesc">Standards internationaux sur les systèmes de paiement, interopérabilité, risques opérationnels et surveillance des infrastructures de marché.</div></div><a href="https://www.bis.org/cpmi/about/overview.htm" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🔐</span><span class="src-name">PCI-DSS &amp; ISO 27001</span></div>
      <div class="extern v"><div class="ebd"><div class="etitle">PCI Security Standards Council</div><div class="edesc">PCI-DSS v4.0 (paiements par carte), ISO 27001 (sécurité de l'information), SWIFT CSP (Customer Security Programme). Référentiels applicables à l'infrastructure SWAM.</div></div><a href="https://www.pcisecuritystandards.org" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">💳</span><span class="src-name">Schemes Cartes — Règles d'interopérabilité</span></div>
      <div class="placeholder"><strong>Contenu à venir</strong>Règles Visa, Mastercard et normes d'interopérabilité applicables au switch national.</div>
    </div>
  </div>

</main>
</div>
<div class="toast" id="toast"></div>
<script>
function changerOnglet(id,btn){document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('actif'));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('actif'));btn.classList.add('actif');document.getElementById('tab-'+id).classList.add('actif');}
function filtrer(onglet,type,btn){btn.closest('.tab-panel').querySelectorAll('.pill').forEach(b=>b.classList.remove('actif'));btn.classList.add('actif');document.getElementById('tab-'+onglet).querySelectorAll('.item').forEach(card=>{card.style.display=(type==='tous'||card.dataset.alerte===type)?'':'none';});}
async function lancerScrape(btn){btn.disabled=true;showToast('Scraping en cours...');try{const d=await fetch('/api/scrape').then(r=>r.json());showToast('✓ '+d.nouveaux+' nouveaux'+(d.alertes_elevees>0?' · 🔴 '+d.alertes_elevees+' alertes !':''));setTimeout(()=>location.reload(),2800);}catch(e){showToast('✗ Erreur lors du scraping');}finally{btn.disabled=false;}}
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),4000);}
</script>
</body>
</html>"""

if __name__ == "__main__":
    init_db()
    print("\n✅  Base prête : veille.db")
    print("🚀  http://localhost:5000")
    print("📦  Données test : http://localhost:5000/api/demo\n")
    app.run(debug=True, port=5000)
