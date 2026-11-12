"""Build the README's data-free SVG diagrams with the Python standard library.

Run from any directory: python scripts/build_readme_assets.py
These are interface/architecture illustrations, not research observations.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets"
INK, MUTED, BLUE, TEAL = "#19384A", "#5B707C", "#0072B2", "#007F78"
RULE, PALE, AQUA, SAND = "#D8E2E8", "#F2F6F9", "#EEF8F5", "#FCF6EC"
FONT = "Arial, Helvetica, sans-serif"
MONO = "Menlo, Consolas, monospace"


class Figure:
    def __init__(self, name, number, title, subtitle, height=590):
        self.name = name
        self.root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", width="1200", height=str(height),
                               viewBox=f"0 0 1200 {height}", role="img", **{"aria-labelledby": "title desc"})
        ET.SubElement(self.root, "title", id="title").text = title
        ET.SubElement(self.root, "desc", id="desc").text = subtitle + " Documentation schematic; no research data."
        defs = ET.SubElement(self.root, "defs")
        marker = ET.SubElement(defs, "marker", id="arrow", markerWidth="8", markerHeight="8",
                               refX="7", refY="4", orient="auto", markerUnits="strokeWidth")
        ET.SubElement(marker, "path", d="M 0 0 L 8 4 L 0 8 Z", fill=BLUE)
        self.rect(0, 0, 1200, height, fill="#FFFFFF", stroke="none", radius=0)
        self.rect(.75, .75, 1198.5, height - 1.5, fill="none", radius=16)
        self.text(40, 43, f"{number:02d}  /  TUNA-DATA FIELD GUIDE", 15, BLUE, "bold")
        self.text(40, 91, title, 30, INK, "bold")
        self.text(40, 126, subtitle, 19, MUTED)

    def rect(self, x, y, w, h, fill="#FFFFFF", stroke=RULE, radius=12):
        return ET.SubElement(self.root, "rect", x=str(x), y=str(y), width=str(w), height=str(h),
                             rx=str(radius), fill=fill, stroke=stroke, **{"stroke-width": "1.3"})

    def text(self, x, y, value, size=20, colour=INK, weight="normal", mono=False, anchor="start"):
        t = ET.SubElement(self.root, "text", x=str(x), y=str(y), fill=colour,
                          **{"font-size": str(size), "font-family": MONO if mono else FONT,
                             "font-weight": weight, "text-anchor": anchor})
        t.text = value
        return t

    def line(self, x1, y1, x2, y2, arrow=False, colour=RULE, width=1.4):
        attrs = {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2), "stroke": colour,
                 "stroke-width": str(width)}
        if arrow:
            attrs["marker-end"] = "url(#arrow)"
        return ET.SubElement(self.root, "line", attrs)

    def pill(self, x, y, w, value, fill=PALE, colour=INK, size=18):
        self.rect(x, y, w, 36, fill=fill, stroke="none", radius=7)
        self.text(x + w / 2, y + 24, value, size, colour, anchor="middle")

    def card(self, x, y, w, h, title, tag=None):
        self.rect(x, y, w, h)
        self.text(x + 24, y + 39, title, 24, INK, "bold")
        self.line(x + 24, y + 60, x + w - 24, y + 60)
        if tag:
            self.text(x + 24, y + h - 24, tag, 16, MUTED)

    def save(self):
        OUT.mkdir(parents=True, exist_ok=True)
        ET.indent(self.root)
        ET.ElementTree(self.root).write(OUT / self.name, encoding="utf-8", xml_declaration=True)


def overview():
    f = Figure("01-library-overview.svg", 1, "One data library, reusable from R and Python",
               "GitHub stores the interface and this guide. Your selected storage holds the data.", 620)
    f.card(40, 180, 250, 250, "Sources")
    f.text(64, 271, "IATTC / WCPFC / IOTC", 18)
    f.text(64, 299, "ICCAT / CCSBT", 18)
    f.text(64, 351, "GFW API", 20, TEAL, "bold")
    f.text(64, 399, "Existing local archives", 18, MUTED)
    f.card(370, 180, 390, 250, "Private library")
    for y, left, right in [(269, "raw/", "Full source archives"), (309, "manifest.json", "Versions + hashes"),
                           (349, "processed/", "Annual + spatial"), (389, "figures/", "Optional outputs")]:
        f.text(394, y, left, 17, BLUE, mono=True)
        f.text(547, y, right, 17)
    f.card(840, 180, 320, 250, "R interface")
    for y, label in [(273, "tuna_read()"), (315, "tuna_processed()"), (357, "tuna_spatial()")]:
        f.text(864, y, label, 20, TEAL, mono=True)
    f.text(864, 403, "Python core via reticulate", 18, MUTED)
    f.line(299, 305, 359, 305, True, BLUE, 2)
    f.line(769, 305, 829, 305, True, BLUE, 2)
    f.line(565, 431, 565, 474, True, BLUE, 2)
    f.rect(370, 484, 790, 88, fill=PALE, stroke="none")
    f.text(394, 512, "Set TUNA_DATA_HOME to the library folder", 18, INK, "bold")
    for x, w, label in [(394, 202, "Local disk"), (614, 270, "Google Drive sync folder"), (902, 234, "Mounted NAS")]:
        f.pill(x, 525, w, label, fill="#FFFFFF", size=17)
    f.text(40, 599, "Original files and previous versions remain in the selected library.", 17, MUTED)
    f.save()


def layers():
    f = Figure("02-data-layers.svg", 2, "Choose the layer that matches your question",
               "Raw archives, annual summaries and spatial records are separate products.", 565)
    cards = [(40, "Source files", "tuna_read()", ["Original fields and units", "Explicit ZIP member / sheet"], ["raw/", "manifest.json"]),
             (435, "Annual catch", "tuna_processed()", ["Year × species × gear", "Statistical bases kept separate"], ["global_by_gear", "pacific_by_gear"]),
             (830, "Spatial layers", "tuna_spatial()", ["Year × grid × gear", "Filter before loading into R"], ["global_catch_cells", "gfw_cells"])]
    for x, title, api, lines, names in cards:
        f.card(x, 172, 330, 288, title)
        f.text(x + 24, 271, api, 20, TEAL, mono=True)
        for y, line in zip([314, 344], lines):
            f.text(x + 24, y, line, 18)
        f.rect(x + 18, 366, 294, 76, fill=PALE, stroke="none", radius=7)
        for y, line in zip([394, 423], names):
            f.text(x + 30, y, line, 17, BLUE, mono=True)
    for x, label, w in [(40, "Catch (tonnes)", 240), (300, "Fish (number)", 240),
                         (560, "Effort (source units)", 280), (860, "AIS activity (hours)", 300)]:
        f.pill(x, 488, w, label, fill=AQUA, colour=TEAL)
    f.text(40, 548, "Keep units, source products and catch_basis when combining records.", 17, MUTED)
    f.save()


def filtering():
    f = Figure("03-r-filtering.svg", 3, "Ask for the years, species and gears you need",
               "Selection and output schema only; no example research observations are shown.", 565)
    f.card(40, 175, 520, 325, "Your R request")
    code = ['tuna_spatial(', '  "pacific_catch_cells",', '  year = 2000:2025,', '  species = "YFT",',
            '  gear = "longline",', '  unit = "tonnes"', ')']
    for y, line in zip(range(275, 465, 28), code):
        f.text(65, y, line, 20, BLUE, mono=True)
    f.line(570, 335, 608, 335, True, BLUE, 2)
    f.card(620, 175, 540, 325, "A filtered R data frame")
    rows = [("year", "Selected years"), ("species_code", "YFT"), ("gear_group", "Longline"),
            ("source + catch_basis", "Kept separate"), ("map_lon + map_lat", "Cell location"),
            ("observed_value", "Known subtotal"), ("n_missing", "Missing components")]
    for i, (field, meaning) in enumerate(rows):
        y = 272 + i * 31
        f.text(644, y, field, 17, BLUE, mono=True)
        f.text(906, y, meaning, 18)
        if i < len(rows) - 1:
            f.line(644, y + 10, 1136, y + 10)
    f.text(40, 541, "Year partitions are selected before conversion to an R data frame.", 18, MUTED)
    f.save()


def updating():
    f = Figure("04-update-and-freeze.svg", 4, "Update sources, build results, then choose a run",
               "Downloads and derived tables have separate version histories.", 565)
    for x, title in [(40, "1  Update raw files"), (435, "2  Build results"), (830, "3  Read a run")]:
        f.card(x, 180, 330, 280, title)
    for y, text in [(278, "tuna-data update"), (314, "tuna-data gfw-sync")]:
        f.text(64, y, text, 19, BLUE, mono=True)
    f.pill(64, 350, 125, "Snapshot A", size=17)
    f.pill(201, 350, 145, "Snapshot B", fill=AQUA, colour=TEAL, size=17)
    f.text(64, 421, "Keep earlier source versions", 18, MUTED)
    f.text(459, 278, "tuna_build()", 19, BLUE, mono=True)
    f.text(459, 314, "tuna_spatial_build()", 19, BLUE, mono=True)
    f.pill(459, 350, 125, "Previous run", size=17)
    f.pill(596, 350, 150, "New run", fill=AQUA, colour=TEAL, size=17)
    f.text(459, 421, "Write tables and a version lock", 18, MUTED)
    f.text(854, 279, "Latest successful run", 20, TEAL, "bold")
    f.text(854, 309, "Default when run is omitted", 17, MUTED)
    f.line(854, 333, 1136, 333)
    f.text(854, 367, "Fixed run for an analysis", 20, INK, "bold")
    f.text(854, 405, 'run = "/.../Spatial_..."', 17, BLUE, mono=True)
    f.line(380, 316, 424, 316, True, BLUE, 2)
    f.line(775, 316, 819, 316, True, BLUE, 2)
    f.rect(40, 489, 1120, 49, fill=PALE, stroke="none", radius=8)
    f.text(64, 520, "Updating raw files does not automatically rebuild processed tables.", 20, INK, "bold")
    f.save()


def migration():
    f = Figure("05-drive-and-nas.svg", 5, "Move the complete library, then change its path",
               "Clone the code separately. Keep the library directory and all its subdirectories together.", 625)
    for x, title in [(40, "Existing data location"), (730, "New computer / NAS")]:
        f.card(x, 180, 430, 307, title)
        f.text(x + 25, 277, "library/", 23, BLUE, "bold", mono=True)
        for y, label in [(317, "manifest.json"), (352, "raw/"), (387, "processed/annual/"),
                          (422, "processed/spatial/"), (457, "figures/  (optional)")]:
            f.text(x + 48, y, label, 20, mono=True)
    f.text(600, 284, "Copy complete folder", 20, BLUE, "bold", anchor="middle")
    f.line(489, 320, 711, 320, True, BLUE, 2)
    f.text(600, 359, "Keep version files", 18, MUTED, anchor="middle")
    f.text(600, 387, "and year partitions", 18, MUTED, anchor="middle")
    f.rect(40, 515, 1120, 56, fill=AQUA, stroke="none", radius=8)
    f.text(64, 550, 'Sys.setenv(TUNA_DATA_HOME = "/mounted/path/library")', 21, TEAL, mono=True)
    f.text(40, 605, "mirror() and backup() cover the raw library; copy processed/ and figures/ separately.", 17, MUTED)
    f.save()


def plot_outputs():
    f = Figure("06-plot-outputs.svg", 6, "Run the plotting scripts and keep reusable outputs",
               "The cards show output types, not data. Actual research figures remain in your private folders.", 545)
    cards = [(40, "Annual trends", "r_plot_merged_yft.R", ["Global and Pacific views", "Gears and bases kept separate"]),
             (435, "Spatial catch", "r_spatial_workflow.R", ["Source-specific catch panels", "Year / species selection"]),
             (830, "AIS activity", "r_spatial_workflow.R", ["Separate RFMO reports", "Not species-specific"])]
    for x, title, script, lines in cards:
        f.card(x, 176, 330, 220, title)
        f.text(x + 24, 272, script, 17, BLUE, mono=True)
        for y, line in zip([321, 352], lines):
            f.text(x + 24, y, line, 18)
    for x, label, detail in [(40, "PNG", "600 dpi artwork + previews"), (325, "SVG", "Editable vector artwork"),
                             (610, "CSV", "Tables used for the plots"), (895, "JSON", "Sources, versions and checks")]:
        f.rect(x, 426, 265, 88, fill=PALE, stroke="none", radius=8)
        f.text(x + 20, 460, label, 23, TEAL, "bold")
        f.text(x + 20, 492, detail, 16)
    f.save()


if __name__ == "__main__":
    for build in [overview, layers, filtering, updating, migration, plot_outputs]:
        build()
    print(f"Built 6 documentation SVGs in {OUT}")
