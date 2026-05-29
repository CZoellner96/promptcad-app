import streamlit as st
import subprocess
import os
import time
import re
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from streamlit_stl import stl_from_file

# 1. Wir holen den Key sicher aus dem Cloud-Tresor und weisen ihn der Variable zu:
API_KEY = st.secrets["GEMINI_API_KEY"]

# 2. Wir konfigurieren die KI:
genai.configure(api_key=API_KEY)

# Wir nutzen das schnelle Flash-Modell für zügige Generierung
model = genai.GenerativeModel('gemini-2.5-flash')

st.set_page_config(initial_sidebar_state="expanded")

# ==========================================
# 0. INITIALISIERUNG
# ==========================================
if "historie" not in st.session_state:
    st.session_state.historie = []
if "generieren_aktiv" not in st.session_state:
    st.session_state.generieren_aktiv = False
if "modell_generiert" not in st.session_state:
    st.session_state.modell_generiert = False
if "textfeld_inhalt" not in st.session_state:
    st.session_state.textfeld_inhalt = ""

# ==========================================
# 1. KI-LOGIK (API & MASTER PROMPT)
# ==========================================

def bereinige_code(raw_text):
    """Entfernt Markdown-Formatierungen, falls die KI welche mitsendet."""
    code = re.sub(r"```(openscad|scad)?\n", "", raw_text, flags=re.IGNORECASE)
    code = code.replace("```", "")
    return code.strip()

def generiere_echten_code(prompt, wandstaerke, deckel_aktiv, gridfinity_aktiv):
    # 👇 MASTER-PROMPT UPDATE: Gridfinity mit Magnetlöchern 👇
    system_anweisung = """
    Du bist ein hochqualifizierter Experte für OpenSCAD und parametrisches Design für den 3D-Druck.
    Deine einzige Aufgabe ist es, die Beschreibungen des Nutzers in fehlerfreien, funktionalen und optimierten OpenSCAD-Code zu übersetzen. Der Fokus liegt auf funktionalen Bauteilen wie Organizer-Boxen, Halterungen, Rastern und technischen Ersatzteilen.
    
    Befolge diese Regeln STRIKT:
    - NUR CODE: Antworte AUSSCHLIESSLICH mit gültigem OpenSCAD-Code. Schreibe absolut keine Erklärungen, keine Begrüßung und keine Kommentare vor oder nach dem Code.
    - KEIN MARKDOWN: Verwende keine Markdown-Formatierungen (wie ```openscad oder ```). Beginne sofort in der ersten Zeile mit dem Programmcode.
    - PARAMETRISCH: Definiere alle wichtigen Maße (Länge, Breite, Höhe, Wandstärke, Lochabstände) als klar benannte Variablen ganz oben im Skript, bevor du die Geometrie aufbaust.
    - GLATTE RUNDUNGEN: Setze ganz oben im Skript immer die Variable $fn = 60;, damit Zylinder und Kurven beim 3D-Druck schön rund und nicht eckig werden.
    - 3D-DRUCK LOGIK: Bedenke, dass die Modelle mit FDM-Druckern gedruckt werden. Wandstärken sollten immer mindestens 1.2mm bis 1.5mm betragen. Vermeide extreme Überhänge.
    - SAUBERE CSG-OPERATIONEN: Nutze Standard-Funktionen wie union(), difference() und intersection() sauber und rücke den Code für gute Lesbarkeit ein.
    - GRIDFINITY WISSENSBASIS: Wenn ein "Gridfinity"-Raster gefordert wird, MUSST du zwingend das folgende Basis-Modul verwenden und es aufrufen (grid_x und grid_y sind die Rastergrößen). Passe die Höhe an den Nutzerprompt an:
      
      module gridfinity_base(grid_x, grid_y) {
          difference() {
              union() {
                  for(i=[0:grid_x-1], j=[0:grid_y-1]) {
                      translate([i*42, j*42, 0]) {
                          hull() {
                              translate([4, 4, 0]) cube([34, 34, 0.1]);
                              translate([0, 0, 4]) cube([42, 42, 0.1]);
                          }
                      }
                  }
                  translate([0, 0, 4]) cube([grid_x*42, grid_y*42, 30]);
              }
              // Aushöhlung der Box
              translate([2, 2, 6]) cube([grid_x*42-4, grid_y*42-4, 35]);
              
              // Magnetlöcher (4 Stück pro Rasterfeld, d=6.5mm)
              for(i=[0:grid_x-1], j=[0:grid_y-1]) {
                  translate([i*42, j*42, 0]) {
                      translate([8, 8, -1]) cylinder(h=4, d=6.5);
                      translate([34, 8, -1]) cylinder(h=4, d=6.5);
                      translate([8, 34, -1]) cylinder(h=4, d=6.5);
                      translate([34, 34, -1]) cylinder(h=4, d=6.5);
                  }
              }
          }
      }
    """
    
    # Nutzer-Wünsche zusammenbauen
    user_kontext = f"Erstelle folgendes 3D-Modell: {prompt}. Die Wandstärke soll {wandstaerke}mm betragen."
    if gridfinity_aktiv:
        user_kontext += " Baue das Modell zwingend auf dem Gridfinity-Standard auf. Nutze dazu das Modul aus deinen Anweisungen."
    if deckel_aktiv:
        user_kontext += " Generiere zusätzlich einen passenden Deckel, der separat neben dem Modell liegt."

    # API-Aufruf
    response = model.generate_content(system_anweisung + "\n\n" + user_kontext)
    return bereinige_code(response.text)

def repariere_code_mit_ki(fehlerhafter_code, fehlermeldung):
    # ECHTES Self-Healing durch KI-Feedback-Schleife
    reparatur_prompt = f"""
    Du bist ein OpenSCAD-Compiler-Experte. Der folgende Code hat beim Kompilieren einen Fehler geworfen.
    
    Fehlermeldung:
    {fehlermeldung}
    
    Fehlerhafter Code:
    {fehlerhafter_code}
    
    Korrigiere den Fehler. Antworte AUSSCHLIESSLICH mit dem reparierten OpenSCAD-Code ohne Markdown-Blöcke.
    """
    response = model.generate_content(reparatur_prompt)
    return bereinige_code(response.text)

def kompiliere_scad_zu_stl(scad_code):
    with open("temp_modell.scad", "w") as f:
        f.write(scad_code)
    try:
        # Passe den Pfad an, falls dein Mac OpenSCAD woanders hat
        result = subprocess.run(
           ['openscad', '-o', 'output.stl', 'temp_modell.scad'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return True, ""
        else:
            return False, result.stderr
    except Exception as e:
        return False, str(e)

# ==========================================
# 2. FRONTEND (IDENTISCH ZUM MVP)
# ==========================================

with st.sidebar:
    st.title("🎛️ Dashboard")
    st.caption("🚀 API Cloud-Edition aktiv")
    with st.expander("⚙️ Basis-Einstellungen", expanded=True):
        wandstaerke = st.slider("Wandstärke (mm)", 1.0, 5.0, 2.0, 0.5)
    with st.expander("🎨 Filament-Farbe (Vorschau)", expanded=True):
        filament_wahl = st.selectbox(
            "Wähle deine Filament-Farbe:",
            ["Bambu Grün", "Prusa Orange", "Mattschwarz", "Signalrot", "Pastellblau", "Eigene Farbe..."]
        )
        if filament_wahl == "Eigene Farbe...":
            filament_farbe = st.color_picker("Farbwähler", "#2b65f8")
        else:
            farben = {
                "Bambu Grün": "#00FF00", "Prusa Orange": "#FF5A00",
                "Mattschwarz": "#1A1A1A", "Signalrot": "#FF0000", "Pastellblau": "#A2C2E8"
            }
            filament_farbe = farben[filament_wahl]
    
    st.markdown("---")
    st.header("📜 Verlauf")
    if not st.session_state.historie:
        st.caption("Noch keine Modelle.")
    else:
        for i, alter_prompt in enumerate(st.session_state.historie):
            kurzer_text = alter_prompt[:20] + "..." if len(alter_prompt) > 20 else alter_prompt
            if st.button(f"⏳ {kurzer_text}", key=f"hist_{i}"):
                st.session_state.textfeld_inhalt = alter_prompt
                st.session_state.generieren_aktiv = True
                st.rerun()
# 👇 HIER EINFÜGEN: Die KI-Richtlinien 👇
    st.markdown("---")
    with st.expander("🛡️ Unsere KI-Richtlinie", expanded=False):
        st.markdown("""
        **Wir bauen auf Vertrauen & Präzision:**
        * 🧠 **Datenschutz:** Deine Prompts werden über die Gemini-API verarbeitet, aber durch unseren Free-Tier-Status nicht für das Training globaler Modelle gespeichert.
        * 📐 **Wissens-Injektion:** Halluzinationen werden durch striktes Prompt-Engineering und fest codierte Industrienormen (z.B. Gridfinity) unterbunden.
        * 🖨️ **Self-Healing:** Unsere Engine fängt fehlerhafte Geometrien ab und repariert sie autonom, bevor sie deinen Drucker erreichen.
        """)

st.title("🛠️ PromptCAD")
st.write("Willkommen im Maschinenraum! Beschreibe dein Modell und passe die Tools an.")

col_text, col_btn = st.columns([3, 1], vertical_alignment="bottom")

with col_text:
    st.markdown("**Was möchtest du drucken?**")
    
with col_btn:
    with st.popover("💡 Beispiele laden", use_container_width=True):
        if st.button("📦 2x3 Gridfinity", use_container_width=True):
            st.session_state.textfeld_inhalt = "Ich brauche eine 2x3 Gridfinity Box."
            st.rerun()
        if st.button("📐 Maß-Box", use_container_width=True):
            st.session_state.textfeld_inhalt = "Erstelle eine offene Box. Sie soll 60mm lang, 40mm breit und 30mm hoch sein."
            st.rerun()
        if st.button("🖊️ Stiftebecher", use_container_width=True):
            st.session_state.textfeld_inhalt = "Mache einen runden Stiftebecher, 80mm hoch und 50mm Durchmesser."
            st.rerun()

user_input = st.text_area(
    label="Verstecktes Label",
    key="textfeld_inhalt",
    placeholder="z.B. Erstelle eine offene Box. Sie soll 40mm lang, 40mm breit und 20mm hoch sein.",
    label_visibility="collapsed", 
    height=100
)
col_links, col_rechts = st.columns([4, 3])

with col_links:
    button_geklickt = st.button("Modell generieren", use_container_width=True)

with col_rechts:
    with st.expander("🛠️ Tool-Box (KI-Erweiterungen)", expanded=False):
        gridfinity_aktiv = st.toggle("📦 Gridfinity-Raster")
        deckel_aktiv = st.toggle("🧢 Passenden Deckel erzeugen")

# --- LOGIK-VERARBEITUNG MIT ECHTER API ---
if button_geklickt or st.session_state.generieren_aktiv:
    st.session_state.generieren_aktiv = False
    st.session_state.modell_generiert = False
    if not user_input.strip():
        st.warning("Bitte gib zuerst einen Text ein!")
    elif API_KEY == "DEIN_API_KEY_HIER":
        st.error("🚨 Halt! Du hast deinen API-Schlüssel noch nicht im Code eingetragen.")
    else:
        if user_input not in st.session_state.historie:
            st.session_state.historie.append(user_input)

        st.info(f"🤖 API-Request gesendet...")

        with st.status("Starte KI-Generierung...", expanded=True) as status:
            st.write("🧠 Gemini 2.5 Flash schreibt den Code...")
            
            try: # 👈 HIER STARTET DER AIRBAG
                # 1. API-Aufruf
                scad_code = generiere_echten_code(user_input, wandstaerke, deckel_aktiv, gridfinity_aktiv)
                
                st.write("🛠️ Kompiliere OpenSCAD-Code (Versuch 1)...")
                erfolg, fehlermeldung = kompiliere_scad_zu_stl(scad_code)
                
                # --- ECHTES SELF-HEALING ---
                if not erfolg:
                    st.write("⚠️ **Syntax-Fehler! Sende Error-Log zurück an die KI...**")
                    st.caption(f"`{fehlermeldung.strip()}`")
                    
                    # 2. API-Aufruf zur Reparatur
                    st.write("🔄 KI analysiert Fehler und repariert den Code...")
                    scad_code = repariere_code_mit_ki(scad_code, fehlermeldung)
                    
                    st.write("🛠️ Kompiliere reparierten Code (Versuch 2)...")
                    erfolg, fehlermeldung = kompiliere_scad_zu_stl(scad_code)
                    
                if erfolg:
                    status.update(label="Modell erfolgreich generiert!", state="complete", expanded=False)
                    st.session_state.modell_generiert = True
                    # Den finalen (ggf. reparierten) Code speichern wir in den State für die Konsole
                    st.session_state.letzter_code = scad_code
                else:
                    status.update(label="KI konnte den Code nicht reparieren. Versuch einen einfacheren Prompt.", state="error", expanded=True)
            
            except ResourceExhausted: # 👈 HIER WIRD DER FEHLER ABGEFANGEN
                status.update(label="Google API Limit erreicht", state="error", expanded=True)
                st.error("🚦 **Auslastungsgrenze erreicht:** Wir haben zu viele Anfragen in kurzer Zeit an Google gesendet. Bitte warte ca. 60 Sekunden und klicke dann erneut auf 'Modell generieren'.")
# --- AUSGABE ---
if st.session_state.modell_generiert:
    st.success("🎉 Modell bereit zum Download!")
    stl_from_file(file_path="output.stl", color=filament_farbe, auto_rotate=True)
    with open("output.stl", "rb") as f:
        st.download_button("⬇️ STL herunterladen", f, "Modell.stl", "application/sla")
    st.markdown("---")
    with st.expander("🔓 Open-Source-Konsole (Für Entwickler & Maker)", expanded=False):
        st.caption("Dieser Code wurde zu 100 % von Google Gemini generiert.")
        st.code(st.session_state.letzter_code, language="openscad")
        st.download_button(
            label="📄 OpenSCAD-Datei herunterladen",
            data=st.session_state.letzter_code,
            file_name="Modell_Code.scad",
            mime="text/plain",
            use_container_width=False
        )
