"""
Bilan mensuel au format PDF (bibliothèque fpdf2) : chiffres clés, dépenses par catégorie
avec budgets, objectifs d'épargne, recommandations, conseils IA éventuels et liste des mouvements.
"""
import os
from datetime import datetime
from typing import List, Optional

from fpdf import FPDF

from recommandations import MOIS_FR, StatsMois, fcfa

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "logo-192.png")
VERT = (15, 110, 90)
ROUGE = (192, 57, 43)
ORANGE = (185, 119, 14)
VERT_CLAIR = (30, 132, 73)
GRIS = (107, 115, 133)
FOND = (245, 246, 248)
COULEURS_NIVEAU = {"alerte": ROUGE, "conseil": ORANGE, "bravo": VERT_CLAIR}

# Les polices intégrées aux PDF ne connaissent que l'alphabet latin (Latin-1) :
# on remplace les quelques caractères typographiques qui n'en font pas partie.
REMPLACEMENTS = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2014": "-", "\u2013": "-",
                 "\u2026": "...", "\u0153": "oe", "\u0152": "OE", "\u202f": " ", "\u2022": "-", "\u20ac": "EUR"}


def latin1(texte) -> str:
    texte = str(texte)
    for a, b in REMPLACEMENTS.items():
        texte = texte.replace(a, b)
    return texte.encode("latin-1", "ignore").decode("latin-1")


class BilanPDF(FPDF):
    def __init__(self, titre: str):
        super().__init__(format="A4")
        self.titre = titre
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(16, 14, 16)

    def header(self):
        if os.path.exists(LOGO):
            self.image(LOGO, x=16, y=11, w=11, h=11)
        self.set_xy(30, 12)
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*VERT)
        self.cell(0, 9, "Finances Perso")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*GRIS)
        self.set_xy(16, 12)
        self.cell(0, 9, latin1(self.titre), align="R")
        self.set_draw_color(226, 229, 234)
        self.line(16, 25, self.w - 16, 25)
        self.set_y(30)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRIS)
        self.cell(0, 6, latin1(f"Généré le {datetime.now():%d/%m/%Y à %H:%M} - page {self.page_no()}/{{nb}}"),
                  align="C")

    def titre_section(self, texte: str):
        if self.get_y() > self.h - 45:
            self.add_page()
        self.ln(3)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(29, 36, 51)
        self.cell(0, 8, latin1(texte), new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def texte(self, texte: str, taille: int = 10, couleur=(29, 36, 51), style: str = ""):
        self.set_font("Helvetica", style, taille)
        self.set_text_color(*couleur)
        self.multi_cell(0, 5, latin1(texte), new_x="LMARGIN", new_y="NEXT")

    def barre(self, x: float, y: float, largeur: float, pourcentage: float, couleur=VERT):
        self.set_fill_color(*FOND)
        self.set_draw_color(226, 229, 234)
        self.rect(x, y, largeur, 3.5, style="DF")
        if pourcentage > 0:
            self.set_fill_color(*couleur)
            self.rect(x, y, largeur * min(pourcentage, 100) / 100, 3.5, style="F")


def generer_bilan_pdf(nom: str, annee: int, mois: int, s: StatsMois, recommandations: List[dict],
                      mouvements: list, conseils_ia: Optional[List[str]] = None) -> bytes:
    nom_mois = f"{MOIS_FR[mois - 1]} {annee}"
    pdf = BilanPDF(f"Bilan de {nom_mois} - {nom}")
    pdf.alias_nb_pages()
    pdf.add_page()
    largeur = pdf.w - 32

    # Chiffres clés : 4 cases
    solde = s.revenus - s.depenses
    taux = round(s.par_groupe.get("epargne", 0) * 100 / s.revenus) if s.revenus else 0
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(29, 36, 51)
    pdf.cell(0, 10, latin1(f"Bilan de {nom_mois}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    cases = [("Revenus", fcfa(s.revenus), VERT_CLAIR), ("Dépenses", fcfa(s.depenses), ROUGE),
             ("Solde", fcfa(solde), VERT_CLAIR if solde >= 0 else ROUGE), ("Taux d'épargne", f"{taux} %", VERT)]
    l_case, y = (largeur - 9) / 4, pdf.get_y()
    for i, (libelle, valeur, couleur) in enumerate(cases):
        x = 16 + i * (l_case + 3)
        pdf.set_fill_color(*FOND)
        pdf.rect(x, y, l_case, 18, style="F")
        pdf.set_xy(x + 3, y + 2.5)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*GRIS)
        pdf.cell(l_case - 6, 5, latin1(libelle))
        pdf.set_xy(x + 3, y + 8.5)
        pdf.set_font("Helvetica", "B", 11.5)
        pdf.set_text_color(*couleur)
        pdf.cell(l_case - 6, 7, latin1(valeur))
    pdf.set_y(y + 22)

    # Dépenses par catégorie
    pdf.titre_section("Dépenses par catégorie")
    categories = sorted(s.par_categorie.items(), key=lambda c: -c[1])
    if not categories:
        pdf.texte("Aucune dépense ce mois-ci.", couleur=GRIS)
    for cat, montant in categories:
        budget = s.budgets.get(cat)
        part = montant * 100 / s.depenses if s.depenses else 0
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(29, 36, 51)
        pdf.cell(55, 6, latin1(cat))
        y = pdf.get_y()
        if budget:
            pourcentage = montant * 100 / budget
            couleur = ROUGE if pourcentage > 100 else ORANGE if pourcentage >= 80 else VERT
            pdf.barre(72, y + 1.3, 55, pourcentage, couleur)
            pdf.set_x(130)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*GRIS)
            pdf.cell(22, 6, latin1(f"{pourcentage:.0f} % budget"))
        else:
            pdf.barre(72, y + 1.3, 55, part, (120, 170, 160))
            pdf.set_x(130)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*GRIS)
            pdf.cell(22, 6, latin1(f"{part:.0f} % du total"))
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(29, 36, 51)
        pdf.cell(0, 6, latin1(fcfa(montant)), align="R", new_x="LMARGIN", new_y="NEXT")

    # Objectifs d'épargne
    if s.objectifs:
        pdf.titre_section("Objectifs d'épargne")
        for o in s.objectifs:
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(29, 36, 51)
            pdf.cell(55, 6, latin1(o["nom"]))
            y = pdf.get_y()
            couleur = VERT_CLAIR if o["statut"] == "atteint" else ROUGE if o["statut"] == "echeance_depassee" else VERT
            pdf.barre(72, y + 1.3, 55, o["pourcentage"], couleur)
            pdf.set_x(130)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*GRIS)
            pdf.cell(22, 6, f"{o['pourcentage']} %")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(29, 36, 51)
            pdf.cell(0, 6, latin1(f"{fcfa(o['epargne'])} / {fcfa(o['montant_cible'])}"), align="R",
                     new_x="LMARGIN", new_y="NEXT")

    # Recommandations
    pdf.titre_section("Recommandations")
    if not recommandations:
        pdf.texte("Aucune recommandation pour ce mois.", couleur=GRIS)
    for r in recommandations:
        y = pdf.get_y()
        pdf.set_x(20)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(29, 36, 51)
        pdf.multi_cell(largeur - 4, 5, latin1(r["titre"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(20)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*GRIS)
        pdf.multi_cell(largeur - 4, 4.8, latin1(r["message"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_fill_color(*COULEURS_NIVEAU.get(r["niveau"], GRIS))
        if pdf.get_y() > y:  # pas de trait si la recommandation a changé de page
            pdf.rect(16, y, 1.3, pdf.get_y() - y, style="F")
        pdf.ln(2)

    if conseils_ia:
        pdf.titre_section("Conseils de l'IA")
        for c in conseils_ia:
            pdf.set_x(20)
            pdf.texte(f"- {c}", taille=9.5)
            pdf.ln(1)

    # Mouvements du mois
    pdf.titre_section(f"Mouvements ({len(mouvements)})")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*GRIS)
    pdf.cell(22, 6, "Date")
    pdf.cell(80, 6, latin1("Libellé"))
    pdf.cell(45, 6, latin1("Catégorie"))
    pdf.cell(0, 6, "Montant", align="R", new_x="LMARGIN", new_y="NEXT")
    for m in mouvements:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(29, 36, 51)
        pdf.cell(22, 5.5, m.date.strftime("%d/%m/%Y"))
        pdf.cell(80, 5.5, latin1(m.libelle[:48]))
        pdf.cell(45, 5.5, latin1("Revenu" if m.type == "revenu" else (m.categorie_nom or "")))
        pdf.set_text_color(*(VERT_CLAIR if m.type == "revenu" else ROUGE))
        signe = "+" if m.type == "revenu" else "-"
        pdf.cell(0, 5.5, latin1(f"{signe} {fcfa(m.montant)}"), align="R", new_x="LMARGIN", new_y="NEXT")
    if not mouvements:
        pdf.texte("Aucun mouvement ce mois-ci.", couleur=GRIS)
    return bytes(pdf.output())
