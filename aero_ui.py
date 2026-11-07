"""
Roblox Auto-Bot - Oberfläche im Frutiger-Aero-Stil
==================================================
Steuert die beiden Bots aus den Ordnern "waschen" und "werten".
Start: Doppelklick auf "Start Bot.bat"  (oder: python aero_ui.py)
"""
import importlib.util
import json
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(ROOT, "einstellungen.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    """Einen Wert in einstellungen.json speichern, ohne die anderen Einstellungen zu verlieren."""
    data = load_settings()
    data[key] = value
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def fmt_retry(sec):
    return f"{sec:g} s".replace(".", ",")


def load_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Module zuerst laden (setzt DPI-Awareness, bevor das Fenster entsteht)
BOTS = {
    "waschen": load_module("waschbot", os.path.join("waschen", "waschbot.py")),
    "werten": load_module("wertbot", os.path.join("werten", "wertbot.py")),
}
SP = load_module("sprachen", "sprachen.py")     # Texte in 20 Sprachen + [Broken]-Wörter

DY = 68                  # zusätzliche Höhe der Steuerung (Auswahlleisten + Schalter)
W, H = 980, 720 + DY     # Grösse im Entwurf (bei 100 %); das Fenster selbst ist frei skalierbar
H_FOLDED = 550 + DY      # Höhe im Entwurf, wenn das Protokoll eingeklappt ist
FONT = "Segoe UI"

THEME = "light"          # "light" = Tag, "dark" = Nacht; wird beim Start aus den Einstellungen gesetzt
THEMES = {
    "light": {
        "canvas_bg": "#5aa9e6",
        "silver": ((250, 252, 255), (178, 196, 214), (120, 140, 165)),
        "track": ((190, 208, 226, 255), (245, 250, 255, 255)), "track_edge": (120, 150, 185, 255),
        "log_bg": "#f4fbff", "log_fg": "#0d3b66", "log_border": "#9cc4e8",
        "tags": {"click": "#16801a", "warn": "#c25e00", "err": "#c0161b", "status": "#0a5cc2"},
        "fill": {}, "shadow": {},
    },
    "dark": {
        "canvas_bg": "#0a1a3a",
        "silver": ((126, 148, 188), (64, 82, 124), (34, 46, 80)),
        "track": ((16, 28, 56, 255), (46, 64, 106, 255)), "track_edge": (90, 125, 180, 255),
        "log_bg": "#0c1830", "log_fg": "#d6e6ff", "log_border": "#2c4a7c",
        "tags": {"click": "#7de882", "warn": "#ffb35c", "err": "#ff8585", "status": "#7ab9ff"},
        # helle Farben für Texte auf dunklem Glas (Schlüssel = Farbe im hellen Design)
        "fill": {"#0d3b66": "#e4f0ff", "#2a5a85": "#a9c9ee", "#0a5cc2": "#74b8ff", "#16801a": "#7de882",
                 "#8aa0b5": "#6f89aa", "#c25e00": "#ffb35c", "#e8f6ff": "#bcd6f6", "#123a5c": "#eaf3ff"},
        "shadow": {"#ffffff": "#030c24", "#0a4a8a": "#000a22", "#0b3a66": "#000a22"},
    },
}


# ================================================================ Grafik (PIL)
def grad(w, h, c1, c2, horizontal=False):
    """Linearer Farbverlauf als RGBA-Bild."""
    n = w if horizontal else h
    t = np.linspace(0, 1, max(n, 1))[:, None]
    line = np.array(c1, float) + (np.array(c2, float) - np.array(c1, float)) * t
    arr = np.broadcast_to(line[None, :, :] if horizontal else line[:, None, :], (h, w, 4))
    return Image.fromarray(arr.astype(np.uint8))


def rr_mask(w, h, r, ss=3):
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], r * ss, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def masked(img, mask):
    out = img.copy()
    a = np.array(out.getchannel("A"), float) * (np.array(mask, float) / 255)
    out.putalpha(Image.fromarray(a.astype(np.uint8)))
    return out


def outline(w, h, r, color, width, ss=3):
    o = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(o).rounded_rectangle([width * ss // 2, width * ss // 2, w * ss - 1 - width * ss // 2,
                                         h * ss - 1 - width * ss // 2], r * ss, outline=color, width=width * ss)
    return o.resize((w, h), Image.LANCZOS)


def glass(w, h, r, strength=1.0):
    """Glas-Panel mit Glanzkante und weichem Schatten (hell = Tag, dunkel = Nacht). Gibt (bild, rand) zurück."""
    dark = THEME == "dark"
    a = lambda v: min(255, int(v * strength))
    pad = 14
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 0, 24, a(150)) if dark else (10, 50, 100, a(80)), (pad, pad + 5, pad + w, pad + h + 5), rr_mask(w, h, r))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7)))
    c1, c2 = ((20, 38, 82, a(190)), (8, 18, 48, a(170))) if dark else ((255, 255, 255, a(120)), (215, 238, 255, a(80)))
    body = masked(grad(w, h, c1, c2), rr_mask(w, h, r))
    img.alpha_composite(body, (pad, pad))
    gh = max(int(h * 0.42), 1)
    gloss_mask = Image.new("L", (w, h), 0)
    gloss_mask.paste(rr_mask(w, h, r).crop((0, 0, w, gh)), (0, 0))
    gloss = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gloss.paste(grad(w, gh, (255, 255, 255, a(50 if dark else 140)), (255, 255, 255, 4 if dark else 10)), (0, 0))
    img.alpha_composite(masked(gloss, gloss_mask), (pad, pad))
    img.alpha_composite(outline(w, h, r, (150, 190, 255, 170) if dark else (255, 255, 255, 220), 2), (pad, pad))
    img.alpha_composite(outline(w - 4, h - 4, max(r - 2, 1), (70, 110, 180, 90) if dark else (120, 170, 220, 70), 1),
                        (pad + 2, pad + 2))
    return img, pad


def pill(w, h, top, bottom, rim, hover=False, pressed=False):
    """Glänzender Aqua-Knopf (Vista/Aero)."""
    pad = 8
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    r = h // 2
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.paste((0, 40, 90, 90), (pad, pad + 3, pad + w, pad + h + 3), rr_mask(w, h, r))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4)))
    k = 0.82 if pressed else (1.12 if hover else 1.0)
    lt = tuple(min(255, int(c * k)) for c in top) + (255,)
    lb = tuple(min(255, int(c * k)) for c in bottom) + (255,)
    img.alpha_composite(masked(grad(w, h, lt, lb), rr_mask(w, h, r)), (pad, pad))
    # helles Leuchten unten (typisch Aero)
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w * 0.1, h * 0.55, w * 0.9, h * 1.5], fill=(255, 255, 255, 90))
    img.alpha_composite(masked(glow.filter(ImageFilter.GaussianBlur(h / 5)), rr_mask(w, h, r)), (pad, pad))
    # Glanz oben
    gw, gh = w - 8, int(h * 0.48)
    img.alpha_composite(masked(grad(gw, gh, (255, 255, 255, 200), (255, 255, 255, 40)), rr_mask(gw, gh, gh // 2)),
                        (pad + 4, pad + 2))
    img.alpha_composite(outline(w, h, r, rim + (255,), 1), (pad, pad))
    return img, pad


def seg_images(w, h, n, silver, aqua):
    """Auswahlleiste aus EINEM Körper (Pillenform) mit n gleich breiten Feldern.
    Gibt (Grundbild, Rand, [ {none, hover, sel, selhover} je Feld ]) zurück."""
    pad, r, ins = 6, h // 2, 2
    top, bottom, rim = silver
    base = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh.paste((0, 40, 90, 70), (pad, pad + 2, pad + w, pad + h + 2), rr_mask(w, h, r))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(3)))
    body = masked(grad(w, h, top + (255,), bottom + (255,)), rr_mask(w, h, r))
    gh = max(h // 2, 2)
    body.alpha_composite(masked(grad(w - 6, gh, (255, 255, 255, 140), (255, 255, 255, 12)),
                                rr_mask(w - 6, gh, gh // 2)), (3, 1))
    d = ImageDraw.Draw(body)
    for k in range(1, n):
        x = int(round(k * w / n))
        d.line([(x, h * 0.22), (x, h * 0.78)], fill=rim + (120,), width=1)
    body.alpha_composite(outline(w, h, r, rim + (255,), 1))
    base.alpha_composite(body, (pad, pad))

    inner_w, inner_h = w - 2 * ins, h - 2 * ins
    inner_mask = rr_mask(inner_w, inner_h, r - ins)

    def piece(c_top, c_bot, alpha_scale, gloss):
        img = masked(grad(inner_w, inner_h, c_top + (int(255 * alpha_scale),), c_bot + (int(255 * alpha_scale),)), inner_mask)
        if gloss:
            g = masked(grad(inner_w - 6, inner_h // 2, (255, 255, 255, 170), (255, 255, 255, 30)),
                       rr_mask(inner_w - 6, inner_h // 2, inner_h // 4))
            img.alpha_composite(g, (3, 1))
        full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        full.alpha_composite(img, (ins, ins))
        return full

    sel = piece(aqua[0], aqua[1], 1.0, True)
    sel_o = ImageDraw.Draw(sel)
    selhover = piece(tuple(min(255, int(c * 1.12)) for c in aqua[0]), tuple(min(255, int(c * 1.12)) for c in aqua[1]), 1.0, True)
    hover = piece((255, 255, 255), (255, 255, 255), 0.38, False)
    segs = []
    for i in range(n):
        x0, x1 = int(round(i * w / n)), int(round((i + 1) * w / n))
        crop = lambda im: im.crop((x0, 0, x1, h))
        none = Image.new("RGBA", (x1 - x0, h), (0, 0, 0, 0))
        segs.append({"none": none, "hover": crop(hover), "sel": crop(sel), "selhover": crop(selhover), "x0": x0, "x1": x1})
    return base, pad, segs


def orb(d, color, ss=4):
    """Glänzende Kugel (Status-Licht / Häkchen)."""
    D = d * ss
    img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:D, 0:D]
    cx, cy = D * 0.5, D * 0.62
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (D * 0.6)
    base = np.array(color, float)
    light = base + (255 - base) * 0.55
    t = np.clip(dist, 0, 1)[..., None]
    rgb = light * (1 - t) + base * 0.65 * t
    a = (np.sqrt((xx - D / 2) ** 2 + (yy - D / 2) ** 2) <= D / 2 - ss).astype(float) * 255
    img = Image.fromarray(np.dstack([rgb, a]).astype(np.uint8))
    g = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([D * 0.18, D * 0.06, D * 0.82, D * 0.5], fill=(255, 255, 255, 170))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(D / 25)))
    o = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    ImageDraw.Draw(o).ellipse([ss, ss, D - ss, D - ss], outline=tuple(int(c * 0.6) for c in color) + (255,),
                              width=ss)
    img.alpha_composite(o)
    return img.resize((d, d), Image.LANCZOS)


def bubble(d, ss=4):
    D = d * ss
    img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:D, 0:D]
    dist = np.sqrt((xx - D / 2) ** 2 + (yy - D / 2) ** 2) / (D / 2)
    inside = dist <= 0.97
    a = np.where(inside, 25 + 150 * np.clip(dist, 0, 1) ** 4, 0)
    rgb = np.dstack([np.full_like(dist, 225), np.full_like(dist, 245), np.full_like(dist, 255)])
    img = Image.fromarray(np.dstack([rgb, a]).astype(np.uint8))
    g = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    dr = ImageDraw.Draw(g)
    dr.ellipse([D * 0.2, D * 0.14, D * 0.48, D * 0.36], fill=(255, 255, 255, 230))
    dr.ellipse([D * 0.6, D * 0.7, D * 0.74, D * 0.8], fill=(255, 255, 255, 120))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(D / 30)))
    return img.resize((d, d), Image.LANCZOS)


def background(w, h, S):
    dark = THEME == "dark"
    # EIN durchgehender Verlauf mit mehreren Stützfarben (kein Sprung, leichtes Rauschen gegen Streifen)
    stops = ([(0.0, (6, 14, 44)), (0.35, (14, 34, 82)), (0.7, (32, 70, 130)), (1.0, (62, 108, 170))] if dark else
             [(0.0, (34, 124, 214)), (0.35, (78, 162, 232)), (0.7, (142, 205, 248)), (1.0, (214, 240, 255))])
    ys = np.linspace(0, 1, max(h, 2))
    cols = np.stack([np.interp(ys, [p for p, _ in stops], [c[k] for _, c in stops]) for k in range(3)], axis=1)
    cols = cols * (1 - 0.0) + 0.0
    arr = np.broadcast_to(cols[:, None, :], (h, w, 3)).astype(float)
    arr = arr + np.random.default_rng(3).uniform(-1.2, 1.2, (h, w, 1))
    img = Image.fromarray(np.dstack([np.clip(arr, 0, 255), np.full((h, w), 255.0)]).astype(np.uint8))
    # Sonnen- bzw. Mond-Glow oben links
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-w * 0.25, -h * 0.45, w * 0.55, h * 0.4],
                                 fill=(170, 200, 255, 70) if dark else (255, 255, 255, 110))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(80 * S)))
    if dark:
        stars = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(stars)
        rnd = random.Random(11)
        for _ in range(90):
            x, y = rnd.uniform(0, w), rnd.uniform(0, h * 0.5)
            r = rnd.uniform(0.6, 1.8) * S
            sd.ellipse([x - r, y - r, x + r, y + r], fill=(235, 242, 255, rnd.randint(110, 235)))
        img.alpha_composite(stars.filter(ImageFilter.GaussianBlur(0.6)))
    # Aero-Schwünge
    sw = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(sw)
    alphas = [(0.0, 26), (0.08, 18), (0.16, 12)] if dark else [(0.0, 70), (0.08, 50), (0.16, 35)]
    for i, (a, al) in enumerate(alphas):
        d.ellipse([-w * 0.3, h * (0.28 + a), w * 1.4, h * (1.25 + a)], outline=(255, 255, 255, al),
                  width=int((22 - i * 6) * S))
    img.alpha_composite(sw.filter(ImageFilter.GaussianBlur(6 * S)))
    # Hügel
    hills = ([(-0.3, 0.80, 0.6, 1.6, (70, 140, 80), (14, 64, 36)), (0.35, 0.84, 1.35, 1.7, (58, 126, 68), (10, 52, 30))]
             if dark else
             [(-0.3, 0.80, 0.6, 1.6, (170, 235, 90), (40, 140, 30)), (0.35, 0.84, 1.35, 1.7, (150, 225, 80), (30, 120, 30))])
    for (x0, y0, x1, y1, c1, c2) in hills:
        box = [int(w * x0), int(h * y0), int(w * x1), int(h * y1)]
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).ellipse(box, fill=255)
        hill = grad(w, h, (0, 0, 0, 0), (0, 0, 0, 0))
        hill.paste(grad(w, h - box[1], c1 + (255,), c2 + (255,)), (0, box[1]))
        img.alpha_composite(masked(hill, m))
        hl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(hl).ellipse([box[0] + 40 * S, box[1] + 6 * S, box[2] - 40 * S, box[1] + 60 * S],
                                   fill=(255, 255, 255, 40 if dark else 90))
        img.alpha_composite(masked(hl.filter(ImageFilter.GaussianBlur(10 * S)), m))
    return img


# ================================================================ Steuerung
class Controller:
    def __init__(self):
        self.q = queue.Queue()
        self.mode = "waschen"
        self.thread = None
        self.bot = None
        self.cancel = threading.Event()
        self.state = "stop"        # stop | run | calib
        self.started = None
        self.anti_afk = True       # alle 3 Min. 3x A + 3x D
        self.background = False    # experimentell: Klicks direkt an Roblox schicken (klappt nicht in jedem Spiel)
        self.monitor = None        # (links, oben, rechts, unten) des Monitors mit Roblox - muss gewählt werden
        self.notify_end = True     # Windows-Benachrichtigung, wenn keine Items mehr da sind und der Bot stoppt
        self.notify_item = False   # Windows-Benachrichtigung bei jedem fertigen Item (muss man selbst einschalten)
        self.t = lambda key, **kw: SP.tr("de", key, **kw)   # wird von der Oberfläche auf die gewählte Sprache gesetzt

    @property
    def mod(self):
        return BOTS[self.mode]

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def say(self, text):
        self.q.put(("calib", text))
        self.q.put(("log", (time.strftime("[%d.%m. %H:%M:%S] ") + text, "warn" if text.lstrip().startswith("!!") else "")))

    def _worker(self, test, force_calib):
        m = self.mod
        for mod in BOTS.values():
            mod.MONITOR = self.monitor
        try:
            cal = None if force_calib else m.load_calibration()
            if cal is not None and not m.calibration_fits(cal):
                self.q.put(("log", (time.strftime("[%d.%m. %H:%M:%S] ") + self.t("m_other_monitor"), "warn")))
                cal = None
            if cal is None:
                self.q.put(("state", "calib"))
                cal = m.calibrate(say=self.say, should_stop=self.cancel.is_set, wait_after=5)
                self.q.put(("calib", None))
                if force_calib:
                    return
            self.bot = m.Bot(cal, test=test, on_log=lambda l, k="": self.q.put(("log", (l, k))),
                             on_slots=lambda s: self.q.put(("slots", s)), anti_afk=self.anti_afk,
                             background=self.background, notify_end=self.notify_end, notify_item=self.notify_item,
                             on_notify=self.on_notify)
            self.q.put(("state", "run"))
            self.bot.run()
        except m.StopBot:
            pass
        except Exception:
            self.q.put(("log", (time.strftime("[%d.%m. %H:%M:%S] ") + self.t("b_error") + "\n" + traceback.format_exc(), "err")))
        finally:
            self.bot = None
            self.q.put(("calib", None))
            self.q.put(("state", "stop"))

    def on_notify(self, kind, info):
        """Titel + Text der Windows-Benachrichtigung in der gewählten Sprache."""
        shop = self.t("shop_wash" if self.mode == "waschen" else "shop_grade")
        if kind == "end":
            return "Roblox Auto-Bot", self.t("toast_end", shop=shop, n=info.get("done", 0))
        slot = info.get("slot")
        if slot:
            return "Roblox Auto-Bot", self.t("toast_item", shop=shop, slot=self.t("slot", n=slot))
        return "Roblox Auto-Bot", self.t("toast_item_any", shop=shop)

    def start(self, test, force_calib=False):
        if self.busy():
            return False
        self.cancel.clear()
        self.thread = threading.Thread(target=self._worker, args=(test, force_calib), daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.cancel.set()
        if self.bot:
            self.bot.stop_requested = True

    def toggle_pause(self):
        if self.bot:
            self.bot.paused = not self.bot.paused
            self.bot.log(self.t("b_pause") if self.bot.paused else self.t("b_resume"), "warn")


# ================================================================ Oberfläche
class AeroUI:
    def __init__(self, root):
        self.root = root
        self.ctl = Controller()
        self.dpi = max(1.0, root.winfo_fpixels("1i") / 96.0)   # Windows-Skalierung (125 % -> 1.25)
        self.zoom = 1.0                                        # zusätzliche Vergrösserung durch die Fenstergrösse
        self.S = self.dpi
        self.ox = self.oy = 0                                  # Rand links/oben, wenn das Fenster breiter/höher ist
        settings = load_settings()
        self.log_open = settings.get("log_open", True)         # Protokoll aufgeklappt?
        self.log = None
        self.log_frame = None
        global THEME
        self.theme = settings.get("theme", "light")            # Tag- oder Nacht-Design
        if self.theme not in THEMES:
            self.theme = "light"
        THEME = self.theme
        self.th = THEMES[self.theme]
        self.ctl.notify_end = settings.get("notify_end", True)
        self.ctl.notify_item = settings.get("notify_item", False)
        self.log_lines = []                                    # alle Protokollzeilen (bleiben beim Neuzeichnen erhalten)
        self.banner_text = None
        self.slots_info_text = None
        self._resize_job = None
        self.test_mode = False
        self.slot_data = None
        self.lang = settings.get("lang", "de")
        if self.lang not in dict(SP.LANGS):
            self.lang = "de"
        self.i18n = []                       # (Text-IDs, Funktion die den Text liefert) - für den Sprachwechsel
        for _m in BOTS.values():
            _m.LANGUAGE = self.lang
        retry = settings.get("retry", 1.0)
        retry = retry if retry in (0.1, 0.5, 1.0, 2.0, 3.0) else 1.0
        for mod in BOTS.values():
            mod.RETRY_INTERVAL = retry
            mod.INTERVAL = min(1.0, retry)           # 0,1-s-Modus: auch die Prüf-Schleife läuft im 0,1-s-Takt
        self.ctl.t = self.t
        self.last_slots_time = None

        root.title("Roblox Auto-Bot")
        root.resizable(True, True)
        root.minsize(int(W * self.dpi * 0.5), int(H_FOLDED * self.dpi * 0.5))
        w0, h0 = int(W * self.dpi), int(self.design_h() * self.dpi)
        root.geometry(f"{w0}x{h0}")
        self.cv = tk.Canvas(root, width=w0, height=h0, highlightthickness=0, bd=0, bg=self.th["canvas_bg"])
        self.cv.pack(fill="both", expand=True)
        self.icon = ImageTk.PhotoImage(orb(64, (40, 170, 240)))
        root.iconphoto(True, self.icon)

        self.build(w0, h0)
        self.restore_monitor()

        root.bind("<Configure>", self.on_resize)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.animate()
        self.tick()
        self.poll()

    # ------------------------------------------------------------ Grösse / Neuaufbau
    def design_h(self):
        return H if self.log_open else H_FOLDED

    def build(self, cw, ch):
        """Alles neu zeichnen, passend zur Fenstergrösse cw x ch (Zustand bleibt erhalten)."""
        hd = self.design_h()
        self.zoom = max(0.45, min(cw / (W * self.dpi), ch / (hd * self.dpi)))
        self.S = self.dpi * self.zoom
        self.w, self.h = cw, ch
        self.ox = max(0, int((cw - W * self.S) / 2))      # Inhalt mittig, Rest ist Himmel
        self.oy = max(0, int((ch - hd * self.S) / 2))
        if self.log_frame is not None:
            self.log_frame.destroy()
            self.log_frame = None
        self.cv.delete("all")
        self.cv.config(width=cw, height=ch, bg=self.th["canvas_bg"])
        self.imgs, self.i18n, self.log = [], [], None
        self.build_background()
        self.build_bubbles()
        self.build_header()
        self.build_controls()
        before = set(self.cv.find_all())
        self.build_slots()
        self.build_log()
        for i in set(self.cv.find_all()) - before:          # alles unter der Steuerung rutscht um die neue Zeile nach unten
            self.cv.move(i, 0, self.s(DY))
        # alles ausser Hintergrund und Blasen an die richtige Stelle schieben
        self.cv.addtag_all("content")
        self.cv.dtag(self.bg_item, "content")
        for b in self.bubbles:
            self.cv.dtag(b["item"], "content")
        self.cv.move("content", self.ox, self.oy)
        for b in self.bubbles:
            self.cv.tag_raise(b["item"], self.bg_item)
        self.restore_state()

    def restore_state(self):
        """Nach dem Neuzeichnen: Auswahl, Schalter, Karten, Hinweise wieder so zeigen wie vorher."""
        self.paint_mode()
        for mon in self.monitors:
            if self.ctl.monitor and tuple(mon["rect"]) == tuple(self.ctl.monitor):
                self.paint_monitor(mon)
        self.paint_retry()
        self.paint_seg(self.theme_seg, self.theme)
        self.cv.itemconfig(self.afk_orb, image=self.orbs["green" if self.ctl.anti_afk else "grey"])
        self.cv.itemconfig(self.bg_orb, image=self.orbs["green" if self.ctl.background else "grey"])
        self.cv.itemconfig(self.ne_orb, image=self.orbs["green" if self.ctl.notify_end else "grey"])
        self.cv.itemconfig(self.ni_orb, image=self.orbs["green" if self.ctl.notify_item else "grey"])
        self.show_banner(self.banner_text)
        self.set_text(self.slots_info, self.slots_info_text or self.t("live"))
        self.update_slots(self.slot_data)
        self.set_status()

    def on_resize(self, event):
        if event.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(200, self._do_resize)    # erst neu zeichnen, wenn das Ziehen aufhört

    def _do_resize(self):
        self._resize_job = None
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        if cw > 50 and ch > 50 and (abs(cw - self.w) > 3 or abs(ch - self.h) > 3):
            self.build(cw, ch)

    def toggle_log(self):
        """Protokoll ein-/ausklappen. Das Fenster wird dabei kürzer/länger, die Grösse der Inhalte bleibt gleich."""
        old_hd = self.design_h()
        self.log_open = not self.log_open
        save_setting("log_open", self.log_open)
        ch = max(int(H_FOLDED * self.dpi * 0.5), self.h + round((self.design_h() - old_hd) * self.S))
        if self.root.state() == "normal":
            self.root.geometry(f"{self.w}x{ch}")
            self.root.update_idletasks()
        self.build(self.cv.winfo_width(), self.cv.winfo_height())

    # ------------------------------------------------------------ Helfer
    def t(self, key, **kw):
        return SP.tr(self.lang, key, **kw)

    def ltext(self, x, y, key, *args, fn=None, **kw):
        """Text, der beim Sprachwechsel automatisch übersetzt wird."""
        fn = fn or (lambda: self.t(key))
        ids = self.text(x, y, fn(), *args, **kw)
        self.i18n.append((ids, fn))
        return ids

    def lbutton(self, x, y, w, h, key, *args, fn=None, **kw):
        """Knopf, dessen Beschriftung beim Sprachwechsel automatisch übersetzt wird."""
        fn = fn or (lambda: self.t(key))
        b = self.button(x, y, w, h, fn(), *args, **kw)
        self.i18n.append((b["text"], fn))
        return b

    def s(self, v):
        return int(round(v * self.S))

    def photo(self, pil):
        p = ImageTk.PhotoImage(pil)
        self.imgs.append(p)
        return p

    def fs(self, size):
        """Schriftgrösse passend zur Fenstergrösse."""
        return max(6, int(round(size * self.zoom)))

    def col(self, c):
        """Textfarbe im aktuellen Design (im Nacht-Design werden dunkle Texte hell)."""
        return self.th["fill"].get(c, c)

    def col_sh(self, c):
        """Schattenfarbe im aktuellen Design."""
        return self.th["shadow"].get(c, c)

    def text(self, x, y, txt, size=11, weight="normal", fill="#0d3b66", shadow=None, anchor="nw", family=FONT):
        font = (family, self.fs(size), weight)
        sid = None
        if shadow:
            sid = self.cv.create_text(self.s(x) + 1, self.s(y) + 1, text=txt, font=font, fill=self.col_sh(shadow),
                                      anchor=anchor)
        tid = self.cv.create_text(self.s(x), self.s(y), text=txt, font=font, fill=self.col(fill), anchor=anchor)
        return (tid, sid)

    def set_text(self, ids, txt, fill=None):
        for i in ids:
            if i:
                self.cv.itemconfig(i, text=txt)
        if fill:
            self.cv.itemconfig(ids[0], fill=self.col(fill))

    def button(self, x, y, w, h, label, colors, cmd, dark_text=False, size=12):
        top, bottom, rim = colors
        W_, H_ = self.s(w), self.s(h)
        states = {k: pill(W_, H_, top, bottom, rim, hover=(k == "hover"), pressed=(k == "down"))
                  for k in ("normal", "hover", "down")}
        pad = states["normal"][1]
        photos = {k: self.photo(v[0]) for k, v in states.items()}
        tag = f"btn{len(self.imgs)}"
        img = self.cv.create_image(self.s(x) - pad, self.s(y) - pad, image=photos["normal"], anchor="nw", tags=tag)
        fg, sh = ("#123a5c", "#ffffff") if dark_text else ("#ffffff", "#0b3a66")
        t = self.text(x + w / 2, y + h / 2, label, size, "bold", fg, sh, "center")
        for i in t:
            if i:
                self.cv.addtag_withtag(tag, i)
        btn = {"img": img, "photos": photos, "text": t, "enabled": True, "label": label}
        self.cv.tag_bind(tag, "<Enter>", lambda e: btn["enabled"] and self.cv.itemconfig(img, image=photos["hover"]))
        self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.itemconfig(img, image=photos["normal"]))
        self.cv.tag_bind(tag, "<ButtonPress-1>",
                         lambda e: btn["enabled"] and self.cv.itemconfig(img, image=photos["down"]))

        def release(e):
            self.cv.itemconfig(img, image=photos["hover"])
            if btn["enabled"]:
                cmd()
        self.cv.tag_bind(tag, "<ButtonRelease-1>", release)
        self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"), add="+")
        self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""), add="+")
        return btn

    def recolor(self, btn, colors):
        top, bottom, rim = colors
        W_, H_ = btn["photos"]["normal"].width() - 16, btn["photos"]["normal"].height() - 16
        for k in ("normal", "hover", "down"):
            btn["photos"][k] = self.photo(pill(W_, H_, top, bottom, rim, k == "hover", k == "down")[0])
        self.cv.itemconfig(btn["img"], image=btn["photos"]["normal"])

    # ------------------------------------------------------------ Aufbau
    def panel_on(self, bg, x, y, w, h, r=18, strength=1.0):
        g, pad = glass(self.s(w), self.s(h), self.s(r), strength)
        bg.alpha_composite(g, (self.ox + self.s(x) - pad, self.oy + self.s(y) - pad))

    def build_background(self):
        bg = background(self.w, self.h, self.S)
        self.panel_on(bg, 20, 16, 940, 92)
        self.panel_on(bg, 20, 122, 940, 118 + DY)
        self.panel_on(bg, 20, 254 + DY, 940, 222)
        for k in range(3):
            self.panel_on(bg, 38 + k * 305, 292 + DY, 290, 168, r=14, strength=1.25)
        self.panel_on(bg, 20, 490 + DY, 940, 214 if self.log_open else 44)
        self.bg_photo = self.photo(bg.convert("RGB"))
        self.bg_item = self.cv.create_image(0, 0, image=self.bg_photo, anchor="nw")

    def build_bubbles(self):
        self.bubbles = []
        rnd = random.Random(7)
        for _ in range(16):
            d = rnd.randint(self.s(10), self.s(46))
            p = self.photo(bubble(d))
            x = rnd.uniform(0, self.w)
            y = rnd.uniform(0, self.h)
            item = self.cv.create_image(x, y, image=p)
            self.bubbles.append({"item": item, "x0": x, "y": y, "d": d, "v": rnd.uniform(0.25, 0.9) * self.S,
                                 "amp": rnd.uniform(6, 22) * self.S, "f": rnd.uniform(0.4, 1.2),
                                 "ph": rnd.uniform(0, 6.3)})

    def build_header(self):
        self.text(44, 30, "Roblox Auto-Bot", 24, "bold", "#ffffff", "#0a4a8a")
        self.ltext(46, 72, "hotkeys", 11, "normal", "#e8f6ff", "#0a4a8a")
        self.clock = self.text(935, 26, "", 24, "normal", "#ffffff", "#0a4a8a", "ne", "Segoe UI Light")
        self.date = self.text(935, 70, "", 10, "normal", "#e8f6ff", "#0a4a8a", "ne")
        self.orbs = {c: self.photo(orb(self.s(18), rgb)) for c, rgb in
                     {"green": (60, 200, 60), "orange": (255, 160, 30), "grey": (160, 170, 180),
                      "blue": (40, 150, 255)}.items()}
        self.status_orb = self.cv.create_image(self.s(560), self.s(56), image=self.orbs["grey"])
        self.status_txt = self.text(576, 56, self.t("s_stop"), 13, "bold", "#ffffff", "#0a4a8a", "w")

    def build_controls(self):
        self.ltext(44, 138, "mode", 10, "bold", "#0d3b66")
        silver = self.th["silver"]
        aqua = ((90, 205, 255), (0, 112, 200), (0, 70, 140))
        self.c_silver, self.c_aqua = silver, aqua
        self.mode_btns = {
            "waschen": self.lbutton(44, 158, 170, 40, "wash", aqua, lambda: self.set_mode("waschen")),
            "werten": self.lbutton(226, 158, 170, 40, "grade", silver, lambda: self.set_mode("werten"), True),
        }
        self.ltext(420, 138, "controls", 10, "bold", "#0d3b66")
        self.btn_start = self.lbutton(420, 158, 120, 40, "start",
                                     ((130, 225, 70), (25, 140, 20), (20, 100, 15)), self.on_start)
        self.btn_pause = self.lbutton(552, 158, 120, 40, "pause",
                                     ((100, 190, 255), (10, 90, 200), (5, 60, 140)), self.on_pause)
        self.btn_stop = self.lbutton(684, 158, 120, 40, "stop",
                                    ((255, 150, 100), (205, 45, 20), (150, 30, 10)), self.on_stop)
        self.btn_cal = self.lbutton(816, 158, 128, 40, "calibrate", silver, self.on_calibrate, True, 11)
        # Zeile 2: Monitor (Pflicht) und Takt des Nachklickens als Auswahlleisten
        self.monitors = BOTS["waschen"].list_monitors()
        self.ltext(44, 228, "monitor", 10, "bold", "#0d3b66", anchor="w", fn=lambda: self.t("monitor") + ":")
        n = max(1, len(self.monitors))
        seg_w = n * min(86, 192 / n)
        self.mon_seg = self.segmented(124, 214, seg_w, 28,
                                      [(mon["nr"], (lambda nr=mon["nr"]: f"{self.t('monitor')} {nr}")) for mon in self.monitors],
                                      lambda nr: self.set_monitor(next(m for m in self.monitors if m["nr"] == nr)))
        self.ltext(470, 228, "retry", 9, "bold", "#0d3b66", anchor="e")
        self.retry_seg = self.segmented(480, 214, 5 * 52, 28,
                                        [(sec, (lambda v=sec: fmt_retry(v))) for sec in (0.1, 0.5, 1.0, 2.0, 3.0)],
                                        self.set_retry)
        # Zeile 3 + 4: Schalter in festem Raster (zwei Spalten), Hell/Dunkel rechts
        self.afk_orb, self.afk_txt = self.toggle(56, 262, "afk", self.ctl.anti_afk, self.toggle_afk)
        self.bg_orb, self.bg_txt = self.toggle(380, 262, "background", self.ctl.background, self.toggle_bg)
        self.ne_orb, self.ne_txt = self.toggle(56, 290, "n_end", self.ctl.notify_end, self.toggle_notify_end)
        self.ni_orb, self.ni_txt = self.toggle(380, 290, "n_item", self.ctl.notify_item, self.toggle_notify_item)
        self.theme_seg = self.segmented(776, 248, 168, 28,
                                        [("light", lambda: "\u2600  " + self.t("light")),
                                         ("dark", lambda: "\u263e  " + self.t("dark"))],
                                        self.set_theme)

    def toggle(self, x, y, key, on, cmd):
        """Schalter mit Kugel + Beschriftung (beides klickbar)."""
        self._uid = getattr(self, "_uid", 0) + 1
        tag = f"tg{self._uid}"
        orb_item = self.cv.create_image(self.s(x), self.s(y), image=self.orbs["green" if on else "grey"], tags=tag)
        txt = self.ltext(x + 16, y, key, 10, "normal", "#0d3b66", anchor="w")
        for i in txt:
            if i:
                self.cv.addtag_withtag(tag, i)
        self.cv.tag_bind(tag, "<Button-1>", lambda e: cmd())
        self.cv.tag_bind(tag, "<Enter>", lambda e: self.cv.config(cursor="hand2"))
        self.cv.tag_bind(tag, "<Leave>", lambda e: self.cv.config(cursor=""))
        return orb_item, txt

    def segmented(self, x, y, w, h, items, cmd, size=9):
        """Auswahlleiste: items = [(wert, beschriftung_fn)]. Genau ein Feld ist gewählt (paint_seg)."""
        n = max(1, len(items))
        W_, H_ = self.s(w), self.s(h)
        base, pad, segs = seg_images(W_, H_, n, self.th["silver"], ((90, 205, 255), (0, 112, 200), (0, 70, 140)))
        self.cv.create_image(self.s(x) - pad, self.s(y) - pad, image=self.photo(base), anchor="nw")
        seg = {"value": None, "items": []}
        for (val, fn), g in zip(items, segs):
            photos = {k: self.photo(g[k]) for k in ("none", "hover", "sel", "selhover")}
            self._uid = getattr(self, "_uid", 0) + 1
            tag = f"sg{self._uid}"
            img = self.cv.create_image(self.s(x) + g["x0"], self.s(y), image=photos["none"], anchor="nw", tags=tag)
            cx = x + ((g["x0"] + g["x1"]) / 2) / self.S
            t = self.text(cx, y + h / 2, fn(), size, "bold", "#123a5c", "#ffffff", "center")
            for i in t:
                if i:
                    self.cv.addtag_withtag(tag, i)
            self.i18n.append((t, fn))
            it = {"val": val, "img": img, "photos": photos, "text": t, "hover": False}
            seg["items"].append(it)

            def enter(e, it=it):
                it["hover"] = True
                self.paint_seg(seg)
                self.cv.config(cursor="hand2")

            def leave(e, it=it):
                it["hover"] = False
                self.paint_seg(seg)
                self.cv.config(cursor="")
            self.cv.tag_bind(tag, "<Enter>", enter)
            self.cv.tag_bind(tag, "<Leave>", leave)
            self.cv.tag_bind(tag, "<ButtonRelease-1>", lambda e, v=val: cmd(v))
        self.paint_seg(seg)
        return seg

    def paint_seg(self, seg, value="__keep__"):
        if value != "__keep__":
            seg["value"] = value
        for it in seg["items"]:
            sel = it["val"] == seg["value"]
            state = ("selhover" if it["hover"] else "sel") if sel else ("hover" if it["hover"] else "none")
            self.cv.itemconfig(it["img"], image=it["photos"][state])
            self.cv.itemconfig(it["text"][0], fill=self.col("#ffffff" if sel else "#123a5c"))
            if it["text"][1]:
                self.cv.itemconfig(it["text"][1], fill=self.col_sh("#0b3a66" if sel else "#ffffff"))

    def build_slots(self):
        self.slots_title = self.text(44, 266, self.t("slots"), 13, "bold", "#0d3b66")
        self.slots_info = self.text(800, 270, self.t("live"), 9, "normal", "#2a5a85", anchor="ne")
        # Sprachauswahl: Klick öffnet die Liste mit allen Sprachen
        silver = self.th["silver"]
        self.lang_btn = self.button(812, 260, 132, 24, dict(SP.LANGS)[self.lang], silver, self.open_lang_menu, True, 9)
        self.cards = []
        tw, th = 250, 18
        track = masked(grad(self.s(tw), self.s(th), *self.th["track"]),
                       rr_mask(self.s(tw), self.s(th), self.s(th / 2)))
        track.alpha_composite(outline(self.s(tw), self.s(th), self.s(th / 2), self.th["track_edge"], 1))
        self.track_photo = self.photo(track)
        for k in range(3):
            x = 38 + k * 305
            c = {"x": x}
            c["title"] = self.ltext(x + 20, 306, "slot", 15, "bold", "#0d3b66", "#ffffff",
                                    fn=lambda n=k + 1: self.t("slot", n=n))
            c["state"] = self.text(x + 270, 310, "—", 10, "bold", "#2a5a85", anchor="ne")
            self.cv.create_image(self.s(x + 20), self.s(352), image=self.track_photo, anchor="nw")
            c["fill"] = self.cv.create_image(self.s(x + 20), self.s(352), anchor="nw")
            c["fill_photo"] = None
            c["pct"] = self.text(x + 145, 361, "", 9, "bold", "#0d3b66", anchor="center")
            c["remain"] = self.text(x + 20, 384, "", 12, "bold", "#0d3b66")
            c["eta"] = self.text(x + 20, 410, "", 10, "normal", "#2a5a85")
            self.cards.append(c)
        # Kalibrier-Banner (über den Karten)
        ban, pad = glass(self.s(900), self.s(150), self.s(16), 2.2)
        self.banner_photo = self.photo(ban)
        self.banner = self.cv.create_image(self.s(40) - pad, self.s(300) - pad, image=self.banner_photo,
                                           anchor="nw", state="hidden")
        self.banner_head = self.ltext(70, 318, "calib_head", 12, "bold", "#c25e00")
        self.banner_txt = self.cv.create_text(self.s(70), self.s(348), text="", font=(FONT, self.fs(14), "bold"),
                                              fill=self.col("#0d3b66"), anchor="nw", width=self.s(840))

    def build_log(self):
        # Titel ist zugleich der Ein-/Ausklapp-Schalter
        log_title = self.ltext(44, 502, "log", 13, "bold", "#0d3b66",
                               fn=lambda: ("▾  " if self.log_open else "▸  ") + self.t("log"))
        for i in log_title:
            if i:
                self.cv.addtag_withtag("logtoggle", i)
        self.cv.tag_bind("logtoggle", "<Button-1>", lambda e: self.toggle_log())
        self.cv.tag_bind("logtoggle", "<Enter>", lambda e: self.cv.config(cursor="hand2"))
        self.cv.tag_bind("logtoggle", "<Leave>", lambda e: self.cv.config(cursor=""))
        self.stats = self.text(944, 506, "", 10, "bold", "#0d3b66", anchor="ne")
        if not self.log_open:
            return
        frame = tk.Frame(self.root, bg=self.th["log_bg"], bd=0, highlightthickness=1,
                         highlightbackground=self.th["log_border"])
        self.log_frame = frame
        self.log = tk.Text(frame, bg=self.th["log_bg"], fg=self.th["log_fg"], relief="flat", bd=0, wrap="word",
                           font=("Consolas", self.fs(9)), padx=8, pady=6, insertwidth=0, cursor="arrow")
        sb = tk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for name, color in self.th["tags"].items():
            self.log.tag_config(name, foreground=color)
        self.log.configure(state="disabled")
        self.cv.create_window(self.s(38), self.s(532), window=frame, anchor="nw",
                              width=self.s(904), height=self.s(156))
        for line, tag in self.log_lines:                     # bisheriges Protokoll wieder einfüllen
            self.log_insert(line, tag)

    # ------------------------------------------------------------ Anzeige
    def show_banner(self, text):
        self.banner_text = text
        state = "hidden" if text is None else "normal"
        for i in (self.banner, self.banner_txt, *[x for x in self.banner_head if x]):
            self.cv.itemconfig(i, state=state)
            self.cv.tag_raise(i)
        if text:
            self.cv.itemconfig(self.banner_txt, text=text)

    def append_log(self, line, kind=None):
        """kind (vom Bot mitgegeben): "click", "warn", "err", "status" - unabhängig von der Sprache des Textes."""
        tag = kind or None
        self.log_lines.append((line, tag))
        if len(self.log_lines) > 800:
            del self.log_lines[:200]
        if self.log is not None:
            self.log_insert(line, tag)

    def log_insert(self, line, tag):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag)
        if int(self.log.index("end-1c").split(".")[0]) > 800:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def fill_image(self, frac):
        tw, th = self.s(250), self.s(18)
        w = max(int(tw * frac), th)
        img = grad(w, th, (150, 245, 110, 255), (25, 160, 35, 255))
        gl = grad(w - 4, th // 2, (255, 255, 255, 200), (255, 255, 255, 40))
        img.alpha_composite(masked(gl, rr_mask(w - 4, th // 2, th // 4)), (2, 1))
        img = masked(img, rr_mask(w, th, th // 2))
        img.alpha_composite(outline(w, th, th // 2, (20, 120, 30, 255), 1))
        return self.photo(img)

    def update_slots(self, infos):
        """Karten neu zeichnen. Wird jede Sekunde aufgerufen; die Restzeit zählt ab der gelesenen Endzeit live runter."""
        self.slot_data = infos
        bot = self.ctl.mod
        now = time.time()
        by_slot = {}
        for s in infos or []:
            s = dict(s)
            if s["state"] == "läuft" and s.get("end"):
                s["remaining"] = max(0.0, s["end"] - now)
                if s.get("total"):
                    s["percent"] = round(100 * (1 - s["remaining"] / s["total"]))
            by_slot[s["slot"]] = s
        colors = {"leer": "#2a5a85", "läuft": "#0a5cc2", "fertig": "#16801a"}
        names = {"leer": self.t("st_empty"), "läuft": self.t("st_running"), "fertig": self.t("st_done")}
        for k, c in enumerate(self.cards):
            s = by_slot.get(k + 1)
            if k >= bot.SLOTS:
                self.set_text(c["state"], self.t("unused"), "#8aa0b5")
                self.set_text(c["remain"], self.t("locked"), "#8aa0b5")
                self.set_text(c["eta"], "")
                self.set_text(c["pct"], "")
                self.cv.itemconfig(c["fill"], image="")
                continue
            if s is None:
                self.set_text(c["state"], "—", "#2a5a85")
                self.set_text(c["remain"], "", "#0d3b66")
                self.set_text(c["eta"], "")
                self.set_text(c["pct"], "")
                self.cv.itemconfig(c["fill"], image="")
                continue
            self.set_text(c["state"], names.get(s["state"], s["state"].upper()), colors.get(s["state"], "#2a5a85"))
            pct = s["percent"]
            if pct is not None and pct > 0:
                if c.get("last_pct") != pct:      # Balken nur neu malen, wenn sich die Prozent ändern
                    c["fill_photo"] = self.fill_image(min(pct, 100) / 100)
                    c["last_pct"] = pct
                self.cv.itemconfig(c["fill"], image=c["fill_photo"])
                self.set_text(c["pct"], f"{pct} %")
            else:
                c["last_pct"] = None
                self.cv.itemconfig(c["fill"], image="")
                self.set_text(c["pct"], "")
            rem = s["remaining"]
            if s["state"] == "läuft":
                if rem is not None:
                    self.set_text(c["remain"], self.t("remaining", t=bot.fmt_secs(rem)), "#0d3b66")
                    end = s.get("end") or now + rem
                    self.set_text(c["eta"], self.t("done_at", t=time.strftime("%H:%M:%S", time.localtime(end))))
                else:
                    self.set_text(c["remain"], self.t("reading"), "#2a5a85")
                    self.set_text(c["eta"], "")
            elif s["state"] == "fertig":
                self.set_text(c["remain"], self.t("done_excl"), "#16801a")
                self.set_text(c["eta"], self.t("collecting"))
            else:
                self.set_text(c["remain"], "", "#2a5a85")
                self.set_text(c["eta"], "")

    def set_status(self):
        st = self.ctl.state
        if st == "run" and self.ctl.bot and self.ctl.bot.paused:
            orb_, txt = "orange", self.t("s_pause")
        elif st == "run":
            orb_, txt = "green", self.t("s_run")
        elif st == "calib":
            orb_, txt = "blue", self.t("s_calib")
        else:
            orb_, txt = "grey", self.t("s_stop")
        self.cv.itemconfig(self.status_orb, image=self.orbs[orb_])
        self.set_text(self.status_txt, txt)

    # ------------------------------------------------------------ Aktionen
    def set_mode(self, mode):
        if self.ctl.busy():
            self.append_log(self.t("m_stop_mode"), "warn")
            return
        self.ctl.mode = mode
        self.paint_mode()
        self.update_slots(None)

    def paint_mode(self):
        for k, b in self.mode_btns.items():
            sel = k == self.ctl.mode
            self.recolor(b, self.c_aqua if sel else self.c_silver)
            fg, sh = ("#ffffff", "#0b3a66") if sel else ("#123a5c", "#ffffff")
            self.cv.itemconfig(b["text"][0], fill=self.col(fg))
            if b["text"][1]:
                self.cv.itemconfig(b["text"][1], fill=self.col_sh(sh))
        self.set_slots_title()

    def set_slots_title(self):
        shop = self.t("shop_wash" if self.ctl.mode == "waschen" else "shop_grade")
        self.set_text(self.slots_title, f"{self.t('slots')}  ·  {shop}")

    def toggle_bg(self):
        # darf auch während der Bot läuft umgeschaltet werden
        self.ctl.background = not self.ctl.background
        if self.ctl.bot:
            self.ctl.bot.background = self.ctl.background
        self.cv.itemconfig(self.bg_orb, image=self.orbs["green" if self.ctl.background else "grey"])
        self.append_log(self.t("m_bg_on" if self.ctl.background else "m_bg_off"))

    def toggle_notify_end(self):
        # darf auch während der Bot läuft umgeschaltet werden
        self.ctl.notify_end = not self.ctl.notify_end
        if self.ctl.bot:
            self.ctl.bot.notify_end = self.ctl.notify_end
        save_setting("notify_end", self.ctl.notify_end)
        self.cv.itemconfig(self.ne_orb, image=self.orbs["green" if self.ctl.notify_end else "grey"])
        self.append_log(f"{self.t('n_end')}: {self.t('on' if self.ctl.notify_end else 'off')}")

    def toggle_notify_item(self):
        self.ctl.notify_item = not self.ctl.notify_item
        if self.ctl.bot:
            self.ctl.bot.notify_item = self.ctl.notify_item
        save_setting("notify_item", self.ctl.notify_item)
        self.cv.itemconfig(self.ni_orb, image=self.orbs["green" if self.ctl.notify_item else "grey"])
        self.append_log(f"{self.t('n_item')}: {self.t('on' if self.ctl.notify_item else 'off')}")

    def set_theme(self, theme):
        """Tag- oder Nacht-Design wählen (alles wird neu gezeichnet, der Zustand bleibt)."""
        global THEME
        if theme == self.theme:
            return
        self.theme = theme
        THEME = self.theme
        self.th = THEMES[self.theme]
        save_setting("theme", self.theme)
        self.build(self.cv.winfo_width(), self.cv.winfo_height())

    def toggle_afk(self):
        # darf auch während der Bot läuft umgeschaltet werden
        self.ctl.anti_afk = not self.ctl.anti_afk
        if self.ctl.bot:
            self.ctl.bot.anti_afk = self.ctl.anti_afk
        self.cv.itemconfig(self.afk_orb, image=self.orbs["green" if self.ctl.anti_afk else "grey"])
        self.append_log(self.t("m_afk_on" if self.ctl.anti_afk else "m_afk_off"))

    def set_monitor(self, mon, save=True):
        if self.ctl.busy():
            self.append_log(self.t("m_stop_monitor"), "warn")
            return
        self.ctl.monitor = tuple(mon["rect"])
        for mod in BOTS.values():
            mod.MONITOR = self.ctl.monitor            # jeder Monitor hat seine eigene Kalibrierung je Modus
        self.paint_monitor(mon)
        if save:
            self.append_log(self.t("m_mon_chosen", n=mon["nr"], d=mon["desc"]))
            save_setting("monitor", list(mon["rect"]))
            self.append_log(self.t("m_cal_status", s=self.cal_status()))

    def paint_monitor(self, mon):
        self.paint_seg(self.mon_seg, mon["nr"])

    def set_retry(self, sec, save=True):
        """Nachklick-Takt: so oft klickt der Bot erneut, bis Roblox reagiert (max. 30 s). Gilt sofort, auch im Lauf."""
        for mod in BOTS.values():
            mod.RETRY_INTERVAL = sec
            mod.INTERVAL = min(1.0, sec)             # 0,1-s-Modus: auch die Prüf-Schleife läuft im 0,1-s-Takt
        self.paint_retry()
        if save:
            self.append_log(self.t("m_retry", s=fmt_retry(sec)))
            save_setting("retry", sec)

    def paint_retry(self):
        self.paint_seg(self.retry_seg, BOTS["werten"].RETRY_INTERVAL)

    def restore_monitor(self):
        """Zuletzt gewählten Monitor wieder auswählen, falls es ihn noch gibt."""
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                saved = tuple(json.load(f).get("monitor") or ())
        except (OSError, ValueError):
            saved = ()
        for mon in self.monitors:
            if tuple(mon["rect"]) == saved:
                self.set_monitor(mon, save=False)
                self.append_log(self.t("m_mon_selected", n=mon["nr"], d=mon["desc"]))
                self.append_log(self.t("m_cal_status", s=self.cal_status()))
                return
        self.append_log(self.t("m_pick_monitor"), "warn")

    def cal_status(self):
        """Welche Kalibrierungen gibt es für den gewählten Monitor? z.B. 'Waschen ✔  ·  Werten ✖'"""
        parts = []
        for mode, key in (("waschen", "wash"), ("werten", "grade")):
            parts.append(f"{self.t(key)} {'✔' if BOTS[mode].load_calibration() else '✖'}")
        return "  ·  ".join(parts)

    def need_monitor(self):
        if self.ctl.monitor is None:
            self.append_log(self.t("m_pick_monitor"), "warn")
            return True
        return False

    def on_start(self):
        if self.need_monitor():
            return
        if self.ctl.start(self.test_mode):
            self.ctl.started = time.time()
            if self.ctl.mod.load_calibration() is None:
                self.append_log(self.t("m_no_calib"), "warn")
        else:
            self.append_log(self.t("m_running"))

    def on_pause(self):
        self.ctl.toggle_pause()

    def on_stop(self):
        self.ctl.stop()

    def on_calibrate(self):
        if self.need_monitor():
            return
        if not self.ctl.start(self.test_mode, force_calib=True):
            self.append_log(self.t("m_stop_calib"), "warn")

    def open_lang_menu(self):
        menu = tk.Menu(self.root, tearoff=0, font=(FONT, 10))
        for code, name in SP.LANGS:
            menu.add_command(label=("●  " if code == self.lang else "     ") + name,
                             command=lambda c=code: self.apply_language(c))
        x = self.root.winfo_rootx() + self.ox + self.s(812)
        y = self.root.winfo_rooty() + self.oy + self.s(286 + DY)
        menu.tk_popup(x, y)

    def apply_language(self, lang, save=True):
        """Alle Texte sofort in die gewählte Sprache umstellen; der Wert-Bot erkennt [Broken] dann in dieser Sprache."""
        self.lang = lang
        for _m in BOTS.values():
            _m.LANGUAGE = lang
        for ids, fn in self.i18n:
            self.set_text(ids, fn())
        self.set_text(self.lang_btn["text"], dict(SP.LANGS)[lang])
        self.set_slots_title()
        self.slots_info_text = None
        self.set_text(self.slots_info, self.t("live"))
        self.update_slots(self.slot_data)
        self.set_status()
        if save:
            save_setting("lang", lang)
            self.append_log(self.t("m_lang", name=dict(SP.LANGS)[lang]))

    def on_close(self):
        self.ctl.stop()
        self.root.after(200, self.root.destroy)

    # ------------------------------------------------------------ Schleifen
    def animate(self):
        t = time.time()
        for b in self.bubbles:
            b["y"] -= b["v"]
            if b["y"] < -b["d"]:
                b["y"] = self.h + b["d"]
                b["x0"] = random.uniform(0, self.w)
            x = b["x0"] + math.sin(t * b["f"] + b["ph"]) * b["amp"]
            self.cv.coords(b["item"], x, b["y"])
        self.root.after(40, self.animate)

    def tick(self):
        self.set_text(self.clock, time.strftime("%H:%M:%S"))
        days = self.t("days")
        self.set_text(self.date, days[time.localtime().tm_wday] + time.strftime(", %d.%m.%Y"))
        done = self.ctl.bot.done if self.ctl.bot else 0
        run = ""
        if self.ctl.state == "run" and self.ctl.started:
            run = "   ·   " + self.t("runtime", t=BOTS["waschen"].fmt_secs(time.time() - self.ctl.started))
        self.set_text(self.stats, self.t("stats", n=done) + run)
        self.set_status()
        if self.slot_data:
            self.update_slots(self.slot_data)    # Restzeit jede Sekunde weiterzählen
        self.root.after(1000, self.tick)

    def poll(self):
        try:
            while True:
                kind, data = self.ctl.q.get_nowait()
                if kind == "log":
                    if isinstance(data, tuple):
                        self.append_log(*data)
                    else:
                        self.append_log(data)
                elif kind == "slots":
                    # einzelne Plätze (z.B. gerade offenbart / neuer Timer) ersetzen, Rest behalten
                    merged = {s["slot"]: s for s in self.slot_data or []}
                    merged.update({s["slot"]: s for s in data})
                    self.update_slots([merged[k] for k in sorted(merged)])
                    self.slots_info_text = self.t("live_sync", t=time.strftime("%H:%M:%S"))
                    self.set_text(self.slots_info, self.slots_info_text)
                elif kind == "calib":
                    self.show_banner(data)
                elif kind == "state":
                    self.ctl.state = data
                    if data == "stop":
                        self.update_slots(None)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    # Oberfläche nur einmal: eine zweite (z.B. alte, vergessene) Instanz würde gleichzeitig klicken
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = wintypes.HANDLE
    _ui_lock = k32.CreateMutexW(None, True, "Local\\RobloxAutoBot_UI")
    if k32.GetLastError() == 183 and "--screenshot" not in sys.argv:   # ERROR_ALREADY_EXISTS
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Roblox Auto-Bot", SP.tr(load_settings().get("lang", "de"), "m_already_open"))
        r.destroy()
        return
    root = tk.Tk()
    ui = AeroUI(root)
    if "--screenshot" in sys.argv:          # nur für Tests: Bild der Oberfläche speichern
        out = sys.argv[sys.argv.index("--screenshot") + 1]
        stamp = lambda sec: time.strftime("[%d.%m. %H:%M:") + f"{sec:02d}] "
        ui.append_log(stamp(2) + ui.t("b_run", shop=ui.t("shop_wash")), "")
        ui.append_log(stamp(3) + ui.t("b_click", what=ui.t("a_at_slot", what="+", n=1)), "click")
        ui.append_log(stamp(4) + ui.t("b_click", what=ui.t("a_first")), "click")
        ui.append_log(stamp(5) + ui.t("b_timer_read", n=1, t="11:00"), "")
        ui.append_log(stamp(33) + "P1: " + ui.t("b_st_running") + " 68%, " + ui.t("b_left", t="3:31") + " | P2: "
                      + ui.t("b_st_done"), "status")
        if "--banner" in sys.argv:
            ui.show_banner(ui.t("c_step2"))
        ui.ctl.state = "run"
        ui.update_slots([{"slot": 1, "state": "läuft", "percent": 68, "remaining": 211,
                          "end": time.time() + 211, "total": 660},
                         {"slot": 2, "state": "fertig", "percent": 100, "remaining": 0}])

        def shot():
            from PIL import ImageGrab
            root.update()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + ui.w, y + ui.h)).save(out)
            root.destroy()
        root.after(1500, shot)
    root.mainloop()


if __name__ == "__main__":
    main()
