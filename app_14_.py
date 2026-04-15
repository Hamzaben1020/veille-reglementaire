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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    titres_exclus = {"projet de loi organique", "proposition de loi organique", "projet de décret-loi", "proposition de loi", "projet de loi", "recherche dans l'archive", "textes finalisés"}
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
                titre = titre_raw
                if "En cours d" in titre:
                    idx = titre.find("Projet")
                    if idx < 0:
                        idx = titre.find("Proposition")
                    if idx > 0:
                        titre = titre[idx:]
                if titre.strip().lower() in titres_exclus:
                    continue
                if not re.search(r'N[°º]\s*\d', titre):
                    continue
                url_item = href if href.startswith('http') else base + href
                date_pub = datetime.now().strftime("%d/%m/%Y")
                if save_item(c, titre, url_item, src["statut"], "chambre", "Chambre des Représentants", "legal", ALERTES_LEGAL, date_pub):
                    nouveaux += 1
        except Exception as e:
            print(f"  Erreur Chambre: {e}")
    limite = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
    c.execute("DELETE FROM items WHERE source_id='chambre' AND date_scrape < ?", (limite,))
    conn.commit()
    conn.close()
    return nouveaux


def scrape_dgssi():
    base = "https://www.dgssi.gov.ma"
    url = f"{base}/fr/textes-legislatifs-et-reglementaires/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    mots_doc = ["arrêté", "arrete", "circulaire", "loi", "décret", "decret", "dahir", "instruction", "directive", "ordonnance"]
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
            if save_item(c, titre, url_item, statut, "dgssi", "DGSSI", "legal", ALERTES_LEGAL):
                nouveaux += 1
    except Exception as e:
        print(f"  Erreur DGSSI: {e}")
    conn.commit()
    conn.close()
    return nouveaux


def scrape_bam():
    base = "https://www.bkam.ma"
    url = f"{base}/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    nouveaux = 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    mots_doc = ["circulaire", "lettre circulaire", "décision réglementaire", "décision reglementaire", "décision règlementaire", "directive", "instruction", "note de service", "dahir"]
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
            est_pdf_direct = '/content/download/' in h
            est_doc_titre = any(m in t for m in mots_doc)
            if not est_pdf_direct and not est_doc_titre:
                continue
            seen.add(titre)
            url_item = href if href.startswith('http') else base + href
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
            if save_item(c, titre, url_item, statut, "bam_paiement", "BAM — Systèmes de paiement", "reglementaire", ALERTES_REGL):
                nouveaux += 1
    except Exception as e:
        print(f"  Erreur BAM: {e}")
    conn.commit()
    conn.close()
    return nouveaux


def get_items(onglet, source_id=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if source_id:
        items = c.execute("SELECT * FROM items WHERE onglet=? AND source_id=? ORDER BY date_scrape DESC", (onglet, source_id)).fetchall()
    else:
        items = c.execute("SELECT * FROM items WHERE onglet=? ORDER BY date_scrape DESC", (onglet,)).fetchall()
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


HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SWAM — Radar Réglementaire</title>
<link href="https://fonts.googleapis.com/css2?family=Urbanist:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --cyan: #00C8D4;
  --violet: #7B2D8B;
  --cyan-dim: rgba(0,200,212,0.15);
  --violet-dim: rgba(123,45,139,0.15);
  --bg: #080B10;
  --surface: #0E1218;
  --surface2: #141920;
  --surface3: #1A2030;
  --border: rgba(255,255,255,0.07);
  --border-bright: rgba(0,200,212,0.3);
  --text: #F0F4FF;
  --text2: #8892A4;
  --text3: #4A5568;
  --eleve: #FF4757;
  --moyen: #FF9A3C;
  --faible: #F0C040;
  --eleve-bg: rgba(255,71,87,0.1);
  --moyen-bg: rgba(255,154,60,0.1);
  --faible-bg: rgba(240,192,64,0.1);
}
*, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior: smooth; }
body {
  font-family: 'Urbanist', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── GRID LAYOUT ── */
.app-grid {
  display: grid;
  grid-template-columns: 260px 1fr;
  grid-template-rows: 64px 1fr;
  min-height: 100vh;
}

/* ── TOPBAR ── */
.topbar {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  padding: 0 1.5rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
  gap: 1rem;
}
.logo-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-right: auto;
}
.logo-svg {
  height: 32px;
  flex-shrink: 0;
}
.logo-divider {
  width: 1px;
  height: 28px;
  background: var(--border);
}
.logo-title {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text2);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  line-height: 1.3;
}
.logo-title strong {
  display: block;
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.update-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.67rem;
  color: var(--text3);
  background: var(--surface2);
  border: 1px solid var(--border);
  padding: 4px 10px;
  border-radius: 4px;
}
.btn-refresh {
  display: flex;
  align-items: center;
  gap: 6px;
  background: linear-gradient(135deg, var(--cyan) 0%, var(--violet) 100%);
  color: white;
  border: none;
  padding: 7px 16px;
  border-radius: 6px;
  font-family: 'Urbanist', sans-serif;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.15s;
  letter-spacing: 0.02em;
}
.btn-refresh:hover { opacity: 0.88; transform: translateY(-1px); }
.btn-refresh:active { transform: translateY(0); }
.btn-refresh:disabled { opacity: 0.4; cursor: wait; }
.spin { display: inline-block; }
.btn-refresh.loading .spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── SIDEBAR ── */
.sidebar {
  background: var(--surface);
  border-right: 1px solid var(--border);
  padding: 1.5rem 0;
  position: sticky;
  top: 64px;
  height: calc(100vh - 64px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.sidebar-section {
  padding: 0.5rem 1rem 0.25rem;
  font-size: 0.62rem;
  font-weight: 700;
  color: var(--text3);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-top: 0.5rem;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0.55rem 1.25rem;
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--text2);
  cursor: pointer;
  border: none;
  background: none;
  width: 100%;
  text-align: left;
  transition: all 0.15s;
  border-left: 2px solid transparent;
  font-family: 'Urbanist', sans-serif;
}
.nav-item:hover { color: var(--text); background: var(--surface2); }
.nav-item.actif {
  color: var(--cyan);
  background: var(--cyan-dim);
  border-left-color: var(--cyan);
}
.nav-item .icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  opacity: 0.7;
}
.nav-item.actif .icon { opacity: 1; }
.nav-badge {
  margin-left: auto;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 20px;
  background: var(--surface3);
  color: var(--text3);
  font-family: 'JetBrains Mono', monospace;
}
.nav-item.actif .nav-badge {
  background: var(--cyan-dim);
  color: var(--cyan);
}

/* Alert counts in sidebar */
.sidebar-alerts {
  margin: 1rem 1.25rem 0;
  padding: 0.75rem;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.alert-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.73rem;
}
.alert-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-right: 6px;
}
.alert-dot.eleve { background: var(--eleve); box-shadow: 0 0 6px var(--eleve); }
.alert-dot.moyen { background: var(--moyen); }
.alert-dot.faible { background: var(--faible); }
.alert-label { display: flex; align-items: center; color: var(--text2); }
.alert-count { font-weight: 700; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; }
.alert-count.eleve { color: var(--eleve); }
.alert-count.moyen { color: var(--moyen); }
.alert-count.faible { color: var(--faible); }

/* ── MAIN CONTENT ── */
.main {
  padding: 2rem;
  overflow-y: auto;
}

/* ── TAB CONTENT ── */
.tab-panel { display: none; }
.tab-panel.actif { display: block; }

/* ── PAGE HEADER ── */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 1.75rem;
  gap: 1rem;
}
.page-title {
  font-size: 1.6rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.page-title span {
  background: linear-gradient(90deg, var(--cyan), var(--violet));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.page-sub {
  font-size: 0.8rem;
  color: var(--text2);
  margin-top: 0.25rem;
  font-weight: 400;
}

/* ── STATS ROW ── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
  margin-bottom: 1.75rem;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.1rem;
  position: relative;
  overflow: hidden;
  transition: border-color 0.2s;
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
}
.stat-card.total::before { background: linear-gradient(90deg, var(--cyan), var(--violet)); }
.stat-card.eleve::before { background: var(--eleve); }
.stat-card.moyen::before { background: var(--moyen); }
.stat-card.faible::before { background: var(--faible); }
.stat-label {
  font-size: 0.67rem;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 600;
  margin-bottom: 0.4rem;
}
.stat-num {
  font-size: 1.9rem;
  font-weight: 800;
  font-family: 'JetBrains Mono', monospace;
  line-height: 1;
}
.stat-card.total .stat-num { color: var(--cyan); }
.stat-card.eleve .stat-num { color: var(--eleve); }
.stat-card.moyen .stat-num { color: var(--moyen); }
.stat-card.faible .stat-num { color: var(--faible); }

/* ── FILTER BAR ── */
.filter-bar {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 1.25rem;
  flex-wrap: wrap;
}
.filter-label {
  font-size: 0.68rem;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 600;
  margin-right: 0.25rem;
}
.pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 20px;
  font-size: 0.73rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid;
  background: transparent;
  font-family: 'Urbanist', sans-serif;
  transition: all 0.15s;
  letter-spacing: 0.02em;
}
.pill.tous { color: var(--cyan); border-color: rgba(0,200,212,0.3); }
.pill.eleve { color: var(--eleve); border-color: rgba(255,71,87,0.3); }
.pill.moyen { color: var(--moyen); border-color: rgba(255,154,60,0.3); }
.pill.faible { color: var(--faible); border-color: rgba(240,192,64,0.3); }
.pill.tous.actif, .pill.tous:hover { background: var(--cyan-dim); border-color: var(--cyan); }
.pill.eleve.actif, .pill.eleve:hover { background: var(--eleve-bg); border-color: var(--eleve); }
.pill.moyen.actif, .pill.moyen:hover { background: var(--moyen-bg); border-color: var(--moyen); }
.pill.faible.actif, .pill.faible:hover { background: var(--faible-bg); border-color: var(--faible); }
.pill-n {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  font-weight: 700;
}

/* ── SOURCE GROUP ── */
.source-group {
  margin-bottom: 2rem;
}
.source-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.75rem;
  padding: 0.6rem 0.8rem;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.source-icon {
  font-size: 14px;
}
.source-name {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.source-count {
  font-size: 0.68rem;
  color: var(--text3);
  font-family: 'JetBrains Mono', monospace;
}
.source-link {
  margin-left: auto;
  font-size: 0.68rem;
  color: var(--cyan);
  text-decoration: none;
  font-weight: 600;
  letter-spacing: 0.03em;
  opacity: 0.7;
  transition: opacity 0.15s;
}
.source-link:hover { opacity: 1; }

/* ── ITEM CARDS ── */
.items-feed { display: flex; flex-direction: column; gap: 0.5rem; }
.item-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.9rem 1rem;
  display: grid;
  grid-template-columns: 3px 1fr auto;
  gap: 0.75rem;
  align-items: start;
  transition: border-color 0.15s, transform 0.15s;
  text-decoration: none;
  color: inherit;
}
.item-card:hover {
  border-color: rgba(0,200,212,0.25);
  transform: translateX(2px);
}
.item-bar { border-radius: 3px; align-self: stretch; min-height: 32px; }
.item-bar.eleve { background: var(--eleve); box-shadow: 0 0 8px rgba(255,71,87,0.5); }
.item-bar.moyen { background: var(--moyen); }
.item-bar.faible { background: var(--faible); }
.item-bar.none { background: var(--surface3); }
.item-body { min-width: 0; }
.item-titre {
  font-size: 0.85rem;
  font-weight: 500;
  line-height: 1.5;
  color: var(--text);
  margin-bottom: 0.4rem;
}
.item-titre a {
  color: inherit;
  text-decoration: none;
}
.item-titre a:hover { color: var(--cyan); }
.item-tags { display: flex; gap: 0.4rem; flex-wrap: wrap; align-items: center; }
.tag {
  font-size: 0.63rem;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.tag.eleve { background: var(--eleve-bg); color: var(--eleve); border: 1px solid rgba(255,71,87,0.2); }
.tag.moyen { background: var(--moyen-bg); color: var(--moyen); border: 1px solid rgba(255,154,60,0.2); }
.tag.faible { background: var(--faible-bg); color: var(--faible); border: 1px solid rgba(240,192,64,0.2); }
.tag.statut { background: var(--surface3); color: var(--text2); border: 1px solid var(--border); font-weight: 500; text-transform: none; letter-spacing: 0; font-size: 0.65rem; }
.tag.mots { background: transparent; color: var(--text3); border: none; font-weight: 400; font-style: italic; text-transform: none; letter-spacing: 0; font-size: 0.63rem; }
.item-date {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem;
  color: var(--text3);
  white-space: nowrap;
  padding-top: 2px;
}

/* ── EXTERNAL CARD ── */
.extern-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1rem 1.2rem;
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.75rem;
  border-left: 3px solid;
}
.extern-card.cyan { border-left-color: var(--cyan); }
.extern-card.violet { border-left-color: var(--violet); }
.extern-body { flex: 1; }
.extern-title { font-size: 0.88rem; font-weight: 700; margin-bottom: 0.25rem; }
.extern-desc { font-size: 0.75rem; color: var(--text2); line-height: 1.5; }
.extern-btn {
  background: var(--surface2);
  color: var(--cyan);
  border: 1px solid rgba(0,200,212,0.2);
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 0.72rem;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
  transition: all 0.15s;
  font-family: 'Urbanist', sans-serif;
}
.extern-btn:hover { background: var(--cyan-dim); border-color: var(--cyan); }

/* ── PLACEHOLDER ── */
.empty-state {
  text-align: center;
  padding: 2.5rem;
  background: var(--surface);
  border: 1px dashed var(--border);
  border-radius: 10px;
  color: var(--text3);
  font-size: 0.82rem;
}
.empty-state strong {
  display: block;
  font-size: 1rem;
  color: var(--text2);
  margin-bottom: 0.4rem;
  font-weight: 700;
}

/* ── NORMATIVE PLACEHOLDER ── */
.placeholder-card {
  background: var(--surface);
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 1.5rem;
  text-align: center;
  color: var(--text3);
  font-size: 0.8rem;
}
.placeholder-card strong {
  display: block;
  color: var(--text2);
  font-size: 0.88rem;
  margin-bottom: 0.3rem;
  font-weight: 700;
}

/* ── TOAST ── */
.toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  background: var(--surface2);
  color: var(--text);
  padding: 0.8rem 1.2rem;
  border-radius: 10px;
  border: 1px solid var(--border);
  border-top: 2px solid var(--cyan);
  font-size: 0.8rem;
  font-weight: 500;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  transform: translateY(80px);
  opacity: 0;
  transition: all 0.3s cubic-bezier(.175,.885,.32,1.275);
  z-index: 999;
  max-width: 300px;
}
.toast.show { transform: translateY(0); opacity: 1; }

/* ── SCROLLBAR ── */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--surface3); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text3); }
</style>
</head>
<body>

<div class="app-grid">

<!-- ══ TOPBAR ══ -->
<header class="topbar">
  <div class="logo-wrap">
    <!-- Logo SWAM SVG inline -->
    <svg class="logo-svg" viewBox="0 0 120 38" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="swamGrad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#00C8D4"/>
          <stop offset="55%" stop-color="#5C6BC0"/>
          <stop offset="100%" stop-color="#7B2D8B"/>
        </linearGradient>
      </defs>
      <text x="0" y="27" font-family="'Urbanist',sans-serif" font-size="28" font-weight="800" fill="url(#swamGrad)" letter-spacing="-1">swam</text>
      <text x="1" y="37" font-family="'Urbanist',sans-serif" font-size="7.5" font-weight="500" fill="#8892A4" letter-spacing="2.5">SWITCH AL MAGHRIB</text>
    </svg>
    <div class="logo-divider"></div>
    <div class="logo-title">
      <strong>Radar Réglementaire</strong>
      Veille Légale & Normative
    </div>
  </div>
  <div class="topbar-right">
    <span class="update-badge">MAJ {{ last_update }}</span>
    <button class="btn-refresh" onclick="lancerScrape(this)">
      <span class="spin">↻</span> Actualiser
    </button>
  </div>
</header>

<!-- ══ SIDEBAR ══ -->
<nav class="sidebar">
  <div class="sidebar-section">Navigation</div>

  <button class="nav-item actif" onclick="changerOnglet('legal', this)">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M12 1L3 5v6c0 5.5 3.8 10.7 9 12 5.2-1.3 9-6.5 9-12V5L12 1z"/>
    </svg>
    Veille Légale
    <span class="nav-badge">{{ stats_legal.total }}</span>
  </button>

  <button class="nav-item" onclick="changerOnglet('reglementaire', this)">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="2" y="3" width="20" height="14" rx="2"/>
      <path d="M8 21h8M12 17v4"/>
    </svg>
    Veille Réglementaire
    <span class="nav-badge">{{ stats_regl.total }}</span>
  </button>

  <button class="nav-item" onclick="changerOnglet('normative', this)">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/>
      <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
    </svg>
    Veille Normative
    <span class="nav-badge">—</span>
  </button>

  <div class="sidebar-section" style="margin-top:1rem;">Alertes globales</div>
  <div class="sidebar-alerts">
    <div class="alert-row">
      <span class="alert-label"><span class="alert-dot eleve"></span>Élevé</span>
      <span class="alert-count eleve">{{ stats_legal.eleve + stats_regl.eleve }}</span>
    </div>
    <div class="alert-row">
      <span class="alert-label"><span class="alert-dot moyen"></span>Moyen</span>
      <span class="alert-count moyen">{{ stats_legal.moyen + stats_regl.moyen }}</span>
    </div>
    <div class="alert-row">
      <span class="alert-label"><span class="alert-dot faible"></span>Faible</span>
      <span class="alert-count faible">{{ stats_legal.faible + stats_regl.faible }}</span>
    </div>
  </div>

  <div class="sidebar-section" style="margin-top:1rem;">Sources</div>
  <a class="nav-item" href="https://www.chambredesrepresentants.ma" target="_blank" style="text-decoration:none;">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
    </svg>
    Chambre des Reps.
    <span style="margin-left:auto;font-size:0.6rem;color:var(--text3)">↗</span>
  </a>
  <a class="nav-item" href="https://www.dgssi.gov.ma" target="_blank" style="text-decoration:none;">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
      <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    </svg>
    DGSSI
    <span style="margin-left:auto;font-size:0.6rem;color:var(--text3)">↗</span>
  </a>
  <a class="nav-item" href="https://www.bkam.ma" target="_blank" style="text-decoration:none;">
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="2" y="7" width="20" height="14" rx="2" ry="2"/>
      <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>
    </svg>
    Bank Al-Maghrib
    <span style="margin-left:auto;font-size:0.6rem;color:var(--text3)">↗</span>
  </a>
</nav>

<!-- ══ MAIN ══ -->
<main class="main">

  <!-- ─── LÉGAL ─── -->
  <div class="tab-panel actif" id="tab-legal">
    <div class="page-header">
      <div>
        <h1 class="page-title">Veille <span>Légale</span></h1>
        <p class="page-sub">Projets de loi, textes adoptés, arrêtés — Chambre & DGSSI</p>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat-card total">
        <div class="stat-label">Total documents</div>
        <div class="stat-num">{{ stats_legal.total }}</div>
      </div>
      <div class="stat-card eleve">
        <div class="stat-label">Alerte élevée</div>
        <div class="stat-num">{{ stats_legal.eleve }}</div>
      </div>
      <div class="stat-card moyen">
        <div class="stat-label">Alerte moyenne</div>
        <div class="stat-num">{{ stats_legal.moyen }}</div>
      </div>
      <div class="stat-card faible">
        <div class="stat-label">Alerte faible</div>
        <div class="stat-num">{{ stats_legal.faible }}</div>
      </div>
    </div>

    <div class="filter-bar">
      <span class="filter-label">Filtre</span>
      <button class="pill tous actif" onclick="filtrer('legal','tous',this)"><span class="pill-n">{{ stats_legal.total }}</span> Tous</button>
      <button class="pill eleve" onclick="filtrer('legal','eleve',this)"><span class="pill-n">{{ stats_legal.eleve }}</span> Élevé</button>
      <button class="pill moyen" onclick="filtrer('legal','moyen',this)"><span class="pill-n">{{ stats_legal.moyen }}</span> Moyen</button>
      <button class="pill faible" onclick="filtrer('legal','faible',this)"><span class="pill-n">{{ stats_legal.faible }}</span> Faible</button>
    </div>

    <!-- Chambre -->
    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">⚖️</span>
        <span class="source-name">Chambre des Représentants</span>
        <span class="source-count">{{ chambre|length }} doc.</span>
        <a href="https://www.chambredesrepresentants.ma/fr/action-legislative" target="_blank" class="source-link">↗ Accéder</a>
      </div>
      {% if chambre %}
      <div class="items-feed" id="legal-chambre">
        {% for item in chambre %}
        <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
          <div class="item-bar {{ item.alerte_niveau or 'none' }}"></div>
          <div class="item-body">
            <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
            <div class="item-tags">
              {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}
              <span class="tag statut">{{ item.statut }}</span>
              {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:60] }}</span>{% endif %}
            </div>
          </div>
          <div class="item-date">{{ item.date_pub or item.date_scrape[:10] }}</div>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty-state"><strong>Aucun projet de loi</strong>Clique sur ↻ Actualiser pour lancer le scraping</div>
      {% endif %}
    </div>

    <!-- DGSSI -->
    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">🔒</span>
        <span class="source-name">DGSSI — Textes législatifs</span>
        <span class="source-count">{{ dgssi|length }} doc.</span>
        <a href="https://www.dgssi.gov.ma/fr/textes-legislatifs-et-reglementaires/" target="_blank" class="source-link">↗ Accéder</a>
      </div>
      {% if dgssi %}
      <div class="items-feed" id="legal-dgssi">
        {% for item in dgssi %}
        <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
          <div class="item-bar {{ item.alerte_niveau or 'none' }}"></div>
          <div class="item-body">
            <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
            <div class="item-tags">
              {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_legal[item.alerte_niveau].label }}</span>{% endif %}
              <span class="tag statut">{{ item.statut }}</span>
              {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:60] }}</span>{% endif %}
            </div>
          </div>
          <div class="item-date">{{ item.date_scrape[:10] }}</div>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty-state"><strong>Aucun document DGSSI</strong>Clique sur ↻ Actualiser</div>
      {% endif %}
    </div>
  </div>


  <!-- ─── RÉGLEMENTAIRE ─── -->
  <div class="tab-panel" id="tab-reglementaire">
    <div class="page-header">
      <div>
        <h1 class="page-title">Veille <span>Réglementaire</span></h1>
        <p class="page-sub">Circulaires BAM, décisions réglementaires, lettres circulaires</p>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat-card total">
        <div class="stat-label">Total documents</div>
        <div class="stat-num">{{ stats_regl.total }}</div>
      </div>
      <div class="stat-card eleve">
        <div class="stat-label">Alerte élevée</div>
        <div class="stat-num">{{ stats_regl.eleve }}</div>
      </div>
      <div class="stat-card moyen">
        <div class="stat-label">Alerte moyenne</div>
        <div class="stat-num">{{ stats_regl.moyen }}</div>
      </div>
      <div class="stat-card faible">
        <div class="stat-label">Alerte faible</div>
        <div class="stat-num">{{ stats_regl.faible }}</div>
      </div>
    </div>

    <div class="filter-bar">
      <span class="filter-label">Filtre</span>
      <button class="pill tous actif" onclick="filtrer('reglementaire','tous',this)"><span class="pill-n">{{ stats_regl.total }}</span> Tous</button>
      <button class="pill eleve" onclick="filtrer('reglementaire','eleve',this)"><span class="pill-n">{{ stats_regl.eleve }}</span> Élevé</button>
      <button class="pill moyen" onclick="filtrer('reglementaire','moyen',this)"><span class="pill-n">{{ stats_regl.moyen }}</span> Moyen</button>
      <button class="pill faible" onclick="filtrer('reglementaire','faible',this)"><span class="pill-n">{{ stats_regl.faible }}</span> Faible</button>
    </div>

    <!-- BAM -->
    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">🏦</span>
        <span class="source-name">Bank Al-Maghrib — Systèmes de paiement</span>
        <span class="source-count">{{ bam|length }} doc.</span>
        <a href="https://www.bkam.ma/Trouvez-l-information-concernant/Reglementation/Systemes-et-moyens-de-paiement" target="_blank" class="source-link">↗ Accéder</a>
      </div>
      {% if bam %}
      <div class="items-feed" id="regl-bam">
        {% for item in bam %}
        <div class="item-card" data-alerte="{{ item.alerte_niveau or 'none' }}">
          <div class="item-bar {{ item.alerte_niveau or 'none' }}"></div>
          <div class="item-body">
            <div class="item-titre"><a href="{{ item.url }}" target="_blank">{{ item.titre }}</a></div>
            <div class="item-tags">
              {% if item.alerte_niveau %}<span class="tag {{ item.alerte_niveau }}">{{ alertes_regl[item.alerte_niveau].label }}</span>{% endif %}
              <span class="tag statut">{{ item.statut }}</span>
              {% if item.alerte_mots %}<span class="tag mots">{{ item.alerte_mots[:60] }}</span>{% endif %}
            </div>
          </div>
          <div class="item-date">{{ item.date_scrape[:10] }}</div>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty-state"><strong>Aucune circulaire / décision</strong>Clique sur ↻ Actualiser</div>
      {% endif %}
    </div>

    <!-- Conseil Concurrence -->
    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">⚖️</span>
        <span class="source-name">Conseil de la Concurrence</span>
      </div>
      <div class="extern-card violet">
        <div class="extern-body">
          <div class="extern-title">Conseil de la Concurrence — Maroc</div>
          <div class="extern-desc">Décisions impactant le secteur des paiements électroniques et nouveaux entrants du marché.</div>
        </div>
        <a href="https://conseil-concurrence.ma/" target="_blank" class="extern-btn">↗ Accéder</a>
      </div>
    </div>
  </div>


  <!-- ─── NORMATIVE ─── -->
  <div class="tab-panel" id="tab-normative">
    <div class="page-header">
      <div>
        <h1 class="page-title">Veille <span>Normative</span></h1>
        <p class="page-sub">Standards internationaux, BIS-CPMI, cybersécurité</p>
      </div>
    </div>

    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">🌐</span>
        <span class="source-name">BIS — CPMI</span>
      </div>
      <div class="extern-card cyan">
        <div class="extern-body">
          <div class="extern-title">Committee on Payments and Market Infrastructures</div>
          <div class="extern-desc">Publications, rapports et standards internationaux sur les systèmes de paiement et l'infrastructure des marchés financiers.</div>
        </div>
        <a href="https://www.bis.org/cpmi/about/overview.htm" target="_blank" class="extern-btn">↗ Accéder</a>
      </div>
    </div>

    <div class="source-group">
      <div class="source-header">
        <span class="source-icon">🔐</span>
        <span class="source-name">Cybersécurité — Normes internationales</span>
      </div>
      <div class="placeholder-card">
        <strong>Contenu à venir</strong>
        ISO 27001, PCI-DSS, SWIFT CSP et autres référentiels cyber applicables à SWAM.
      </div>
    </div>
  </div>

</main>
</div>

<div class="toast" id="toast"></div>

<script>
function changerOnglet(id, btn) {
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('actif'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('actif'));
  btn.classList.add('actif');
  document.getElementById('tab-' + id).classList.add('actif');
}

function filtrer(onglet, type, btn) {
  btn.closest('.tab-panel').querySelectorAll('.pill').forEach(b => b.classList.remove('actif'));
  btn.classList.add('actif');
  document.getElementById('tab-' + onglet).querySelectorAll('.item-card').forEach(card => {
    card.style.display = (type === 'tous' || card.dataset.alerte === type) ? '' : 'none';
  });
}

async function lancerScrape(btn) {
  btn.disabled = true;
  btn.classList.add('loading');
  btn.querySelector('.spin').textContent = '⟳';
  showToast('Scraping en cours...');
  try {
    const d = await fetch('/api/scrape').then(r => r.json());
    const msg = d.nouveaux + ' nouveaux' + (d.alertes_elevees > 0 ? ' · 🔴 ' + d.alertes_elevees + ' alertes !' : '');
    showToast('✓ ' + msg);
    setTimeout(() => location.reload(), 2800);
  } catch(e) {
    showToast('✗ Erreur lors du scraping');
  } finally {
    btn.disabled = false;
    btn.classList.remove('loading');
    btn.querySelector('.spin').textContent = '↻';
  }
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 4000);
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
