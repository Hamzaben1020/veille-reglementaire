from flask import Flask, render_template_string, jsonify
import requests
from bs4 import BeautifulSoup
import sqlite3
import re
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
DB_PATH = "veille.db"

ALERTES_LEGAL = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["paiement électronique","paiements électroniques","HPS","HPS Switch","SWAM","Switch Al Maghrib","réseau bancaire","réseau VISA","MasterCard","paiement electronique","paiements electroniques"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["fraude","financement","données privées","données personnelles","donnees personnelles","donnees privees"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["finance","économie","economie"]}
}
ALERTES_REGL = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["paiement électronique","paiements électroniques","HPS","HPS Switch","SWAM","Switch Al Maghrib","réseau bancaire","réseau VISA","MasterCard","paiement electronique","paiements electroniques","système de paiement","moyen de paiement","interchange","monétique","monetique","mobile payment","virement","SRBM","SIMT","acquisition","CMI","établissement de paiement"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["fraude","financement","données personnelles","donnees personnelles","établissement de crédit","agrément","surveillance","blanchiment","concentration économique"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["finance","économie","economie","bancaire","crédit"]}
}
ALERTES_CYBER = {
    "eleve": {"label": "ÉLEVÉ", "emoji": "🔴", "mots": ["données personnelles","donnees personnelles","violation","breach","cyber","système d'information","sécurité des systèmes","paiement électronique","SWAM","HPS"]},
    "moyen": {"label": "MOYEN", "emoji": "🟠", "mots": ["données","traitement","autorisation","déclaration","conformité","télécommunications","interopérabilité"]},
    "faible": {"label": "FAIBLE", "emoji": "🟡", "mots": ["numérique","digital","informatique","réseau"]}
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def detecter_alerte(texte, alertes):
    t = texte.lower()
    for niveau in ["eleve","moyen","faible"]:
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
        c.execute("""INSERT OR IGNORE INTO items (titre,url,statut,source_id,source_nom,onglet,alerte_niveau,alerte_mots,date_pub,date_scrape)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (titre, url, statut, source_id, source_nom, onglet, niveau,
             ", ".join(mots) if mots else "", date_pub, datetime.now().strftime("%Y-%m-%d %H:%M")))
        return c.rowcount > 0
    except: return False

# ── CHAMBRE ──────────────────────────────────────────────────────────────────
def scrape_chambre():
    urls = [
        {"url":"https://www.chambredesrepresentants.ma/fr/legislation/projets-de-loi","statut":"Projet de loi"},
        {"url":"https://www.chambredesrepresentants.ma/fr/legislation/textes-votes-chambre-representants","statut":"Texte adopté"},
        {"url":"https://www.chambredesrepresentants.ma/fr/legislation/lois-transferts-bureau","statut":"Déposé au Bureau"},
        {"url":"https://www.chambredesrepresentants.ma/fr/legislation/textes-en-cours-detude-commission","statut":"En commission"},
    ]
    base = "https://www.chambredesrepresentants.ma"
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    titres_exclus = {"projet de loi organique","proposition de loi organique","projet de décret-loi","proposition de loi","projet de loi","recherche dans l'archive","textes finalisés"}
    for src in urls:
        try:
            resp = requests.get(src["url"], headers=HEADERS, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre_raw = a.get_text(strip=True); href = a.get('href','')
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
        except Exception as e: print(f"Erreur Chambre: {e}")
    limite = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='chambre' AND date_scrape < ?", (limite,))
    conn.commit(); conn.close(); return nouveaux

# ── SGG ───────────────────────────────────────────────────────────────────────
def scrape_sgg():
    base = "https://www.sgg.gov.ma"
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_cibles = ["projet de loi","avant-projet","avant projet","loi organique","dahir","décret","decret"]
    try:
        resp = requests.get(f"{base}/Legislation.aspx", headers=HEADERS, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href','')
            if len(titre) < 10: continue
            t = titre.lower()
            if not (any(m in t for m in mots_cibles) or '.pdf' in href.lower()): continue
            url_item = href if href.startswith('http') else base + "/" + href.lstrip("/")
            if "avant-projet" in t or "avant projet" in t: statut = "Avant-projet de loi"
            elif "projet de loi" in t: statut = "Projet de loi"
            elif "loi organique" in t: statut = "Loi organique"
            else: statut = "Texte législatif"
            if save_item(c, titre, url_item, statut, "sgg", "SGG", "legal", ALERTES_LEGAL): nouveaux += 1
    except Exception as e: print(f"Erreur SGG: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── BULLETIN OFFICIEL ─────────────────────────────────────────────────────────
def scrape_bo():
    base = "https://www.bulletinofficiel.ma"
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_paiement = ["paiement","monétique","monetique","bancaire","crédit","bank","financier","dahir","décret"]
    try:
        resp = requests.get(f"{base}/fr/derniers-bulletins", headers=HEADERS, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href','')
            if len(titre) < 10: continue
            if not any(m in titre.lower() for m in mots_paiement): continue
            url_item = href if href.startswith('http') else base + href
            if save_item(c, titre, url_item, "Bulletin Officiel", "bo", "Bulletin Officiel", "legal", ALERTES_LEGAL): nouveaux += 1
    except Exception as e: print(f"Erreur BO: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── BANK AL-MAGHRIB ───────────────────────────────────────────────────────────
def scrape_bam():
    base = "https://www.bkam.ma"
    url = f"{base}/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement"
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["circulaire","lettre circulaire","décision réglementaire","décision reglementaire","décision règlementaire","directive","instruction","note de service","dahir"]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        seen = set()
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href','')
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
    except Exception as e: print(f"Erreur BAM: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── CONSEIL DE LA CONCURRENCE ─────────────────────────────────────────────────
def scrape_concurrence():
    urls = [
        {"url":"https://conseil-concurrence.ma/category/communiques/","statut":"Communiqué"},
        {"url":"https://conseil-concurrence.ma/category/avis-decisions/","statut":"Avis / Décision"},
        {"url":"https://conseil-concurrence.ma/category/avis-decisions/pratiques-anticoncurrentielles/decisions-contentieuses/","statut":"Décision contentieuse"},
    ]
    mots_paiement = ["paiement","monétique","monetique","interchange","cmi","visa","mastercard","acquiring","acquéreur","établissement de paiement","switch","swam","hps","banque","bancaire","fintech","wallet","mobile"]
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    limite_date = datetime.now() - timedelta(weeks=8)

    for src in urls:
        try:
            resp = requests.get(src["url"], headers=HEADERS, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            # WordPress : articles h2/h3 avec liens
            articles = soup.find_all(['h2','h3'], class_=lambda x: x and ('entry-title' in x or 'post-title' in x))
            if not articles:
                articles = soup.find_all(['h2','h3'])

            for h in articles:
                a = h.find('a', href=True)
                if not a: continue
                titre = a.get_text(strip=True)
                href = a.get('href','')
                if len(titre) < 15: continue

                # Chercher la date proche de l'article
                date_pub = ""
                parent = h.parent
                if parent:
                    date_el = parent.find(class_=lambda x: x and 'date' in str(x).lower())
                    if date_el:
                        date_pub = date_el.get_text(strip=True)[:20]

                # Chercher lien PDF dans le parent
                url_final = href
                if parent:
                    pdf_link = parent.find('a', href=lambda x: x and '.pdf' in x.lower())
                    if pdf_link:
                        url_final = pdf_link.get('href','')
                        statut_item = src["statut"] + " (PDF)"
                    else:
                        statut_item = src["statut"]
                else:
                    statut_item = src["statut"]

                # Filtrer : garder tout sauf les très génériques si pas de mot paiement
                t = titre.lower()
                est_pertinent = any(m in t for m in mots_paiement)

                # Sauvegarder tous les items (filtrés par alerte dans detecter_alerte)
                if save_item(c, titre, url_final, statut_item, "concurrence", "Conseil de la Concurrence", "reglementaire", ALERTES_REGL, date_pub):
                    nouveaux += 1

        except Exception as e: print(f"Erreur Concurrence: {e}")

    # Purger > 8 semaines
    limite_str = (datetime.now() - timedelta(weeks=8)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='concurrence' AND date_scrape < ?", (limite_str,))
    conn.commit(); conn.close(); return nouveaux

# ── OFFICE DES CHANGES ────────────────────────────────────────────────────────
def scrape_office_changes():
    urls = [
        "https://www.oc.gov.ma/fr/reglementation/circulaires",
        "https://www.oc.gov.ma/fr/reglementation/instructions",
        "https://www.oc.gov.ma/fr/actualites",
    ]
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["circulaire","instruction","note","arrêté","décision","change","paiement","transfert","virement","devise"]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre = a.get_text(strip=True); href = a.get('href','')
                if len(titre) < 10: continue
                t = titre.lower(); h = href.lower()
                if not (any(m in t for m in mots_doc) or '.pdf' in h): continue
                url_item = href if href.startswith('http') else "https://www.oc.gov.ma" + href
                if "circulaire" in t: statut = "Circulaire"
                elif "instruction" in t: statut = "Instruction"
                elif "note" in t: statut = "Note"
                elif "arrêté" in t: statut = "Arrêté"
                else: statut = "Document OC"
                if save_item(c, titre, url_item, statut, "office_changes", "Office des Changes", "reglementaire", ALERTES_REGL): nouveaux += 1
        except Exception as e: print(f"Erreur OC: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── CNDP ──────────────────────────────────────────────────────────────────────
def scrape_cndp():
    urls = [
        {"url":"https://www.cndp.ma/deliberation/","statut":"Délibération"},
        {"url":"https://www.cndp.ma/actualites/","statut":"Actualité CNDP"},
    ]
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    limite_date = datetime.now() - timedelta(weeks=12)

    for src in urls:
        try:
            resp = requests.get(src["url"], headers=HEADERS, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre = a.get_text(strip=True); href = a.get('href','')
                if len(titre) < 15: continue
                t = titre.lower()
                # Exclure menus de navigation
                if any(x in t for x in ["accueil","contact","qui sommes","mention","politique","cookie"]): continue
                url_item = href if href.startswith('http') else "https://www.cndp.ma" + href
                # Détecter si c'est un PDF
                statut = src["statut"]
                if '.pdf' in href.lower(): statut += " (PDF)"
                if save_item(c, titre, url_item, statut, "cndp", "CNDP", "cyber", ALERTES_CYBER): nouveaux += 1
        except Exception as e: print(f"Erreur CNDP: {e}")

    conn.commit(); conn.close(); return nouveaux

# ── DGSSI ─────────────────────────────────────────────────────────────────────
def scrape_dgssi():
    base = "https://www.dgssi.gov.ma"
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["arrêté","arrete","circulaire","loi","décret","decret","dahir","instruction","directive","ordonnance"]
    try:
        resp = requests.get(f"{base}/fr/textes-legislatifs-et-reglementaires/", headers=HEADERS, timeout=15); resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True); href = a.get('href','')
            if len(titre) < 10 or not href: continue
            t = titre.lower()
            if not (any(m in t for m in mots_doc) or '.pdf' in href.lower()): continue
            url_item = href if href.startswith('http') else base + href
            if "arrêté" in t or "arrete" in t: statut = "Arrêté"
            elif "circulaire" in t: statut = "Circulaire"
            elif "dahir" in t or "loi" in t: statut = "Loi / Dahir"
            elif "décret" in t or "decret" in t: statut = "Décret"
            else: statut = "Document DGSSI"
            if save_item(c, titre, url_item, statut, "dgssi", "DGSSI", "cyber", ALERTES_CYBER): nouveaux += 1
    except Exception as e: print(f"Erreur DGSSI: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── ANRT ──────────────────────────────────────────────────────────────────────
def scrape_anrt():
    urls = [
        {"url":"https://www.anrt.ma/fr/decisions","statut":"Décision ANRT"},
        {"url":"https://www.anrt.ma/fr/actualites","statut":"Actualité ANRT"},
    ]
    nouveaux = 0; conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    mots_doc = ["décision","arrêté","circulaire","directive","licence","autorisation","interopérabilité","paiement","mobile","fintech"]
    for src in urls:
        try:
            resp = requests.get(src["url"], headers=HEADERS, timeout=15); resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre = a.get_text(strip=True); href = a.get('href','')
                if len(titre) < 10: continue
                t = titre.lower()
                if not (any(m in t for m in mots_doc) or '.pdf' in href.lower()): continue
                if any(x in t for x in ["accueil","contact","qui sommes","mentions"]): continue
                url_item = href if href.startswith('http') else "https://www.anrt.ma" + href
                statut = src["statut"]
                if '.pdf' in href.lower(): statut += " (PDF)"
                if save_item(c, titre, url_item, statut, "anrt", "ANRT", "cyber", ALERTES_CYBER): nouveaux += 1
        except Exception as e: print(f"Erreur ANRT: {e}")
    conn.commit(); conn.close(); return nouveaux

# ── HELPERS ───────────────────────────────────────────────────────────────────
def get_items(onglet, source_id=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
    if source_id:
        items = c.execute("SELECT * FROM items WHERE onglet=? AND source_id=? ORDER BY date_scrape DESC", (onglet, source_id)).fetchall()
    else:
        items = c.execute("SELECT * FROM items WHERE onglet=? ORDER BY date_scrape DESC", (onglet,)).fetchall()
    conn.close(); return items

def get_stats(lst):
    return {"total":len(lst),"eleve":sum(1 for x in lst if x["alerte_niveau"]=="eleve"),"moyen":sum(1 for x in lst if x["alerte_niveau"]=="moyen"),"faible":sum(1 for x in lst if x["alerte_niveau"]=="faible")}

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    chambre = get_items("legal","chambre"); sgg = get_items("legal","sgg"); bo = get_items("legal","bo")
    bam = get_items("reglementaire","bam_paiement")
    concurrence = get_items("reglementaire","concurrence")
    office_changes = get_items("reglementaire","office_changes")
    dgssi = get_items("cyber","dgssi"); cndp = get_items("cyber","cndp"); anrt = get_items("cyber","anrt")
    return render_template_string(HTML,
        chambre=chambre, sgg=sgg, bo=bo,
        bam=bam, concurrence=concurrence, office_changes=office_changes,
        dgssi=dgssi, cndp=cndp, anrt=anrt,
        stats_legal=get_stats(list(chambre)+list(sgg)+list(bo)),
        stats_regl=get_stats(list(bam)+list(concurrence)+list(office_changes)),
        stats_cyber=get_stats(list(dgssi)+list(cndp)+list(anrt)),
        alertes_legal=ALERTES_LEGAL, alertes_regl=ALERTES_REGL, alertes_cyber=ALERTES_CYBER,
        last_update=datetime.now().strftime("%d/%m/%Y %H:%M"))

@app.route("/api/scrape")
def api_scrape():
    try: n1=scrape_chambre()
    except: n1=0
    try: n2=scrape_sgg()
    except: n2=0
    try: n3=scrape_bo()
    except: n3=0
    try: n4=scrape_bam()
    except: n4=0
    try: n5=scrape_concurrence()
    except: n5=0
    try: n6=scrape_office_changes()
    except: n6=0
    try: n7=scrape_dgssi()
    except: n7=0
    try: n8=scrape_cndp()
    except: n8=0
    try: n9=scrape_anrt()
    except: n9=0
    total = n1+n2+n3+n4+n5+n6+n7+n8+n9
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    eleves = c.execute("SELECT COUNT(*) FROM items WHERE alerte_niveau='eleve'").fetchone()[0]
    conn.close()
    return jsonify({"status":"ok","nouveaux":total,
        "detail":{"chambre":n1,"sgg":n2,"bo":n3,"bam":n4,"concurrence":n5,"office_changes":n6,"dgssi":n7,"cndp":n8,"anrt":n9},
        "alertes_elevees":eleves})
@app.route("/api/demo")
def api_demo():
    conn=sqlite3.connect(DB_PATH); c=conn.cursor()
    demo=[
        ("Projet de loi N°103.22 relatif aux paiements électroniques et au réseau HPS Switch","https://www.chambredesrepresentants.ma/fr/loi10322","Projet de loi","chambre","Chambre des Représentants","legal"),
        ("Projet de loi N°61.25 modifiant la loi N°103.14 portant création de l'ANSS","https://www.chambredesrepresentants.ma/fr/loi6125","En commission","chambre","Chambre des Représentants","legal"),
        ("Projet de loi N°55.19 relatif à la protection des données personnelles","https://www.chambredesrepresentants.ma/fr/loi5519","Texte adopté","chambre","Chambre des Représentants","legal"),
        ("Avant-projet de loi sur les services de paiement électronique","https://www.sgg.gov.ma/avant-projet-paiement.pdf","Avant-projet de loi","sgg","SGG","legal"),
        ("Dahir n° 1-05-178 portant promulgation de la loi n° 103-12 relative aux établissements de crédit","https://www.bulletinofficiel.ma/fr/bo/6024","Bulletin Officiel","bo","Bulletin Officiel","legal"),
        ("Décision réglementaire N°392/W/2018 relative au paiement mobile domestique","https://www.bkam.ma/content/download/612250/Decision392.pdf","Décision réglementaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Lettre circulaire N° LC/BKAM/2018/70 relative au paiement mobile domestique","https://www.bkam.ma/content/download/612251/LC-BKAM-2018-70.pdf","Lettre circulaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Circulaire N° 14/G/06 relative à la mise en place du SRBM","https://www.bkam.ma/content/download/498845/CIRCULAIRE_SRBM.pdf","Circulaire BAM","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Décision règlementaire relative aux frais d'interchange monétique domestique","https://www.bkam.ma/content/download/834939/Decision-interchange.pdf","Décision réglementaire","bam_paiement","Bank Al-Maghrib","reglementaire"),
        ("Communiqué relatif à la décision N°152/D/2024 concernant le CMI et les établissements de paiement électronique","https://conseil-concurrence.ma/decision-152-d-2024-cmi/","Décision","concurrence","Conseil de la Concurrence","reglementaire"),
        ("Instruction générale des opérations de change (IGOC) 2024","https://www.oc.gov.ma/sites/default/files/reglementation/pdf/2024-01/IGOC%202024.pdf","Instruction","office_changes","Office des Changes","reglementaire"),
        ("Circulaire de l'Office des Changes relative aux paiements électroniques transfrontaliers","https://www.oc.gov.ma/fr/reglementation/circulaires","Circulaire","office_changes","Office des Changes","reglementaire"),
        ("Arrêté du Chef du Gouvernement relatif à la sécurité des systèmes d'information","https://www.dgssi.gov.ma/arrete-ssi.pdf","Arrêté","dgssi","DGSSI","cyber"),
        ("Loi n° 43-20 relative aux services de confiance pour les transactions électroniques","https://www.dgssi.gov.ma/loi-43-20.pdf","Loi / Dahir","dgssi","DGSSI","cyber"),
        ("Délibération N° D-939-2025 du 28/11/2025 relative au modèle de déclaration simplifiée cookies","https://www.cndp.ma/deliberation/","Délibération","cndp","CNDP","cyber"),
        ("Délibération N° D-940-2025 du 28/11/2025 relative à la gestion des newsletters et données personnelles","https://www.cndp.ma/deliberation/","Délibération","cndp","CNDP","cyber"),
        ("Décision ANRT relative à l'attribution des licences 5G aux opérateurs mobiles nationaux","https://www.anrt.ma/fr/decisions","Décision ANRT","anrt","ANRT","cyber"),
    ]
    inserted=0
    for d in demo:
        titre,url,statut,source_id,source_nom,onglet=d
        if onglet=="legal": alertes=ALERTES_LEGAL
        elif onglet=="reglementaire": alertes=ALERTES_REGL
        else: alertes=ALERTES_CYBER
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
.logo-svg{height:44px;flex-shrink:0;width:auto}
.logo-sep{width:1px;height:24px;background:var(--border2)}
.logo-text{font-size:.72rem;color:var(--text3);font-weight:500;letter-spacing:.05em;line-height:1.4}
.logo-text strong{display:block;color:var(--text);font-weight:700;font-size:.82rem;letter-spacing:.02em}
.topbar-meta{font-family:'JetBrains Mono',monospace;font-size:.65rem;color:var(--text3);background:var(--surface2);border:1px solid var(--border);padding:4px 10px;border-radius:6px}
.btn-refresh{display:flex;align-items:center;gap:6px;background:var(--grad);color:#fff;border:none;padding:8px 16px;border-radius:8px;font-family:'Outfit',sans-serif;font-size:.78rem;font-weight:600;cursor:pointer;transition:opacity .2s,transform .15s}
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
.page-hd{margin-bottom:1.5rem}.page-title{font-size:1.4rem;font-weight:800;letter-spacing:-.02em;color:var(--text)}
.page-title .acc{background:var(--grad);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.page-sub{font-size:.78rem;color:var(--text3);margin-top:.2rem}
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
.tag.pdf{background:#FFF3E0;color:#E65100;border-color:#FFCC80;font-size:.6rem}
.tag.kw{background:transparent;border-color:transparent;color:var(--text3);font-style:italic;font-weight:400;font-size:.62rem;letter-spacing:0;text-transform:none;padding-left:0}
.idate{font-family:'JetBrains Mono',monospace;font-size:.6rem;color:var(--text3);white-space:nowrap;padding-top:2px}
.empty{text-align:center;padding:2rem;background:var(--surface);border:1px dashed var(--border2);border-radius:9px;color:var(--text3);font-size:.8rem}
.empty strong{display:block;color:var(--text2);font-size:.88rem;margin-bottom:.3rem;font-weight:700}
.placeholder{background:var(--surface);border:1px dashed var(--border2);border-radius:9px;padding:1.5rem;text-align:center;color:var(--text3);font-size:.78rem}
.placeholder strong{display:block;color:var(--text2);font-size:.85rem;margin-bottom:.3rem;font-weight:700}
.extern{background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:.95rem 1.1rem;display:flex;align-items:center;gap:1rem;margin-bottom:.6rem}
.extern.c{border-left:3px solid var(--cyan)}.extern.v{border-left:3px solid var(--violet)}
.ebd{flex:1}.etitle{font-size:.85rem;font-weight:700;color:var(--text);margin-bottom:.2rem}.edesc{font-size:.74rem;color:var(--text2);line-height:1.5}
.ebtn{background:var(--surface2);color:var(--cyan);border:1px solid rgba(0,188,212,.3);padding:6px 13px;border-radius:6px;font-size:.7rem;font-weight:700;text-decoration:none;white-space:nowrap;transition:all .15s;font-family:'Outfit',sans-serif}
.ebtn:hover{background:var(--cyan-light);border-color:var(--cyan)}
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--surface);color:var(--text);padding:.8rem 1.2rem;border-radius:10px;border:1px solid var(--border);border-top:2px solid var(--cyan);font-size:.78rem;font-weight:500;box-shadow:0 8px 24px rgba(0,0,0,.12);transform:translateY(70px);opacity:0;transition:all .3s cubic-bezier(.175,.885,.32,1.275);z-index:999;max-width:280px}
.toast.show{transform:translateY(0);opacity:1}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:4px}
</style>
</head>
<body>
<div class="layout">

<header class="topbar">
  <div class="logo-block">
    <img class="logo-svg" src="https://raw.githubusercontent.com/Hamzaben1020/veille-reglementaire/main/logo.png" alt="SWAM Switch Al Maghrib">
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
  <a class="nav-btn" href="https://conseil-concurrence.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Conseil Concurrence<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.oc.gov.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>Office des Changes<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.cndp.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>CNDP<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.dgssi.gov.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>DGSSI<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
  <a class="nav-btn" href="https://www.anrt.ma" target="_blank"><svg class="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 6l5 5m0 0l5-5m-5 5V2M23 18l-5-5m0 0l-5 5m5-5v8"/></svg>ANRT<span style="margin-left:auto;font-size:.6rem;color:var(--text3)">↗</span></a>
</nav>

<main class="main">

{%- macro render_feed(items, alertes, feed_id) %}
  {% if items %}
  <div class="feed" id="{{ feed_id }}">
    {% for item in items %}
    <div class="item" data-alerte="{{ item.alerte_niveau or 'none' }}">
      <div class="ibar {{ item.alerte_niveau or 'none' }}"></div>
      <div class="item-body">
        <div class="ititre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
        <div class="itags">
          {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes[item.alerte_niveau].label }}</span>{% endif %}
          <span class="tag st">{{ item.statut }}</span>
          {% if '(PDF)' in item.statut %}<span class="tag pdf">⬇ PDF</span>{% endif %}
          {% if item.alerte_mots %}<span class="tag kw">{{ item.alerte_mots[:60] }}</span>{% endif %}
        </div>
      </div>
      <div class="idate">{{ item.date_pub or item.date_scrape[:10] }}</div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty"><strong>Aucun document</strong>Clique sur ↻ Actualiser</div>
  {% endif %}
{%- endmacro %}

  <!-- LÉGAL -->
  <div class="tab-panel actif" id="tab-legal">
    <div class="page-hd"><h1 class="page-title">Veille <span class="acc">Légale</span></h1><p class="page-sub">Chambre des Représentants · SGG · Bulletin Officiel</p></div>
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
      {{ render_feed(chambre, alertes_legal, "legal-chambre") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📋</span><span class="src-name">SGG — Secrétariat Général du Gouvernement</span><span class="src-cnt">{{ sgg|length }} doc.</span><a href="https://www.sgg.gov.ma/Legislation.aspx" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(sgg, alertes_legal, "legal-sgg") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📰</span><span class="src-name">Bulletin Officiel</span><span class="src-cnt">{{ bo|length }} doc.</span><a href="https://www.bulletinofficiel.ma" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(bo, alertes_legal, "legal-bo") }}
    </div>
  </div>

  <!-- RÉGLEMENTAIRE -->
  <div class="tab-panel" id="tab-reglementaire">
    <div class="page-hd"><h1 class="page-title">Veille <span class="acc">Réglementaire</span></h1><p class="page-sub">Bank Al-Maghrib · Conseil de la Concurrence · Office des Changes</p></div>
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
      {{ render_feed(bam, alertes_regl, "regl-bam") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">⚖️</span><span class="src-name">Conseil de la Concurrence</span><span class="src-cnt">{{ concurrence|length }} doc.</span><a href="https://conseil-concurrence.ma/category/communiques/" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(concurrence, alertes_regl, "regl-concurrence") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">💱</span><span class="src-name">Office des Changes</span><span class="src-cnt">{{ office_changes|length }} doc.</span><a href="https://www.oc.gov.ma/fr/reglementation" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(office_changes, alertes_regl, "regl-oc") }}
    </div>
  </div>

  <!-- CYBER & DONNÉES -->
  <div class="tab-panel" id="tab-cyber">
    <div class="page-hd"><h1 class="page-title">Cyber <span class="acc">&amp; Données</span></h1><p class="page-sub">DGSSI · CNDP · ANRT</p></div>
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
      {{ render_feed(dgssi, alertes_cyber, "cyber-dgssi") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🛡️</span><span class="src-name">CNDP — Protection des Données Personnelles</span><span class="src-cnt">{{ cndp|length }} doc.</span><a href="https://www.cndp.ma/deliberation/" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(cndp, alertes_cyber, "cyber-cndp") }}
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">📡</span><span class="src-name">ANRT — Agence Nationale de Réglementation des Télécommunications</span><span class="src-cnt">{{ anrt|length }} doc.</span><a href="https://www.anrt.ma/fr/decisions" target="_blank" class="src-link">↗ Accéder</a></div>
      {{ render_feed(anrt, alertes_cyber, "cyber-anrt") }}
    </div>
  </div>

  <!-- NORMATIVE -->
  <div class="tab-panel" id="tab-normative">
    <div class="page-hd"><h1 class="page-title">Normative <span class="acc">Internationale</span></h1><p class="page-sub">BIS-CPMI · ISO · PCI-DSS · SWIFT CSP</p></div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🌐</span><span class="src-name">BIS — CPMI</span></div>
      <div class="extern c"><div class="ebd"><div class="etitle">Committee on Payments and Market Infrastructures</div><div class="edesc">Standards internationaux sur les systèmes de paiement, interopérabilité, risques opérationnels et surveillance des IMFs.</div></div><a href="https://www.bis.org/cpmi/about/overview.htm" target="_blank" class="ebtn">↗ Accéder</a></div>
    </div>
    <div class="source-group">
      <div class="source-hd"><span class="src-ico">🔐</span><span class="src-name">PCI-DSS &amp; ISO 27001</span></div>
      <div class="extern v"><div class="ebd"><div class="etitle">PCI Security Standards Council</div><div class="edesc">PCI-DSS v4.0, ISO 27001, SWIFT CSP. Référentiels de sécurité applicables à l'infrastructure SWAM.</div></div><a href="https://www.pcisecuritystandards.org" target="_blank" class="ebtn">↗ Accéder</a></div>
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

scheduler = BackgroundScheduler()
scheduler.add_job(lambda: (scrape_chambre(), scrape_sgg(), scrape_bo(),
    scrape_bam(), scrape_concurrence(), scrape_office_changes(),
    scrape_dgssi(), scrape_cndp(), scrape_anrt()),
    'cron', hour=8, minute=0)
scheduler.start()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
