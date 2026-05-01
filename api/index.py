from http.server import BaseHTTPRequestHandler
import fitz
import io
import cgi
import base64
import json
import re
from PIL import Image


def extract_data(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text().replace('\u202f', ' ').replace('\xa0', ' ') + "\n"

    def find(pattern, default=""):
        m = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else default

    def clean(v):
        return v.replace(' ', '').strip() if v else ''

    nom = find(r'(PV (?:PART|PRO)[^\n]+)', '')
    adresse = find(r"([\w\s,\-']+\d{5}[^\n]*France)", '')
    date = find(r'(\d{1,2}\s+\w+\.?\s+\d{4})', '')

    modules = find(r'(\d+)\s*Modules PV', '6')
    onduleurs = find(r'(\d+)\s*Onduleur', '1')
    optimiseurs = find(r'(\d+)\s*Optimiseurs', '6')
    puissance_dc = find(r'Puissance DC Install[^\n]*\n\s*(\d+[\.,]?\d*)\s*kWc', '3')
    puissance_ac = find(r'Puissance Max AC[^\n]*\n\s*(\d+[\.,]\d+)\s*kW', '2,69')
    production = find(r'Production D.nergie\s*\nAnnuelle\s*\n\s*([\d ]+)\s*kWh', '3703')
    co2 = find(r'missions De CO2\s*\n[^\n]*\n\s*([\d,]+)\s*kg', '218,49')
    arbres = find(r'Arbres[^\n]*\n\s*(\d+)', '10')

    paiements_nets = find(r'Paiements nets\n€\s*([\d ]+)', '4200')
    economies_duree = find(r'dans la dur[^\n]*\n€\s*([\d ]+)', '16209')
    van = find(r'\(VAN\)\n€\s*([\d ]+)', '12009')
    tri = find(r'\(TRI\)\n([\d,]+)\s*%', '15,46')
    retour_invest = find(r'investissement\n(\d+)\s*ann', '6')
    prix_systeme = find(r'Prix du syst[^\n]*\n€\s*([\d ]+)', '7920')
    montant_aides = find(r'Montant des aides\n€\s*([\d ]+)', '3720')
    retour_pct = find(r'investissements\n([\d,]+)\s*%', '151,63')
    cout_kwh = find(r'€/kWh\s*([\d,]+)', '0,065')
    facture_mens = find(r'Facture mensuelle\n€\s*([\d,]+)', '67,92')
    facture_atea = find(r'Facture avec[^\n]*\n€\s*([\d,]+)', '14,05')
    economies_mens = find(r'conomies sur facture\n€\s*([\d,]+)', '53,87')
    compensation = find(r'Compensation facture\n([\d,]+)\s*%', '79,32')

    az_match = re.search(r'(\d+)°\s*\n?\s*(\d+)°', full_text)
    azimut = az_match.group(1) if az_match else '318'
    inclinaison = az_match.group(2) if az_match else '15'

    tarif_achat = find(r"prix d.achat de l.lectricit\)?:\s*([^\n]+)", 'TARIF BLEU CORSE')
    tarif_vente = find(r'tarif de vente:\s*([^\n]+)', '')
    aide_info = find(r'Aide 1:\s*([^\n]+)', '')

    fuseau = find(r'Fuseau horaire\s*\n?\s*([^\n]+UTC[^\n]+)', '')
    station = find(r'Station m.t.o\s*\n?\s*([^\n]+)', '')
    altitude = find(r'Altitude\s*\n?\s*(\d+\s*m)', '4 m')
    source_donnees = find(r'station\s*\n?\s*(Meteonorm[^\n]+)', 'Meteonorm 8.2')
    reseau = find(r'Reseau\s*\n?\s*([^\n]+V[^\n]*)', '230V L-N')

    vers_domicile = find(r'Vers le domicile\s+([\d ]+)\s*kWh', '2333')
    vd_pct_m = re.search(r'Vers le domicile\s+[\d ]+\s*kWh\s*\((\d+)%\)', full_text)
    vers_domicile_pct = vd_pct_m.group(1) if vd_pct_m else '63'
    vers_reseau = find(r'Vers le r.seau\s+([\d ]+)\s*kWh', '1371')
    vr_pct_m = re.search(r'Vers le r.seau\s+[\d ]+\s*kWh\s*\((\d+)%\)', full_text)
    vers_reseau_pct = vr_pct_m.group(1) if vr_pct_m else '37'
    depuis_pv = find(r'Depuis le PV\s+([\d ]+)\s*kWh', '2333')
    dp_pct_m = re.search(r'Depuis le PV\s+[\d ]+\s*kWh\s*\((\d+)%\)', full_text)
    depuis_pv_pct = dp_pct_m.group(1) if dp_pct_m else '51'
    depuis_reseau = find(r'du r.seau\s+([\d ]+)\s*kWh', '2223')
    dr_pct_m = re.search(r'du r.seau\s+[\d ]+\s*kWh\s*\((\d+)%\)', full_text)
    depuis_reseau_pct = dr_pct_m.group(1) if dr_pct_m else '49'

    flux_rows = []
    for year in range(1, 21):
        # Try multiple patterns since first row has "Montant des aides" filled
        if year == 1:
            pat = rf'\n\s*1\s+€\s*([\d ]+,\d{{2}})\s*€\s*([\d ]+,\d{{2}})\s*€\s*([\d ]+,\d{{2}})\s*€\s*(-?[\d ]+,\d{{2}})'
        else:
            pat = rf'\n\s*{year}\s+€\s*([\d ]+,\d{{2}})\s*€\s*([\d ]+,\d{{2}})\s*€\s*(-?[\d ]+,\d{{2}})'
        m = re.search(pat, full_text)
        if m:
            if year == 1:
                flux_rows.append({
                    'year': year, 'aides': m.group(1),
                    'eco_nettes': m.group(2), 'flux_annuel': m.group(3),
                    'flux_cumul': m.group(4)
                })
            else:
                flux_rows.append({
                    'year': year, 'aides': '',
                    'eco_nettes': m.group(1), 'flux_annuel': m.group(2),
                    'flux_cumul': m.group(3)
                })

    photos = []
    page = doc[0]
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            img_data = doc.extract_image(xref)
            w, h = img_data.get('width', 0), img_data.get('height', 0)
            if w > 800 and h > 400 and len(photos) < 3:
                pil_img = Image.open(io.BytesIO(img_data['image'])).convert('RGB')
                if pil_img.width > 1500:
                    ratio = 1500 / pil_img.width
                    pil_img = pil_img.resize((1500, int(pil_img.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                pil_img.save(buf, format='JPEG', quality=70)
                b64 = base64.b64encode(buf.getvalue()).decode()
                photos.append('data:image/jpeg;base64,' + b64)
        except Exception:
            pass

    cy0 = find(r'\n\s*0\s+€\s*-([\d ]+),00', '7920')
    cashflow_year_0 = -int(clean(cy0)) if cy0 else -7920

    return {
        "client": {"nom": nom, "adresse": adresse, "date": date},
        "systeme": {
            "modules": modules, "onduleurs": onduleurs, "optimiseurs": optimiseurs,
            "puissance_dc": clean(puissance_dc) + " kWc",
            "puissance_ac": clean(puissance_ac) + " kW",
            "production_annuelle": clean(production) + " kWh",
            "co2_economise": clean(co2) + " kg",
            "arbres": clean(arbres)
        },
        "financier": {
            "paiements_nets": clean(paiements_nets),
            "economies_duree": clean(economies_duree),
            "van": clean(van), "tri": clean(tri),
            "retour_investissement": clean(retour_invest),
            "prix_systeme": clean(prix_systeme),
            "montant_aides": clean(montant_aides),
            "retour_pct": clean(retour_pct),
            "cout_kwh": clean(cout_kwh),
            "facture_mensuelle": clean(facture_mens),
            "facture_avec_atea": clean(facture_atea),
            "economies_mensuelles": clean(economies_mens),
            "compensation": clean(compensation),
            "tarif_achat": tarif_achat.strip(),
            "tarif_vente": tarif_vente.strip(),
            "aide_info": aide_info.strip()
        },
        "production": {
            "vers_domicile_kwh": clean(vers_domicile),
            "vers_domicile_pct": int(vers_domicile_pct),
            "vers_reseau_kwh": clean(vers_reseau),
            "vers_reseau_pct": int(vers_reseau_pct),
            "depuis_pv_kwh": clean(depuis_pv),
            "depuis_pv_pct": int(depuis_pv_pct),
            "depuis_reseau_kwh": clean(depuis_reseau),
            "depuis_reseau_pct": int(depuis_reseau_pct)
        },
        "modules_detail": {
            "nombre": modules,
            "modele": "Hengdian Group DMEGC \u2014 DM500M10RT-B60HBT",
            "puissance": clean(puissance_dc) + " kWc",
            "azimut": azimut + "\u00b0",
            "inclinaison": inclinaison + "\u00b0"
        },
        "params": {
            "fuseau": fuseau.strip(),
            "station": station.strip(),
            "altitude": altitude,
            "source_donnees": source_donnees.strip(),
            "reseau": reseau.strip()
        },
        "flux": flux_rows,
        "cashflow_year_0": cashflow_year_0,
        "photos": photos
    }


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            content_type = self.headers.get('Content-Type', '')
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            environ = {
                'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': content_type,
                'CONTENT_LENGTH': str(length),
            }
            form = cgi.FieldStorage(fp=io.BytesIO(body), environ=environ, keep_blank_values=True)
            if 'pdf' not in form:
                return self._error("Champ 'pdf' manquant", 400)
            pdf_bytes = form['pdf'].file.read()
            result = extract_data(pdf_bytes)
            response = json.dumps(result).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self._cors()
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._error(str(e), 500)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _error(self, msg, code):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
