from flask import Flask, render_template_string, jsonify
import requests
from bs4 import BeautifulSoup
import sqlite3
import re
from datetime import datetime, timedelta

app = Flask(__name__)
DB_PATH = "veille.db"

ALERTES_LEGAL = {
    "eleve": {
        "label": "ÉLEVÉ", "emoji": "🔴",
        "mots": ["paiement électronique", "paiements électroniques", "HPS",
                 "HPS Switch", "SWAM", "Switch Al Maghrib", "réseau bancaire",
                 "réseau VISA", "MasterCard", "paiement electronique",
                 "paiements electroniques"]
    },
    "moyen": {
        "label": "MOYEN", "emoji": "🟠",
        "mots": ["fraude", "financement", "données privées", "données personnelles",
                 "donnees personnelles", "donnees privees"]
    },
    "faible": {
        "label": "FAIBLE", "emoji": "🟡",
        "mots": ["finance", "économie", "economie"]
    }
}

ALERTES_REGL = {
    "eleve": {
        "label": "ÉLEVÉ", "emoji": "🔴",
        "mots": ["paiement électronique", "paiements électroniques", "HPS",
                 "HPS Switch", "SWAM", "Switch Al Maghrib", "réseau bancaire",
                 "réseau VISA", "MasterCard", "paiement electronique",
                 "paiements electroniques", "système de paiement", "moyen de paiement",
                 "interchange", "monétique", "monetique", "mobile payment",
                 "virement", "SRBM", "SIMT"]
    },
    "moyen": {
        "label": "MOYEN", "emoji": "🟠",
        "mots": ["fraude", "financement", "données privées", "données personnelles",
                 "donnees personnelles", "établissement de crédit", "agrément",
                 "surveillance", "blanchiment"]
    },
    "faible": {
        "label": "FAIBLE", "emoji": "🟡",
        "mots": ["finance", "économie", "economie", "bancaire", "crédit"]
    }
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
    conn.commit()
    conn.close()


def save_item(c, titre, url, statut, source_id, source_nom, onglet, alertes, date_pub=""):
    niveau, mots = detecter_alerte(titre, alertes)
    try:
        c.execute("""INSERT OR IGNORE INTO items
            (titre,url,statut,source_id,source_nom,onglet,alerte_niveau,alerte_mots,date_pub,date_scrape)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (titre, url, statut, source_id, source_nom, onglet,
             niveau, ", ".join(mots) if mots else "", date_pub,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        return c.rowcount > 0
    except:
        return False


# ─── SCRAPER CHAMBRE ─────────────────────────────────────────────────────────
def scrape_chambre():
    """4 dernières semaines. Projets/textes de loi avec numéro uniquement."""
    urls = [
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/projets-de-loi",
         "statut": "Projet de loi"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/textes-votes-chambre-representants",
         "statut": "Texte adopté"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/lois-transferts-bureau",
         "statut": "Déposé au Bureau"},
        {"url": "https://www.chambredesrepresentants.ma/fr/legislation/textes-en-cours-detude-commission",
         "statut": "En commission"},
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base = "https://www.chambredesrepresentants.ma"
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Exclure les titres génériques de navigation (sans numéro de loi)
    titres_exclus = {
        "projet de loi organique", "proposition de loi organique",
        "projet de décret-loi", "proposition de loi", "projet de loi",
        "recherche dans l'archive", "textes finalisés"
    }

    for src in urls:
        try:
            resp = requests.get(src["url"], headers=headers, timeout=15)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all('a', href=True):
                titre_raw = a.get_text(strip=True)
                href = a.get('href', '')
                if not href or len(titre_raw) < 10:
                    continue
                # Nettoyer préfixe navigation
                titre = titre_raw
                if "En cours d" in titre:
                    idx = titre.find("Projet")
                    if idx < 0:
                        idx = titre.find("Proposition")
                    if idx > 0:
                        titre = titre[idx:]

                # Exclure catégories génériques exactes
                if titre.strip().lower() in titres_exclus:
                    continue
                # Doit contenir un numéro de loi (N°XX ou Nº)
                if not re.search(r'N[°º]\s*\d', titre):
                    continue

                url_item = href if href.startswith('http') else base + href
                date_pub = datetime.now().strftime("%d/%m/%Y")
                if save_item(c, titre, url_item, src["statut"],
                             "chambre", "Chambre des Représentants", "legal",
                             ALERTES_LEGAL, date_pub):
                    nouveaux += 1
        except Exception as e:
            print(f"  Erreur Chambre: {e}")

    # Purger ce qui date de plus de 4 semaines
    limite = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='chambre' AND date_scrape < ?", (limite,))
    conn.commit()
    conn.close()
    print(f"  Chambre : {nouveaux} nouveaux")
    return nouveaux


# ─── SCRAPER DGSSI ───────────────────────────────────────────────────────────
def scrape_dgssi():
    """Extraire arrêtés, circulaires, lois depuis DGSSI"""
    base = "https://www.dgssi.gov.ma"
    url = f"{base}/fr/textes-legislatifs-et-reglementaires/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    mots_doc = ["arrêté", "arrete", "circulaire", "loi", "décret", "decret",
                "dahir", "instruction", "directive", "ordonnance"]

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True)
            href = a.get('href', '')
            if len(titre) < 10 or not href:
                continue

            t = titre.lower()
            h = href.lower()
            est_doc = any(m in t for m in mots_doc) or '.pdf' in h
            if not est_doc:
                continue

            url_item = href if href.startswith('http') else base + href

            if "arrêté" in t or "arrete" in t:
                statut = "Arrêté"
            elif "circulaire" in t:
                statut = "Circulaire"
            elif "dahir" in t or "loi" in t:
                statut = "Loi / Dahir"
            elif "décret" in t or "decret" in t:
                statut = "Décret"
            else:
                statut = "Document réglementaire"

            if save_item(c, titre, url_item, statut,
                         "dgssi", "DGSSI", "legal", ALERTES_LEGAL):
                nouveaux += 1

    except Exception as e:
        print(f"  Erreur DGSSI: {e}")

    conn.commit()
    conn.close()
    print(f"  DGSSI : {nouveaux} nouveaux")
    return nouveaux


# ─── SCRAPER SGG ─────────────────────────────────────────────────────────────
def scrape_sgg():
    """Projets et avant-projets de lois depuis SGG"""
    base = "https://www.sgg.gov.ma"
    url = f"{base}/Legislation.aspx"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    mots_cibles = ["projet de loi", "avant-projet", "avant projet",
                   "loi organique", "dahir", "décret", "decret"]

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True)
            href = a.get('href', '')
            if len(titre) < 10:
                continue

            t = titre.lower()
            est_pertinent = any(m in t for m in mots_cibles) or '.pdf' in href.lower()
            if not est_pertinent:
                continue

            url_item = href if href.startswith('http') else base + "/" + href.lstrip("/")

            if "avant-projet" in t or "avant projet" in t:
                statut = "Avant-projet de loi"
            elif "projet de loi" in t:
                statut = "Projet de loi"
            elif "loi organique" in t:
                statut = "Loi organique"
            else:
                statut = "Texte législatif"

            if save_item(c, titre, url_item, statut,
                         "sgg", "SGG", "legal", ALERTES_LEGAL):
                nouveaux += 1

    except Exception as e:
        print(f"  Erreur SGG: {e}")

    conn.commit()
    conn.close()
    print(f"  SGG : {nouveaux} nouveaux")
    return nouveaux


# ─── SCRAPER BAM ─────────────────────────────────────────────────────────────
def scrape_bam():
    """
    Extraire UNIQUEMENT les circulaires, lettres circulaires et décisions
    réglementaires BAM = liens /content/download/ (PDFs directs)
    + liens dont le titre contient les mots clés réglementaires
    """
    base = "https://www.bkam.ma"
    url = f"{base}/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    mots_doc = ["circulaire", "lettre circulaire", "décision réglementaire",
                "décision reglementaire", "décision règlementaire",
                "directive", "instruction", "note de service", "dahir"]

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        seen = set()
        for a in soup.find_all('a', href=True):
            titre = a.get_text(strip=True)
            href = a.get('href', '')
            if len(titre) < 10 or titre in seen:
                continue

            t = titre.lower()
            h = href.lower()

            # Cibler UNIQUEMENT : PDFs directs OU titres avec mots réglementaires
            est_pdf_direct = '/content/download/' in h
            est_doc_titre = any(m in t for m in mots_doc)

            if not est_pdf_direct and not est_doc_titre:
                continue

            seen.add(titre)
            url_item = href if href.startswith('http') else base + href

            # Typer le document
            if "circulaire du wali" in t:
                statut = "Circulaire du Wali"
            elif "lettre circulaire" in t:
                statut = "Lettre circulaire"
            elif "circulaire" in t:
                statut = "Circulaire BAM"
            elif "décision réglementaire" in t or "décision règlementaire" in t or "décision reglementaire" in t:
                statut = "Décision réglementaire"
            elif "directive" in t:
                statut = "Directive BAM"
            elif "instruction" in t:
                statut = "Instruction BAM"
            else:
                statut = "Document BAM"

            if save_item(c, titre, url_item, statut,
                         "bam_paiement", "BAM — Systèmes de paiement", "reglementaire", ALERTES_REGL):
                nouveaux += 1

    except Exception as e:
        print(f"  Erreur BAM: {e}")

    conn.commit()
    conn.close()
    print(f"  BAM : {nouveaux} nouveaux")
    return nouveaux


# ─── SCRAPER BIS CPMI ────────────────────────────────────────────────────────
def scrape_bis():
    """Section 'What's new' du CPMI BIS — 4 dernières semaines"""
    url = "https://www.bis.org/cpmi/about/overview.htm"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    limite_date = datetime.now() - timedelta(weeks=4)

    MOIS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
            "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

    def parse_bis_date(txt):
        """Convertir '26 Feb 2026' en datetime"""
        try:
            parts = txt.strip().split()
            if len(parts) == 3:
                day, mon, year = int(parts[0]), MOIS.get(parts[1].lower()[:3], 0), int(parts[2])
                if mon:
                    return datetime(year, mon, day)
        except:
            pass
        return None

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # La section What's new est dans un tableau — chaque ligne = date + titre
        for row in soup.select("table tr, .list_item, [class*='item']"):
            cells = row.find_all(["td", "div", "li"])
            date_txt = ""
            titre = ""
            href = ""

            # Chercher date et lien dans la ligne
            for cell in cells:
                txt = cell.get_text(strip=True)
                a = cell.find("a", href=True)
                if a:
                    titre = a.get_text(strip=True)
                    href = a.get("href", "")
                elif re.match(r'\d{1,2}\s+\w{3}\s+\d{4}', txt):
                    date_txt = txt

            # Fallback : chercher date dans tout le texte de la ligne
            if not date_txt:
                m = re.search(r'(\d{1,2}\s+\w{3,9}\s+\d{4})', row.get_text())
                if m:
                    date_txt = m.group(1)

            if not titre or not href:
                continue

            date_pub_dt = parse_bis_date(date_txt) if date_txt else None

            # Filtre 4 semaines
            if date_pub_dt and date_pub_dt < limite_date:
                continue

            url_item = href if href.startswith("http") else "https://www.bis.org" + href
            date_pub_str = date_pub_dt.strftime("%d/%m/%Y") if date_pub_dt else ""

            # Type de publication (sous-titre dans la cellule)
            type_pub = ""
            for cell in cells:
                txt = cell.get_text(strip=True)
                if any(x in txt for x in ["Papers", "Briefs", "Report", "Working"]) and txt != titre:
                    type_pub = txt[:60]
                    break

            titre_complet = titre
            if save_item(c, titre_complet, url_item, type_pub or "Publication BIS",
                         "bis_cpmi", "BIS — CPMI", "normative", ALERTES_REGL, date_pub_str):
                nouveaux += 1

    except Exception as e:
        print(f"  Erreur BIS: {e}")

    # Purger > 4 semaines
    limite_str = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='bis_cpmi' AND date_scrape < ?", (limite_str,))
    conn.commit()
    conn.close()
    print(f"  BIS : {nouveaux} nouveaux")
    return nouveaux


# ─── ROUTES ──────────────────────────────────────────────────────────────────

def get_items(onglet, source_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if source_id:
        items = c.execute(
            "SELECT * FROM items WHERE onglet=? AND source_id=? ORDER BY date_scrape DESC",
            (onglet, source_id)).fetchall()
    else:
        items = c.execute(
            "SELECT * FROM items WHERE onglet=? ORDER BY date_scrape DESC",
            (onglet,)).fetchall()
    conn.close()
    return items


@app.route("/")
def dashboard():
    chambre = get_items("legal", "chambre")
    dgssi   = get_items("legal", "dgssi")
    bam     = get_items("reglementaire", "bam_paiement")

    all_legal = list(chambre) + list(dgssi)
    all_regl  = list(bam)

    stats_legal = {
        "total": len(all_legal),
        "eleve": sum(1 for x in all_legal if x["alerte_niveau"] == "eleve"),
        "moyen": sum(1 for x in all_legal if x["alerte_niveau"] == "moyen"),
        "faible": sum(1 for x in all_legal if x["alerte_niveau"] == "faible"),
    }
    stats_regl = {
        "total": len(all_regl),
        "eleve": sum(1 for x in all_regl if x["alerte_niveau"] == "eleve"),
        "moyen": sum(1 for x in all_regl if x["alerte_niveau"] == "moyen"),
        "faible": sum(1 for x in all_regl if x["alerte_niveau"] == "faible"),
    }

    return render_template_string(HTML,
        chambre=chambre, dgssi=dgssi, bam=bam,
        stats_legal=stats_legal, stats_regl=stats_regl,
        alertes_legal=ALERTES_LEGAL, alertes_regl=ALERTES_REGL,
        last_update=datetime.now().strftime("%d/%m/%Y %H:%M"))


@app.route("/api/scrape")
def api_scrape():
    n1 = scrape_chambre()
    n2 = scrape_dgssi()
    n3 = scrape_bam()
    total = n1 + n2 + n3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    eleves = c.execute("SELECT COUNT(*) FROM items WHERE alerte_niveau='eleve'").fetchone()[0]
    conn.close()
    return jsonify({"status": "ok", "nouveaux": total,
                    "detail": {"chambre": n1, "dgssi": n2, "bam": n3},
                    "alertes_elevees": eleves})


@app.route("/api/demo")
def api_demo():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    demo = [
        ("Projet de loi N°61.25 modifiant la loi N°103.14 portant création de l'Agence nationale de la sécurité des systèmes d'information",
         "https://www.chambredesrepresentants.ma/fr/loi6125", "En commission",
         "chambre", "Chambre des Représentants", "legal"),
        ("Projet de loi N°103.22 relatif aux paiements électroniques et au réseau HPS Switch",
         "https://www.chambredesrepresentants.ma/fr/loi10322", "Projet de loi",
         "chambre", "Chambre des Représentants", "legal"),
        ("Projet de loi N°55.19 relatif à la protection des données personnelles",
         "https://www.chambredesrepresentants.ma/fr/loi5519", "Texte adopté",
         "chambre", "Chambre des Représentants", "legal"),
        ("Arrêté du Chef du Gouvernement relatif à la sécurité des systèmes d'information",
         "https://www.dgssi.gov.ma/arrete-ssi.pdf", "Arrêté",
         "dgssi", "DGSSI", "legal"),
        ("Loi n° 43-20 relative aux services de confiance pour les transactions électroniques",
         "https://www.dgssi.gov.ma/loi-43-20.pdf", "Loi / Dahir",
         "dgssi", "DGSSI", "legal"),
        ("Avant-projet de loi sur l'économie numérique et le financement participatif",
         "https://www.sgg.gov.ma/avant-projet-numerique.pdf", "Avant-projet de loi",
         "sgg", "SGG", "legal"),
        ("Décision réglementaire N°392/W/2018 relative au paiement mobile domestique",
         "https://www.bkam.ma/content/download/612250/6778237/version/1/file/Decision392.pdf",
         "Décision réglementaire", "bam_paiement", "BAM — Systèmes de paiement", "reglementaire"),
        ("Lettre circulaire N° LC/BKAM/2018/70 relative au paiement mobile domestique",
         "https://www.bkam.ma/content/download/612251/6778239/version/1/file/LC-BKAM-2018-70.pdf",
         "Lettre circulaire", "bam_paiement", "BAM — Systèmes de paiement", "reglementaire"),
        ("Décision règlementaire relative aux frais d'interchange monétique domestique",
         "https://www.bkam.ma/content/download/834939/9078080/version/1/file/Decision-interchange.pdf",
         "Décision réglementaire", "bam_paiement", "BAM — Systèmes de paiement", "reglementaire"),
        ("Circulaire N° 14/G/06 relative à la mise en place du Système des Règlements Bruts du Maroc (SRBM)",
         "https://www.bkam.ma/content/download/498845/4962775/CIRCULAIRE_SRBM_14-G-06.pdf",
         "Circulaire BAM", "bam_paiement", "BAM — Systèmes de paiement", "reglementaire"),
        ("Circulaire BAM relative aux établissements de paiement électronique et agrément HPS Switch",
         "https://www.bkam.ma/content/download/000001/circ-paiement.pdf",
         "Circulaire BAM", "bam_paiement", "BAM — Systèmes de paiement", "reglementaire"),
    ]
    inserted = 0
    for d in demo:
        titre, url, statut, source_id, source_nom, onglet = d
        alertes = ALERTES_LEGAL if onglet == "legal" else ALERTES_REGL
        if save_item(c, titre, url, statut, source_id, source_nom, onglet, alertes):
            inserted += 1
    conn.commit()
    conn.close()
    return jsonify({"status": "ok", "inseres": inserted})


# ─── TEMPLATE ────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Veille Légale & Réglementaire — SWAM</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=Epilogue:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f5f0e8; --card:#fffdf8; --border:#e0d8c8;
  --text:#2c2416; --muted:#9a8e7a;
  --eleve:#c0392b; --moyen:#d35400; --faible:#c9a84c;
  --vert:#27ae60; --bleu:#2980b9; --accent:#8b6914;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Epilogue',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;}
header{display:flex;align-items:center;justify-content:space-between;padding:0 2rem;height:64px;
  border-bottom:1px solid var(--border);background:rgba(13,15,20,0.97);
  position:sticky;top:0;z-index:100;}
.logo{font-family:'Syne',sans-serif;font-size:1.1rem;color:#fff;}
.logo em{color:var(--accent);font-style:normal;}
.logo small{display:block;font-size:0.65rem;color:var(--muted);letter-spacing:2px;text-transform:uppercase;font-weight:300;}
.header-right{display:flex;align-items:center;gap:1rem;}
.update-time{font-size:0.72rem;color:var(--muted);}
.btn-scrape{background:var(--accent);color:white;border:none;padding:0.45rem 1.1rem;
  border-radius:6px;font-size:0.8rem;font-weight:600;cursor:pointer;
  font-family:'Epilogue',sans-serif;transition:all 0.2s;}
.btn-scrape:hover{opacity:0.85;}
.btn-scrape:disabled{opacity:0.4;cursor:wait;}
.tabs{background:var(--card);border-bottom:1px solid var(--border);padding:0 2rem;display:flex;}
.tab{padding:0.9rem 1.6rem;font-family:'Syne',sans-serif;font-size:0.78rem;font-weight:700;
  color:var(--muted);cursor:pointer;border:none;background:none;
  border-bottom:3px solid transparent;margin-bottom:-1px;transition:all 0.2s;
  text-transform:uppercase;letter-spacing:0.5px;}
.tab:hover{color:var(--text);}
.tab.actif{color:var(--accent);border-bottom-color:var(--accent);}
.tab-content{display:none;}
.tab-content.actif{display:block;}
.tab-inner{padding:1.5rem 2rem;max-width:1200px;margin:0 auto;}
.filters-bar{display:flex;gap:0.75rem;align-items:center;background:var(--card);
  border:1px solid var(--border);padding:0.75rem 1rem;border-radius:8px;
  margin-bottom:1.4rem;flex-wrap:wrap;}
.filters-label{font-size:0.68rem;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:1px;margin-right:0.2rem;}
.pill{display:flex;align-items:center;gap:0.5rem;padding:0.5rem 1rem;border-radius:8px;
  font-size:0.78rem;font-weight:600;cursor:pointer;border:1px solid;transition:all 0.2s;
  background:transparent;font-family:'Epilogue',sans-serif;white-space:nowrap;}
.pill.tous{color:var(--bleu);border-color:var(--bleu);}
.pill.eleve{color:var(--eleve);border-color:var(--eleve);}
.pill.moyen{color:var(--moyen);border-color:var(--moyen);}
.pill.faible{color:var(--faible);border-color:var(--faible);}
.pill.actif,.pill:hover{color:#fff!important;}
.pill.tous.actif,.pill.tous:hover{background:var(--bleu);}
.pill.eleve.actif,.pill.eleve:hover{background:var(--eleve);}
.pill.moyen.actif,.pill.moyen:hover{background:var(--moyen);}
.pill.faible.actif,.pill.faible:hover{background:var(--faible);color:#000!important;}
.pill-num{font-size:1.05rem;font-family:'Syne',sans-serif;font-weight:700;}
.source-groupe{margin-bottom:2rem;}
.source-header{display:flex;align-items:center;gap:0.6rem;margin-bottom:0.75rem;
  padding-bottom:0.5rem;border-bottom:1px solid var(--border);}
.source-header h3{font-family:'Syne',sans-serif;font-size:0.9rem;font-weight:700;}
.source-link{font-size:0.65rem;color:var(--accent);text-decoration:none;font-weight:600;margin-left:auto;}
.source-link:hover{text-decoration:underline;}
.source-count{font-size:0.67rem;color:var(--muted);}
.items-list{display:flex;flex-direction:column;gap:0.6rem;}
.item-card{background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:1rem 1.1rem;display:flex;align-items:flex-start;gap:0.9rem;
  transition:border-color 0.2s;}
.item-card:hover{border-color:var(--accent);}
.bar{width:4px;border-radius:4px;flex-shrink:0;align-self:stretch;min-height:36px;}
.bar.eleve{background:var(--eleve);box-shadow:0 0 8px rgba(192,57,43,0.4);}
.bar.moyen{background:var(--moyen);box-shadow:0 0 8px rgba(211,84,0,0.3);}
.bar.faible{background:var(--faible);}
.bar.none{background:var(--border);}
.item-body{flex:1;min-width:0;}
.item-titre{font-size:0.87rem;font-weight:500;line-height:1.45;}
.item-titre a{color:var(--text);text-decoration:none;}
.item-titre a:hover{color:var(--accent);}
.item-meta{display:flex;gap:0.4rem;margin-top:0.4rem;flex-wrap:wrap;align-items:center;}
.tag{font-size:0.67rem;padding:2px 7px;border-radius:4px;font-weight:600;}
.tag.eleve{background:rgba(192,57,43,.15);color:var(--eleve);}
.tag.moyen{background:rgba(211,84,0,.15);color:var(--moyen);}
.tag.faible{background:rgba(201,168,76,.2);color:#7a5c00;}
.tag.statut{background:rgba(41,128,185,.1);color:var(--bleu);}
.tag.mots{background:#ede8dc;color:var(--muted);font-weight:400;font-style:italic;font-size:0.63rem;}
.extern-card{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--accent);
  border-radius:10px;padding:1rem 1.2rem;display:flex;align-items:center;gap:1rem;}
.extern-body{flex:1;}
.extern-title{font-family:'Syne',sans-serif;font-size:0.92rem;font-weight:700;margin-bottom:0.3rem;}
.extern-desc{font-size:0.77rem;color:var(--muted);line-height:1.5;}
.extern-btn{background:var(--accent);color:#fff;text-decoration:none;padding:0.48rem 1rem;
  border-radius:6px;font-size:0.73rem;font-weight:700;text-transform:uppercase;
  white-space:nowrap;transition:opacity 0.2s;}
.extern-btn:hover{opacity:0.85;}
.normatif-placeholder{background:var(--card);border:1px dashed var(--border);border-radius:10px;
  padding:1.5rem;text-align:center;color:var(--muted);font-size:0.82rem;}
.normatif-placeholder strong{display:block;color:var(--text);font-size:0.9rem;
  margin-bottom:0.4rem;font-family:'Syne',sans-serif;}
.normatif-header{display:flex;align-items:center;gap:0.6rem;margin-bottom:0.7rem;
  padding-bottom:0.5rem;border-bottom:1px solid var(--border);}
.normatif-header h3{font-family:'Syne',sans-serif;font-size:0.9rem;font-weight:700;}
.empty{text-align:center;padding:2rem;color:var(--muted);font-size:0.82rem;
  background:var(--card);border:1px dashed var(--border);border-radius:10px;}
.toast{position:fixed;bottom:1.5rem;right:1.5rem;background:var(--card);color:var(--text);
  padding:0.9rem 1.3rem;border-radius:10px;border:1px solid var(--border);
  border-left:4px solid var(--accent);font-size:0.82rem;
  box-shadow:0 8px 30px rgba(0,0,0,0.15);transform:translateY(80px);opacity:0;
  transition:all 0.3s;z-index:999;max-width:320px;}
.toast.show{transform:translateY(0);opacity:1;}
</style>
</head>
<body>

<header>
  <div class="logo">Veille <em>Légale & Réglementaire</em>
    <small>SWAM · Switch Al Maghrib · Paiements Électroniques</small>
  </div>
  <div class="header-right">
    <span class="update-time">MAJ : {{ last_update }}</span>
    <button class="btn-scrape" onclick="lancerScrape(this)">↻ Actualiser</button>
  </div>
</header>

<div class="tabs">
  <button class="tab actif" onclick="changerOnglet('legal',this)">⚖️ Veille Légale</button>
  <button class="tab" onclick="changerOnglet('reglementaire',this)">🏦 Veille Réglementaire</button>
  <button class="tab" onclick="changerOnglet('normative',this)">🌐 Veille Normative</button>
</div>


<!-- ══════════════════ LÉGAL ══════════════════ -->
<div class="tab-content actif" id="tab-legal">
<div class="tab-inner">

  <div class="filters-bar">
    <span class="filters-label">Filtre :</span>
    <button class="pill tous actif" onclick="filtrer('legal','tous',this)">
      <span class="pill-num">{{ stats_legal.total }}</span>&nbsp;Tous
    </button>
    <button class="pill eleve" onclick="filtrer('legal','eleve',this)">
      <span class="pill-num">{{ stats_legal.eleve }}</span>&nbsp;🔴 Élevé
    </button>
    <button class="pill moyen" onclick="filtrer('legal','moyen',this)">
      <span class="pill-num">{{ stats_legal.moyen }}</span>&nbsp;🟠 Moyen
    </button>
    <button class="pill faible" onclick="filtrer('legal','faible',this)">
      <span class="pill-num">{{ stats_legal.faible }}</span>&nbsp;🟡 Faible
    </button>
  </div>

  <!-- Chambre -->
  <div class="source-groupe">
    <div class="source-header">
      <h3>⚖️ Chambre des Représentants</h3>
      <span class="source-count">{{ chambre|length }} document(s)</span>
      <a href="https://www.chambredesrepresentants.ma/fr/action-legislative" target="_blank" class="source-link">↗ Ouvrir le site</a>
    </div>
    {% if chambre %}
    <div class="items-list" id="legal-chambre">
      {% for item in chambre %}
      <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
        <div class="bar {{ item.alerte_niveau or 'none' }}"></div>
        <div class="item-body">
          <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
          <div class="item-meta">
            {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].emoji }} {{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}
            <span class="tag statut">{{ item.statut }}</span>
            {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:70] }}</span>{% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}<div class="empty">Aucun projet de loi — clique sur ↻ Actualiser</div>{% endif %}
  </div>

  <!-- DGSSI -->
  <div class="source-groupe">
    <div class="source-header">
      <h3>🔒 DGSSI — Arrêtés, Circulaires & Lois</h3>
      <span class="source-count">{{ dgssi|length }} document(s)</span>
      <a href="https://www.dgssi.gov.ma/fr/textes-legislatifs-et-reglementaires/" target="_blank" class="source-link">↗ Ouvrir le site</a>
    </div>
    {% if dgssi %}
    <div class="items-list" id="legal-dgssi">
      {% for item in dgssi %}
      <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
        <div class="bar {{ item.alerte_niveau or 'none' }}"></div>
        <div class="item-body">
          <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
          <div class="item-meta">
            {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].emoji }} {{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}
            <span class="tag statut">{{ item.statut }}</span>
            {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:70] }}</span>{% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}<div class="empty">Aucun document — clique sur ↻ Actualiser</div>{% endif %}
  </div>


</div>
</div>


<!-- ══════════════════ RÉGLEMENTAIRE ══════════════════ -->
<div class="tab-content" id="tab-reglementaire">
<div class="tab-inner">

  <div class="filters-bar">
    <span class="filters-label">Filtre :</span>
    <button class="pill tous actif" onclick="filtrer('reglementaire','tous',this)">
      <span class="pill-num">{{ stats_regl.total }}</span>&nbsp;Tous
    </button>
    <button class="pill eleve" onclick="filtrer('reglementaire','eleve',this)">
      <span class="pill-num">{{ stats_regl.eleve }}</span>&nbsp;🔴 Élevé
    </button>
    <button class="pill moyen" onclick="filtrer('reglementaire','moyen',this)">
      <span class="pill-num">{{ stats_regl.moyen }}</span>&nbsp;🟠 Moyen
    </button>
    <button class="pill faible" onclick="filtrer('reglementaire','faible',this)">
      <span class="pill-num">{{ stats_regl.faible }}</span>&nbsp;🟡 Faible
    </button>
  </div>

  <!-- BAM -->
  <div class="source-groupe">
    <div class="source-header">
      <h3>🏦 BAM — Circulaires, Lettres & Décisions réglementaires</h3>
      <span class="source-count">{{ bam|length }} document(s)</span>
      <a href="https://www.bkam.ma/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement" target="_blank" class="source-link">↗ Ouvrir le site</a>
    </div>
    {% if bam %}
    <div class="items-list" id="regl-bam">
      {% for item in bam %}
      <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
        <div class="bar {{ item.alerte_niveau or 'none' }}"></div>
        <div class="item-body">
          <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
          <div class="item-meta">
            {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_regl[item.alerte_niveau].emoji }} {{ alertes_regl[item.alerte_niveau].label }}</span>{% endif %}
            <span class="tag statut">{{ item.statut }}</span>
            {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:70] }}</span>{% endif %}
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}<div class="empty">Aucune circulaire / décision — clique sur ↻ Actualiser</div>{% endif %}
  </div>

  <!-- Conseil de la Concurrence -->
  <div class="source-groupe">
    <div class="source-header">
      <h3>⚖️ Conseil de la Concurrence</h3>
    </div>
    <div class="extern-card">
      <div class="extern-body">
        <div class="extern-title">Conseil de la Concurrence — Maroc</div>
        <div class="extern-desc">Anticiper les nouvelles règles du marché, nouveaux entrants et décisions impactant le secteur des paiements électroniques.</div>
      </div>
      <a href="https://conseil-concurrence.ma/" target="_blank" class="extern-btn">↗ Accéder au site</a>
    </div>
  </div>

</div>
</div>


<!-- ══════════════════ NORMATIVE ══════════════════ -->
<div class="tab-content" id="tab-normative">
<div class="tab-inner">
  <div style="margin-bottom:1.8rem">
    <div class="normatif-header">
      <h3>🌐 BIS — CPMI</h3>
    </div>
    <div class="extern-card">
      <div class="extern-body">
        <div class="extern-title">BIS — Committee on Payments and Market Infrastructures</div>
        <div class="extern-desc">Publications, rapports et standards internationaux sur les systèmes de paiement et l'infrastructure des marchés financiers.</div>
      </div>
      <a href="https://www.bis.org/cpmi/about/overview.htm" target="_blank" class="extern-btn">↗ Accéder au site</a>
    </div>
  </div>
  <div>
    <div class="normatif-header"><h3>🔐 Cybersécurité — Normes internationales</h3></div>
    <div class="normatif-placeholder">
      <strong>Contenu à venir</strong>
      ISO 27001, PCI-DSS, SWIFT CSP et autres référentiels cyber applicables à SWAM.
    </div>
  </div>
</div>
</div>

<div class="toast" id="toast"></div>
<script>
function changerOnglet(id,btn){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('actif'));
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('actif'));
  btn.classList.add('actif');
  document.getElementById('tab-'+id).classList.add('actif');
}
function filtrer(onglet,type,btn){
  btn.closest('.tab-content').querySelectorAll('.pill').forEach(b=>b.classList.remove('actif'));
  btn.classList.add('actif');
  document.getElementById('tab-'+onglet).querySelectorAll('.item-card').forEach(card=>{
    card.style.display=(type==='tous'||card.dataset.alerte===type)?'':'none';
  });
}
async function lancerScrape(btn){
  btn.disabled=true;btn.textContent='⏳ ...';
  showToast('Scraping en cours...');
  try{
    const d=await fetch('/api/scrape').then(r=>r.json());
    showToast('✅ '+d.nouveaux+' nouveaux'+(d.alertes_elevees>0?' · 🔴 '+d.alertes_elevees+' alertes !':''));
    setTimeout(()=>location.reload(),2800);
  }catch(e){showToast('❌ Erreur scraping');}
  finally{btn.disabled=false;btn.textContent='↻ Actualiser';}
}
function showToast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),5000);
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    init_db()
    print("\n✅  Base prête : veille.db")
    print("🚀  http://localhost:5000")
    print("📦  Données test : http://localhost:5000/api/demo\n")
    app.run(debug=True, port=5000)
