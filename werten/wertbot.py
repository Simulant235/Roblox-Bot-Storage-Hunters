"""
Bewertungs-Shop-Bot für Roblox
==============================
Füllt leere Plätze automatisch mit dem ersten Item, das nicht [Broken] ist (Raster: 4 pro Reihe,
dann die nächste Reihe, notfalls wird die Liste gescrollt), wartet bis der Timer abgelaufen ist,
klickt OFFENBAREN, überspringt nach 1 Sekunde die Animation (Klick an dieselbe Stelle) und klickt
danach BEANSPRUCHEN. Reagiert Roblox nicht, wird im Nachklick-Takt erneut geklickt, max. 30 s.
Klickt NIE auf "Neurollen" (Robux!), "Überspringen" oder "Freischalten".

Start:   python wertbot.py             -> Bot läuft (kalibriert beim ersten Start)
         python wertbot.py --calibrate -> Kalibrierung neu machen
         python wertbot.py --test      -> nur anzeigen was erkannt wird, keine Klicks
         python wertbot.py --anti-afk  -> zusätzlich alle 3 Min. 3x A + 3x D (gegen AFK-Kick)
         python wertbot.py --monitor 2 -> nur Monitor 2 (von links gezählt)
         (oder die Oberfläche im Hauptordner: "Start Bot.bat")

Hotkeys (funktionieren auch wenn Roblox im Vordergrund ist):
  F8  = Position speichern (nur während Kalibrierung)
  F6  = Pause / Weiter
  F10 = Beenden   (oder Maus in die obere linke Bildschirmecke)
"""
import ctypes
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageGrab

# ---------------------------------------------------------------- Einstellungen
NAME = "Bewertungs-Shop"
SLOTS = 3               # Anzahl freigeschalteter Plätze (gesperrte werden ohnehin nie angeklickt)
INTERVAL = 1.0          # Sekunden: Prüfen, Warten nach Klicks, Animation-Skip, Item-Liste, alles
STATUS_INTERVAL = 30    # Sekunden: nur die Anzeige P1/P2/P3 (Fortschritt + Restzeit)
TIMER_KORREKTUR = -1    # Sekunden: gelesene Restzeit anpassen, damit sie genau mit Roblox übereinstimmt
ITEM_COLUMNS = 4         # Items pro Reihe in der Auswahlliste
MAX_SCROLLS = 15         # so oft wird die Item-Liste höchstens weitergescrollt
ITEM_ROWS = 3            # (alt) Reihen ohne Scrollen
RETRY_INTERVAL = 1.0     # Sekunden: so oft wird nachgeklickt, bis Roblox reagiert (0.5 / 1 / 2 / 3, in der Oberfläche wählbar)
WAIT_MAX = 30           # Sekunden: so lange wird nachgeklickt/gewartet, dann gibt der Bot auf (Fehler)
EMPTY_WAIT = 5           # Sekunden: so lange wird auf Karten gewartet, bevor "keine Items" geprüft/entschieden wird
AFK_INTERVAL = 180      # Sekunden: Anti-AFK (3x A, dann 3x D), damit Roblox nicht wegen Inaktivität kickt
NO_CARDS = "keine-karten"   # choose_item(): in der Liste sind keine Karten zu sehen
LANGUAGE = "de"         # Sprache, in der Roblox die Etiketten zeigt (siehe sprachen.py); Englisch wird immer mitgeprüft

CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalibrierung.json")
CAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalibrierungen")   # eine Datei je Monitor
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")

# Pixel-Koordinaten = echte Bildschirmpixel (auch bei Windows-Skalierung 125%/150%)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32
VK_F6, VK_F8, VK_F10 = 0x75, 0x77, 0x79


class StopBot(BaseException):
    """Bot soll aufhören (F10, Stopp-Knopf). Kein Fehler -> wird nicht als Absturz behandelt."""


# ---------------------------------------------------------------- Eingabe
class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    """Mausposition, oder None wenn Windows sie gerade nicht liefert (z.B. Bildschirm gesperrt)."""
    p = _Point()
    if not user32.GetCursorPos(ctypes.byref(p)):
        return None
    return p.x, p.y


def key_pressed(vk):
    """True, wenn die Taste gerade gedrückt ist."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def keep_awake():
    # Verhindert Bildschirmschoner / Standby, solange der Bot läuft
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def click(x, y):
    focus_roblox()   # sonst wird der erste Klick nur zum Aktivieren des Fensters verbraucht
    # Roblox ignoriert Klicks ohne Mausbewegung -> kurz wackeln, dann klicken
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0001, 1, 0, 0, 0)   # MOVE +1
    time.sleep(0.03)
    user32.mouse_event(0x0001, -1, 0, 0, 0)  # MOVE -1
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
    time.sleep(0.05)


SCAN_A, SCAN_D = 0x1E, 0x20


def press_key(scan):
    # Scancode statt virtueller Taste -> kommt auch in Roblox an
    user32.keybd_event(0, scan, 0x0008, 0)            # KEYDOWN
    time.sleep(0.05)
    user32.keybd_event(0, scan, 0x0008 | 0x0002, 0)   # KEYUP
    time.sleep(0.05)


user32.FindWindowW.restype = wintypes.HWND


def find_roblox():
    """Fenster-Handle von Roblox oder None."""
    return user32.FindWindowW(None, "Roblox") or None


def post_click(x, y):
    """Klick direkt an das Roblox-Fenster schicken - echte Maus bleibt frei. False, wenn Roblox fehlt."""
    hwnd = find_roblox()
    if not hwnd:
        return False
    pt = _Point(int(x), int(y))
    user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(pt))
    lp = wintypes.LPARAM(((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF))
    h = wintypes.HWND(hwnd)
    user32.PostMessageW(h, 0x0200, wintypes.WPARAM(0), lp)   # WM_MOUSEMOVE
    time.sleep(0.05)
    user32.PostMessageW(h, 0x0201, wintypes.WPARAM(1), lp)   # WM_LBUTTONDOWN
    time.sleep(0.06)
    user32.PostMessageW(h, 0x0202, wintypes.WPARAM(0), lp)   # WM_LBUTTONUP
    time.sleep(0.05)
    return True


def post_key(vk, scan):
    """Taste direkt an das Roblox-Fenster schicken, ohne es nach vorne zu holen."""
    hwnd = find_roblox()
    if not hwnd:
        return False
    h = wintypes.HWND(hwnd)
    user32.PostMessageW(h, 0x0100, wintypes.WPARAM(vk), wintypes.LPARAM(1 | (scan << 16)))  # WM_KEYDOWN
    time.sleep(0.05)
    user32.PostMessageW(h, 0x0101, wintypes.WPARAM(vk), wintypes.LPARAM(1 | (scan << 16) | (3 << 30)))  # WM_KEYUP
    time.sleep(0.05)
    return True


user32.GetForegroundWindow.restype = wintypes.HWND


def focus_roblox():
    """Roblox in den Vordergrund holen, falls es nicht schon aktiv ist.
    Ein Klick in ein inaktives Fenster aktiviert nur das Fenster und wird selbst verschluckt."""
    hwnd = find_roblox()
    if not hwnd or user32.GetForegroundWindow() == hwnd:
        return
    h = wintypes.HWND(hwnd)
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)                       # SW_RESTORE (war minimiert)
    fg = user32.GetForegroundWindow()
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
    if other and other != me:
        user32.AttachThreadInput(me, other, True)     # sonst blockiert Windows den Fensterwechsel oft
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    if other and other != me:
        user32.AttachThreadInput(me, other, False)
    time.sleep(0.15)
    if user32.GetForegroundWindow() != hwnd:          # Notlösung: kurzer Alt-Druck hebt die Sperre auf
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetForegroundWindow(h)
        time.sleep(0.15)


MONITOR = None              # (links, oben, rechts, unten) des gewählten Monitors; None = alle Monitore
SCREEN_X, SCREEN_Y = 0, 0   # Bildschirmkoordinate des Pixels (0,0) im letzten Screenshot


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", wintypes.DWORD)]


def list_monitors():
    """Alle Monitore, von links nach rechts nummeriert: [{"nr", "rect", "primary", "desc"}, ...]"""
    mons = []

    def cb(hmon, hdc, rect, lparam):
        info = _MonInfo()
        info.cbSize = ctypes.sizeof(_MonInfo)
        user32.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(info))
        r = info.rcMonitor
        mons.append({"rect": (r.left, r.top, r.right, r.bottom), "primary": bool(info.dwFlags & 1)})
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_Rect),
                              wintypes.LPARAM)(cb)
    user32.EnumDisplayMonitors(None, None, proc, 0)
    mons.sort(key=lambda m: (m["rect"][0], m["rect"][1]))
    for k, m in enumerate(mons):
        l, t, r, b = m["rect"]
        m["nr"] = k + 1
        m["desc"] = f"{r - l}x{b - t}" + (", Hauptmonitor" if m["primary"] else "")
    return mons


def on_monitor(x, y):
    if MONITOR is None:
        return True
    l, t, r, b = MONITOR
    return l <= x < r and t <= y < b


def calibration_fits(cal):
    """Liegen alle kalibrierten Punkte auf dem gewählten Monitor?"""
    keys = ("main_title", "plus1", "plus2", "picker_title", "first_item", "picker_close", "item_right", "item_down")
    return all(on_monitor(*cal[k]) for k in keys if k in cal)


def scroll_wheel(x, y, notches):
    """Mausrad über (x, y) drehen (echte Maus). notches > 0 = nach unten scrollen."""
    focus_roblox()
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0001, 1, 0, 0, 0)   # MOVE +1 (Roblox braucht eine Mausbewegung)
    time.sleep(0.03)
    user32.mouse_event(0x0001, -1, 0, 0, 0)
    time.sleep(0.05)
    for _ in range(abs(notches)):
        user32.mouse_event(0x0800, 0, 0, -120 if notches > 0 else 120, 0)   # WHEEL
        time.sleep(0.06)


def post_scroll(x, y, notches):
    """Mausrad direkt an das Roblox-Fenster schicken (Hintergrund-Modus)."""
    hwnd = find_roblox()
    if not hwnd:
        return False
    h = wintypes.HWND(hwnd)
    delta = -120 if notches > 0 else 120
    lp = wintypes.LPARAM(((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF))
    for _ in range(abs(notches)):
        user32.PostMessageW(h, 0x020A, wintypes.WPARAM((delta & 0xFFFF) << 16), lp)   # WM_MOUSEWHEEL
        time.sleep(0.06)
    return True


def grab():
    """Screenshot vom gewählten Monitor (oder von allen, wenn keiner gewählt ist)."""
    global SCREEN_X, SCREEN_Y
    if MONITOR:
        SCREEN_X, SCREEN_Y = MONITOR[0], MONITOR[1]
        shot = ImageGrab.grab(bbox=tuple(MONITOR), all_screens=True)
    else:
        SCREEN_X, SCREEN_Y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        shot = ImageGrab.grab(all_screens=True)
    return np.asarray(shot.convert("RGB")).astype(np.int16)


# ---------------------------------------------------------------- Erkennung
def crop(img, cx, cy, hw, hh):
    """Ausschnitt um einen Bildschirmpunkt. Gibt (ausschnitt, x0, y0) in Bildschirmkoordinaten zurück."""
    cx, cy = cx - SCREEN_X, cy - SCREEN_Y
    h, w = img.shape[:2]
    x0, x1 = max(0, int(cx - hw)), min(w, int(cx + hw))
    y0, y1 = max(0, int(cy - hh)), min(h, int(cy + hh))
    if x1 <= x0 or y1 <= y0:
        return img[0:0, 0:0], 0, 0
    return img[y0:y1, x0:x1], x0 + SCREEN_X, y0 + SCREEN_Y


def lavender_mask(reg):
    # Das "+" ist hell-lila (ca. 208,200,232 bzw. 224,160,232 beim Hover).
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (r >= 180) & (b >= 210) & (b - g >= 20) & (r >= g)


def looks_like_plus(m, sp):
    """True, wenn die Maske ein dünnes Kreuz (+) zeigt: beide Balken voll, Ecken leer.
    Die Kreuzmitte wird selbst gesucht - der Kalibrierpunkt muss nicht genau in der Mitte liegen."""
    h, w = m.shape
    t = max(1, int(0.012 * sp))
    arm = max(3, int(0.05 * sp))
    gap = max(2, int(0.035 * sp))
    rows, cols = m.mean(axis=1), m.mean(axis=0)
    if rows.max() == 0:
        return False
    cy = int(round(np.nonzero(rows >= 0.9 * rows.max())[0].mean()))   # Mitte des Querbalkens
    cx = int(round(np.nonzero(cols >= 0.9 * cols.max())[0].mean()))   # Mitte des Längsbalkens
    if cy - arm < 0 or cx - arm < 0 or cy + arm >= h or cx + arm >= w:
        return False
    horiz = m[cy - t:cy + t + 1, cx - arm:cx + arm + 1].mean()
    vert = m[cy - arm:cy + arm + 1, cx - t:cx + t + 1].mean()
    rq = (slice(max(0, cy - arm - t), cy - gap + 1), slice(cy + gap, cy + arm + t + 1))
    cq = (slice(max(0, cx - arm - t), cx - gap + 1), slice(cx + gap, cx + arm + t + 1))
    corners = max(m[a, b].mean() if m[a, b].size else 0.0 for a in rq for b in cq)
    return bool(horiz >= 0.85 and vert >= 0.85 and corners <= 0.08)


def green_text_mask(reg):
    # Grüne Schrift von OFFENBAREN / Beanspruchen (ca. 136,216,144)
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (g >= 170) & (g - r >= 50) & (g - b >= 50)


def green_border_mask(reg):
    # Grüner Rahmen der Knöpfe (ca. 104,152,104)
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (g >= 125) & (g <= 185) & (g - r >= 30) & (g - b >= 30)


def red_tag_mask(reg):
    # Dunkelrote [Broken]-Schrift (ca. 128,48,48, bei Hover gedimmt). Grün ~ Blau schliesst Orange/Braun aus.
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (r >= 100) & (r - g >= 45) & (r - b >= 45) & (np.abs(g - b) <= 25)


def gold_mask(reg):
    # Goldener NEUROLLEN-Knopf (ca. 248,200,88 bis 248,224,128) -> NIE anklicken
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (r >= 230) & (g >= 180) & (g <= 235) & (b >= 70) & (b <= 150)


def bar_mask(reg):
    # Gefüllter Teil des Fortschrittsbalkens (ca. 150,207,254)
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (b >= 220) & (g >= 170) & (g <= 225) & (r >= 110) & (r <= 180)


def track_mask(reg):
    # Leerer Teil des Fortschrittsbalkens (grau ca. 58,58,58)
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    return (r >= 54) & (r <= 66) & (np.abs(r - g) <= 4) & (np.abs(r - b) <= 4)


def runs(row):
    """Zusammenhängende True-Stücke einer Zeile als (start, ende_exklusiv)."""
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def bar_fraction(reg):
    """Fortschritt 0..1 aus einem Bildausschnitt mit dem Balken, oder None."""
    fill, track = bar_mask(reg), track_mask(reg)
    best = None
    for y in range(reg.shape[0]):
        for a, b in runs(fill[y]):
            if b - a < 3:
                continue
            # rechts vom blauen Teil muss die graue Spur weitergehen
            g = 0
            while b + g < reg.shape[1] and track[y, b + g]:
                g += 1
            if g < 3:
                continue
            total = (b - a) + g
            if best is None or total > best[0]:
                best = (total, (b - a) / total)
    return None if best is None else best[1]


OCR_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ocr.ps1")
TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?")


def text_lines(reg):
    """Jede Zeile mit weisser Schrift als eigenes OCR-Bild (dunkle Schrift auf weiss, 4x vergrössert)."""
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    white = (r >= 170) & (g >= 170) & (b >= 170) & (np.abs(r - b) <= 40)
    lines = []
    for y in np.nonzero(white.sum(axis=1) >= 2)[0]:
        if lines and y <= lines[-1][1] + 2:
            lines[-1][1] = y
        else:
            lines.append([y, y])
    out = []
    for y0, y1 in lines:
        if y1 - y0 < 5:
            continue
        xs = np.nonzero(white[y0:y1 + 1].any(axis=0))[0]
        x0, x1 = max(0, xs.min() - 2), xs.max() + 3
        lum = reg[max(0, y0 - 2):y1 + 3, x0:x1].mean(axis=2)
        im = Image.fromarray((255 - np.clip((lum - 60) * 255 / 140, 0, 255)).astype(np.uint8))
        im = im.resize((im.width * 4, im.height * 4), Image.LANCZOS)
        canvas = Image.new("L", (im.width + 80, im.height + 80), 255)
        canvas.paste(im, (40, 40))
        out.append(canvas)
    return out


def ocr(images, lang=None):
    """Texterkennung von Windows (ocr.ps1) für mehrere Bilder auf einmal.
    lang = Sprache wie 'ru-RU' (nur wenn Windows sie installiert hat, sonst die Windows-Sprache)."""
    if not images:
        return []
    tmp = tempfile.mkdtemp(prefix="botocr_")
    paths = []
    env = dict(os.environ, BOT_OCR_LANG=lang or "")
    try:
        for k, im in enumerate(images):
            p = os.path.join(tmp, f"{k}.png")
            im.save(p)
            paths.append(p)
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", OCR_SCRIPT, *paths],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                             creationflags=0x08000000, env=env)
        return [line[4:] for line in res.stdout.splitlines() if line.startswith("OCR|")]
    finally:
        for p in paths:
            os.remove(p)
        os.rmdir(tmp)


def parse_time(text):
    """'10:48' -> 648 Sekunden, '1:02:03' -> 3723 Sekunden."""
    m = TIME_RE.search(text)
    if not m:
        return None
    a, b, c = m.groups()
    if int(b) >= 60 or (c and int(c) >= 60):
        return None
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def _load_sprachen():
    """sprachen.py aus dem Hauptordner laden (Broken-Wörter + Texterkennungs-Sprache je Sprache)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sprachen.py")
    try:
        spec = importlib.util.spec_from_file_location("sprachen", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


SPRACHEN = _load_sprachen()


def normalize(text):
    """Kleinbuchstaben, ohne Akzente, nur Buchstaben ('[Kaputt]' -> 'kaputt', 'cassé' -> 'casse')."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[\W\d_]+", "", t)


def broken_words():
    """Wörter für [Broken] in der gewählten Sprache plus Englisch (Roblox zeigt oft Englisch)."""
    table = SPRACHEN.BROKEN if SPRACHEN else {"de": ["kaputt"], "en": ["broken"]}
    words = table.get(LANGUAGE, []) + table.get("en", ["broken"])
    return [normalize(w) for w in words]


def tag_readable(text):
    """Konnte die Texterkennung ein Wort lesen? Bei Russisch/Japanisch/... müssen passende Schriftzeichen dabei sein."""
    word = normalize(text)
    script = SPRACHEN.SCRIPT.get(LANGUAGE) if SPRACHEN else None
    if script:
        return len(re.findall(f"[{script}]", word)) >= 2
    return len(word) >= 3


def ocr_lang():
    """Sprache für die Windows-Texterkennung der Etiketten (z.B. 'ru-RU')."""
    return SPRACHEN.OCR.get(LANGUAGE) if SPRACHEN else None


def tag_is_broken(text, ratio=None):
    """Ist das rote Etikett [Broken]? Wort in der gewählten Sprache oder Englisch -> ja.
    Anderes lesbares Wort (z.B. [Exclusive]) -> nein. Nicht lesbar -> sicherheitshalber wie [Broken] behandeln."""
    word = normalize(text)
    if any(w and w in word for w in broken_words()):
        return True
    return not tag_readable(text)


def signature(img, pt, sp):
    reg, _, _ = crop(img, pt[0], pt[1], 0.25 * sp, 0.05 * sp)
    return reg


def sig_dist(img, pt, sp, ref):
    """Mittlere Farbabweichung zwischen dem Bildausschnitt bei pt und der gespeicherten Signatur."""
    cur = signature(img, pt, sp)
    if cur.shape != ref.shape:
        return float("inf")
    return float(np.abs(cur - ref).mean())


def sig_matches(img, pt, sp, ref):
    return sig_dist(img, pt, sp, ref) < 30


def plus_color_ok(c):
    """Taugt die gespeicherte Farbe als Farbe des + ? (hell genug, z.B. 209,196,233 - nicht 29,29,29)"""
    return bool(c) and sum(c) >= 360 and max(c) >= 150


def plus_color(img, pt, sp):
    """Farbe des + Symbols (die hellsten Pixel um den Punkt)."""
    reg, _, _ = crop(img, pt[0], pt[1], 0.06 * sp, 0.06 * sp)
    if reg.size == 0:
        return None
    px = reg.reshape(-1, 3)
    lum = px.sum(axis=1)
    return px[lum >= np.percentile(lum, 80)].mean(axis=0).round().tolist()


def measure_shift(old, new, geo):
    """Um wie viele Pixel ist die Item-Liste zwischen zwei Screenshots nach oben gewandert?
    0 = nicht bewegt (Listenende), None = nicht messbar."""
    (fx, fy), (rx, ry), (sx, sy) = geo
    a, _, _ = crop(old, fx + 1.5 * rx, fy + 0.5 * sy, 2.0 * rx, 0.95 * sy)
    b, _, _ = crop(new, fx + 1.5 * rx, fy + 0.5 * sy, 2.0 * rx, 0.95 * sy)
    if a.size == 0 or a.shape != b.shape:
        return None
    a = a.mean(axis=2)[::2, ::2]
    b = b.mean(axis=2)[::2, ::2]
    h = a.shape[0]
    smax = min(int(1.2 * sy / 2), h - max(8, h // 5))
    if smax < 4:
        return None
    costs = np.array([np.abs(a[s:] - b[:h - s]).mean() for s in range(smax)])
    best = int(costs.argmin())
    if best != 0 and costs[best] > 0.6 * costs.mean():
        return None                       # kein klares Minimum -> unzuverlässig
    shift = best * 2.0
    return 0.0 if shift < 0.1 * sy else shift


class Layout:
    def __init__(self, cal):
        self.cal = cal
        p1, p2 = cal["plus1"], cal["plus2"]
        self.sp = sp = float(p2[0] - p1[0])          # Abstand zwischen den Plätzen
        self.s2 = (sp / 326.0) ** 2                   # Flächen-Skalierung
        self.plus = [(p1[0] + i * sp, p1[1] + i * (p2[1] - p1[1])) for i in range(3)]
        self.main_sig = np.array(cal["main_sig"], dtype=np.int16)
        self.picker_sig = np.array(cal["picker_sig"], dtype=np.int16)
        # Nur helle Farben taugen als "+-Farbe". Eine dunkle/graue (z.B. weil das + bei der Kalibrierung
        # nicht zu sehen war) wäre die Hintergrundfarbe und würde jeden Platz "zudecken" -> ignorieren.
        self.plus_colors = [np.array(c, dtype=np.int16) for c in cal.get("plus_colors", []) if plus_color_ok(c)]

    def slot_empty(self, img, i):
        """Leerer Platz = ein dünnes + (Kreuzform) in der Mitte, nicht irgendeine lila Fläche."""
        x, y = self.plus[i]
        reg, _, _ = crop(img, x, y, 0.12 * self.sp, 0.12 * self.sp)
        if reg.size == 0:
            return False
        # jede Farbe einzeln prüfen, damit eine falsche Farbe das lila + nicht überdeckt
        masks = [lavender_mask(reg)] + [np.abs(reg - c).sum(axis=-1) < 45 for c in self.plus_colors]
        return any(looks_like_plus(m, self.sp) for m in masks)

    def progress(self, img, i):
        # Balken liegt (wie beim Waschen) ca. 0.42 Platzbreiten unter dem "+", etwas Spielraum
        x, y = self.plus[i]
        reg, _, _ = crop(img, x, y + 0.42 * self.sp, 0.47 * self.sp, 0.2 * self.sp)
        return bar_fraction(reg) if reg.size else None

    def slot_band(self, img, i):
        """Bereich unter dem Item-Bild, wo OFFENBAREN / Beanspruchen / Neurollen liegen."""
        x, y = self.plus[i]
        sp = self.sp
        return crop(img, x, y + 0.475 * sp, 0.45 * sp, 0.275 * sp)

    def green_button(self, img, i):
        """Mitte des grünen Knopfs (Rahmen oben+unten mit grüner Schrift dazwischen) oder None."""
        reg, x0, y0 = self.slot_band(img, i)
        if reg.size == 0:
            return None
        sp = self.sp
        border = green_border_mask(reg)
        rows = np.nonzero(border.sum(axis=1) >= 0.5 * sp)[0]
        if len(rows) < 2:
            return None
        text = green_text_mask(reg)
        for top in rows:
            for bot in rows:
                h = bot - top
                if 0.08 * sp <= h <= 0.2 * sp:
                    inner = text[top + 2:bot - 1]
                    if inner.sum() >= 40 * self.s2:
                        xs = np.nonzero(border[top])[0]
                        return int(x0 + (xs.min() + xs.max()) / 2), int(y0 + (top + bot) / 2)
        return None

    def reroll_visible(self, img, i):
        reg, _, _ = self.slot_band(img, i)
        return reg.size > 0 and gold_mask(reg).sum() >= 300 * self.s2

    def near_gold(self, img, x, y):
        """Sicherheitscheck: liegt ein goldener (Robux-)Knopf direkt an dieser Stelle?"""
        reg, _, _ = crop(img, x, y, 0.15 * self.sp, 0.06 * self.sp)
        return reg.size > 0 and gold_mask(reg).sum() >= 20 * self.s2

    def item_grid(self):
        """((erstes Item), (Vektor nach rechts), (Vektor nach unten)) oder None, wenn nicht kalibriert."""
        c = self.cal
        if "item_right" not in c or "item_down" not in c:
            return None
        fx, fy = c["first_item"]
        return ((fx, fy), (c["item_right"][0] - fx, c["item_right"][1] - fy),
                (c["item_down"][0] - fx, c["item_down"][1] - fy))

    def red_tag(self, img, cx, cy, dx, dy):
        """Rotes Etikett ([Broken], [Exclusive], ...) auf der Karte um (cx, cy)?
        Gibt (Bild für die Texterkennung, Grün/Rot-Verhältnis) zurück oder None. dx/dy = Kartenabstand."""
        reg, x0, _ = crop(img, cx, cy + 0.165 * dy, 0.46 * dx, 0.135 * dy)
        if reg.size == 0:
            return None
        m = red_tag_mask(reg)
        if m.sum() < 40 * (dx / 177.7) ** 2:
            return None
        rows = np.nonzero(m.sum(axis=1) >= 2)[0]
        if len(rows) == 0:
            return None
        span = rows.max() - rows.min() + 1
        if span < 0.04 * dy or span > 0.1 * dy:
            return None           # keine Textzeile (zu flach oder zu hoch, z.B. rotes Item-Bild)
        cols = np.nonzero(m.any(axis=0))[0]
        box = m[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
        if box.mean() > 0.6:
            return None           # ausgefüllter Block = Bild, Text hat Lücken zwischen den Buchstaben
        if abs(x0 + (cols.min() + cols.max()) / 2 - cx) > 0.2 * dx:
            return None           # Text steht nicht mittig in der Karte
        px = reg[m].astype(float)
        ratio = float(np.median(px[:, 1] / np.maximum(px[:, 0], 1)))
        tag = reg[max(0, rows.min() - 3):rows.max() + 4, max(0, cols.min() - 3):cols.max() + 4].astype(float)
        redness = np.clip((tag[..., 0] - (tag[..., 1] + tag[..., 2]) / 2 - 15) * 4, 0, 255)
        im = Image.fromarray((255 - redness).astype(np.uint8))        # rote Schrift -> schwarz auf weiss
        im = im.resize((im.width * 4, im.height * 4), Image.LANCZOS)
        canvas = Image.new("L", (im.width + 80, im.height + 80), 255)
        canvas.paste(im, (40, 40))
        return canvas, ratio

    def item_broken(self, img, cx, cy, dx, dy):
        """Steht auf der Karte um (cx, cy) wirklich [Broken] (nicht z.B. [Exclusive])?"""
        tag = self.red_tag(img, cx, cy, dx, dy)
        if tag is None:
            return False
        texts = ocr([tag[0]], ocr_lang())
        return tag_is_broken(texts[0] if texts else "")

    def timer_images(self, img, i):
        """Bildausschnitte mit dem Timer (ca. 0.5 Platzbreiten unter dem "+")."""
        x, y = self.plus[i]
        reg, _, _ = crop(img, x, y + 0.5 * self.sp, 0.45 * self.sp, 0.25 * self.sp)
        return text_lines(reg) if reg.size else []

    def title_dists(self, img):
        """(Abweichung vom Shop-Titel, Abweichung vom Listen-Titel). Kleiner = passt besser."""
        return (sig_dist(img, self.cal["main_title"], self.sp, self.main_sig),
                sig_dist(img, self.cal["picker_title"], self.sp, self.picker_sig))

    # Shop und Item-Liste haben ihren Titel oft an fast derselben Stelle und in derselben Farbe.
    # Darum gilt immer das, was BESSER passt - nicht nur "unter der Schwelle".
    def main_open(self, img):
        dm, dp = self.title_dists(img)
        return dm < 30 and dm <= dp

    def picker_open(self, img):
        dm, dp = self.title_dists(img)
        return dp < 30 and dp < dm


# ---------------------------------------------------------------- Kalibrierung
def wait_f8(text, say, should_stop):
    say(text + "   " + T("c_hold"))
    while key_pressed(VK_F8):  # warten bis F8 losgelassen ist
        time.sleep(0.03)
    while True:
        if key_pressed(VK_F10) or should_stop():
            say(T("c_cancel"))
            raise StopBot
        if key_pressed(VK_F8) and cursor_pos():
            pos = cursor_pos()
            if not on_monitor(*pos):
                say("   " + T("c_offmon"))
                while key_pressed(VK_F8):
                    time.sleep(0.03)
                continue
            say("   " + T("c_saved_pt", pos=pos))
            time.sleep(0.3)
            return pos
        time.sleep(0.03)


def calibrate(say=print, should_stop=lambda: False, wait_after=5):
    say(T("c_intro", shop=shop()))
    main_title = wait_f8(T("c_step1", shop=shop()), say, should_stop)
    img_main = grab()
    plus1 = wait_f8(T("c_step2"), say, should_stop)
    plus2 = wait_f8(T("c_step3"), say, should_stop)
    if plus2[0] - plus1[0] < 50:
        say(T("c_order"))
        raise StopBot
    sp = float(plus2[0] - plus1[0])
    main_sig = signature(img_main, main_title, sp)

    say(T("c_clickplus"))
    picker_title = wait_f8(T("c_step4"), say, should_stop)
    img_picker = grab()
    picker_close = wait_f8(T("c_step5"), say, should_stop)
    picker_sig = signature(img_picker, picker_title, sp)

    cal = {
        "main_title": main_title, "plus1": plus1, "plus2": plus2,
        "picker_title": picker_title, "picker_close": picker_close,
        "main_sig": main_sig.tolist(), "picker_sig": picker_sig.tolist(),
        "plus_colors": [plus_color(img_main, plus1, sp), plus_color(img_main, plus2, sp)],
    }
    lay = Layout(cal)
    ok = True
    for i in range(2):
        if not lay.slot_empty(img_main, i):
            say(T("c_warn_plus", n=i + 1))
            ok = False
    if lay.picker_open(img_main) or not lay.main_open(img_main):
        say(T("c_warn_title"))
        ok = False
    if not lay.picker_open(img_picker) or lay.main_open(img_picker):
        say(T("c_warn_picker"))
        ok = False
    n_cards = len(HILFEN.find_cards(img_picker, HILFEN.card_size_hint(picker_title, picker_close)))
    say(T("c_cards", n=n_cards))
    if n_cards == 0 and not HILFEN.empty_list(img_picker, (picker_title[0] - SCREEN_X, picker_title[1] - SCREEN_Y),
                                              (picker_close[0] - SCREEN_X, picker_close[1] - SCREEN_Y)):
        say(T("c_warn_cards"))
        ok = False
    save_calibration(cal)
    say(T("c_done") if ok else T("c_done_warn"))
    say(T("c_close_list", s=wait_after))
    time.sleep(wait_after)
    return cal


def cal_path(rect=None):
    """Datei der Kalibrierung: eine pro Monitor (und pro Modus, denn jeder Bot hat seinen eigenen Ordner)."""
    rect = rect or MONITOR
    if not rect:
        return CAL_FILE
    return os.path.join(CAL_DIR, "_".join(str(int(v)) for v in rect) + ".json")


def save_calibration(cal):
    path = cal_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cal, f)


def load_calibration():
    """Kalibrierung für den gewählten Monitor. Die alte Einzeldatei wird übernommen, wenn sie zu diesem Monitor
    passt (und bleibt als Sicherung liegen)."""
    path = cal_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if MONITOR and os.path.exists(CAL_FILE):
        try:
            with open(CAL_FILE, encoding="utf-8") as f:
                legacy = json.load(f)
        except (OSError, ValueError):
            return None
        if calibration_fits(legacy):
            save_calibration(legacy)
            return legacy
    return None


def _load_hilfen():
    """hilfen.py aus dem Hauptordner: Karten-Erkennung, "Keine Items"-Erkennung, Windows-Benachrichtigung."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hilfen.py")
    spec = importlib.util.spec_from_file_location("hilfen", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HILFEN = _load_hilfen()


def T(key, **kw):
    """Meldung in der gewählten Sprache (fehlt sie: Englisch, dann Deutsch)."""
    return SPRACHEN.tr(LANGUAGE, key, **kw) if SPRACHEN else key


def shop():
    return T("shop_grade")


# ---------------------------------------------------------------- Bot
def single_instance():
    """Nur ein Bot gleichzeitig (Waschen ODER Werten, Konsole ODER Oberfläche). Gibt ein Handle oder None zurück."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    h = kernel32.CreateMutexW(None, True, "Local\\RobloxAutoBot_Bot")
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(wintypes.HANDLE(h))
        return None
    return h


def release_instance(h):
    if h:
        ctypes.windll.kernel32.ReleaseMutex(wintypes.HANDLE(h))
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(h))


def fmt_secs(s):
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Bot:
    def __init__(self, cal, test=False, on_log=None, on_slots=None, anti_afk=False, background=False,
                 notify_end=True, notify_item=False, on_notify=None):
        self.lay = Layout(cal)
        self.cal = cal
        self.test = test
        self.on_log = on_log
        self.on_slots = on_slots          # bekommt alle STATUS_INTERVAL Sekunden die Platz-Infos
        self.paused = False
        self.stop_requested = False
        self.anti_afk = anti_afk          # alle AFK_INTERVAL Sekunden 3x A + 3x D
        self.background = background      # Klicks/Tasten direkt an Roblox schicken, echte Maus bleibt frei
        self.bg_fails = 0
        self.last_afk = time.time()
        self.notify_end = notify_end      # Windows-Benachrichtigung, wenn keine Items mehr da sind und der Bot stoppt
        self.notify_item = notify_item    # Windows-Benachrichtigung bei jedem fertigen Item
        self.on_notify = on_notify        # (art, info) -> (titel, text); ohne Oberfläche deutsche Standardtexte
        self.scroll_anchor = None         # Mausposition in der Liste (zum Hochscrollen beim nächsten Öffnen)
        self.no_items_until = 0.0
        self.no_items = False        # "Keine Items mehr" gesehen: Liste wurde 1x geschlossen, jetzt nur noch fertig werden lassen
        self._pend_logged = False
        self.last_status_time = 0.0
        self.done = 0
        self.hint_grid = False
        self.scrolled = False             # Item-Liste wurde gescrollt (beim nächsten Öffnen zuerst nach oben)
        self.timer_try = {}               # Platz -> wann zuletzt versucht wurde, den Timer zu lesen
        self.ends = {}                    # Platz -> (endzeit, gesamtdauer oder None), selbst heruntergezählt

    def log(self, msg, kind=""):
        """kind: "" normal, "click", "warn", "err", "status" - bestimmt nur die Farbe in der Oberfläche."""
        line = time.strftime("[%d.%m. %H:%M:%S] ") + msg
        if self.on_log:
            self.on_log(line, kind)
        else:
            print(line, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def act(self, x, y, what, gold_check=True):
        if gold_check and self.lay.near_gold(grab(), x, y):
            self.log(T("b_safety", what=what), "warn")
            return False
        if self.test:
            self.log(T("b_test_pfx") + what, "warn")
        else:
            self.log(T("b_click", what=what), "click")
        if not self.test:
            self.do_click(x, y)
        return True

    def do_click(self, x, y):
        if not self.background:
            click(x, y)
        elif not post_click(x, y):
            self.log(T("b_no_roblox"), "warn")

    def sleep(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.check_keys()
            time.sleep(0.05)

    def check_keys(self):
        # Ecke oben links = Notbremse, aber nur mit echter Maus (im Hintergrund-Modus gehört die Maus dir)
        if self.stop_requested or key_pressed(VK_F10) or (not self.background and cursor_pos() == (0, 0)):
            raise StopBot
        if key_pressed(VK_F6):
            while key_pressed(VK_F6):
                time.sleep(0.03)
            self.paused = not self.paused
            self.log(T("b_pause") if self.paused else T("b_resume"), "warn")

    def scroll_list(self, x, y, notches):
        self.log((T("b_test_pfx") if self.test else "") +
                 T("b_scroll", dir=T("b_down") if notches > 0 else T("b_up"), n=abs(notches)),
                 "warn" if self.test else "")
        if self.test:
            return
        if self.background:
            if not post_scroll(x, y, notches):
                self.log(T("b_no_roblox"), "warn")
        else:
            scroll_wheel(x, y, notches)

    def stable_grab(self):
        """Screenshot, nachdem die Liste aufgehört hat sich zu bewegen (Scroll-Animation)."""
        img = grab()
        for _ in range(6):
            self.sleep(0.25)
            nxt = grab()
            if img.shape == nxt.shape and np.abs(img - nxt).mean() < 0.8:
                return nxt
            img = nxt
        return img

    def scan_cards(self, img, cards):
        """Karten in Lesereihenfolge durchgehen: (x, y, nr) der ersten, die nicht [Broken] ist. Rote Etiketten werden
        in einem Durchgang gelesen: nur [Broken] (oder Unlesbares) wird übersprungen, [Exclusive] o.ä. wird genommen."""
        seen = []
        for nr, (cx, cy, w, h) in enumerate(cards, 1):
            tag = self.lay.red_tag(img, cx, cy, w * 1.077, h * 1.042)
            seen.append((cx, cy, nr, tag))
            if tag is None:
                break                                     # Karte ohne rotes Etikett -> die nehmen wir
        tagged = [c[3] for c in seen if c[3] is not None]
        words = iter(ocr([t[0] for t in tagged], ocr_lang()) if tagged else [])
        for cx, cy, nr, tag in seen:
            if tag is None:
                return cx, cy, nr
            word = next(words, "")
            if not tag_is_broken(word):
                self.log(T("b_tag_taken", nr=nr, word=word.strip()))
                return cx, cy, nr
            if not tag_readable(word) and not any(w in normalize(word) for w in broken_words()):
                self.log(T("b_tag_unread", nr=nr), "warn")
        return None

    def choose_item(self):
        """Ziel (x, y, nr) des ersten Items, das nicht [Broken] ist. Die Karten werden automatisch erkannt (4, 3, 2 oder
        1 pro Reihe, zentriert). Sind alle sichtbaren kaputt, wird die Liste gescrollt.
        NO_CARDS = keine Karten sichtbar, None = bis zum Listenende nichts Brauchbares."""
        if self.scrolled and self.scroll_anchor:          # Liste war beim letzten Mal gescrollt -> erst nach oben
            self.scroll_list(self.scroll_anchor[0], self.scroll_anchor[1], -MAX_SCROLLS)
            self.scrolled = False
            self.sleep(INTERVAL)
        img = grab()
        cards = self.detect_cards(img)
        if not cards:
            return NO_CARDS
        per_notch = None
        for attempt in range(MAX_SCROLLS + 1):
            hit = self.scan_cards(img, cards)
            if hit:
                if hit[2] > 1:
                    self.log(T("b_broken_skip", nr=hit[2]))
                return hit
            if attempt == MAX_SCROLLS:
                break
            # alles Sichtbare ist [Broken] -> scrollen: erst 1 Rastung, danach so viele, dass höchstens 0,8 Reihen wandern
            cx0, cy0, cw, ch = cards[0]
            mx, my = sum(c[0] for c in cards) / len(cards), cy0 + 0.5 * ch   # Mausposition mitten in der Liste
            self.scroll_anchor = (mx, my)
            notches = 1 if not per_notch else max(1, int(0.8 * ch * 1.042 / per_notch))
            self.scroll_list(mx, my, notches)
            new = self.stable_grab()
            shift = measure_shift(img, new, ((cx0, cy0), (cw * 1.077, 0), (0, ch * 1.042)))
            if not shift:
                self.log(T("b_list_end") if shift == 0 else T("b_scroll_nomeasure"), "warn")
                return None
            per_notch = shift / notches
            self.scrolled = True
            img = new
            cards = self.detect_cards(img)
            if not cards:
                return None
        return None

    def close_picker(self):
        """X der Item-Liste klicken - aber NUR, wenn die Liste wirklich offen ist.
        Sonst läge an dieser Stelle das X des Shops und der Bot würde den Shop schliessen."""
        if not self.test and not self.lay.picker_open(grab()):
            self.log(T("b_list_closed"))
            return
        cx, cy = self.cal["picker_close"]
        self.act(cx, cy, T("a_x_list"), gold_check=False)

    # ---------------------------------------------------------- Item-Liste: Karten werden automatisch erkannt
    def detect_cards(self, img):
        """Karten der Item-Liste: [(mitte_x, mitte_y, breite, höhe), ...] in Bildschirmkoordinaten, in Lesereihenfolge.
        Bei weniger als 4 Items sind die Karten zentriert - das wird automatisch berücksichtigt."""
        t, c = self.cal["picker_title"], self.cal["picker_close"]
        if HILFEN.empty_list(img, (t[0] - SCREEN_X, t[1] - SCREEN_Y), (c[0] - SCREEN_X, c[1] - SCREEN_Y)):
            return []                                  # grosser Hinweistext = leere Liste, nichts anderes als Karte werten
        hint = HILFEN.card_size_hint(t, c)
        rects = HILFEN.find_cards(img, hint)
        return [((x0 + x1) / 2 + SCREEN_X, (y0 + y1) / 2 + SCREEN_Y, x1 - x0, y1 - y0) for x0, y0, x1, y1 in rects]

    def list_empty(self, img):
        """Zeigt die Liste "Keine Items verfügbar" (grosser Text, keine Karten)? Funktioniert in jeder Sprache."""
        t, c = self.cal["picker_title"], self.cal["picker_close"]
        return (HILFEN.empty_list(img, (t[0] - SCREEN_X, t[1] - SCREEN_Y), (c[0] - SCREEN_X, c[1] - SCREEN_Y))
                and not self.detect_cards(img))

    def no_cards_target(self):
        """In der offenen Liste sind keine Karten zu sehen. Kurz warten (Laden); zeigt sie "Keine Items verfügbar",
        wird beendet. Sonst Notlösung: das bei der Kalibrierung gespeicherte erste Item (falls vorhanden).
        Gibt (x, y, 1) zurück oder None, wenn es nichts zu klicken gibt."""
        deadline = time.time() + EMPTY_WAIT
        empty_since = None
        while True:
            img = grab()
            if not self.lay.picker_open(img) or self.detect_cards(img):
                return None                                # Liste ist zu bzw. Karten sind da -> nächster Durchgang
            if self.list_empty(img):
                empty_since = empty_since or time.time()
                if time.time() - empty_since >= 1.5:       # 1,5 s lang stabil leer, nicht nur beim Laden
                    self.finish_no_items()
                    return None
            else:
                empty_since = None
            if time.time() >= deadline:
                break
            self.sleep(0.25)
        first = self.cal.get("first_item")
        if first:
            self.log(T("b_nocards_fb"), "warn")
            return first[0], first[1], 1
        self.log(T("b_nocards_err"), "err")
        self.close_picker()
        self.no_items_until = time.time() + WAIT_MAX
        return None

    def finish_no_items(self):
        """Die Liste zeigt "Keine Items verfügbar". Ist sonst nichts mehr im Gang: zweimal auf X klicken
        (1. Liste schliessen, 2. Fenster schliessen), per Windows-Benachrichtigung Bescheid geben und stoppen."""
        cx, cy = self.cal["picker_close"]
        if self.test:
            self.log(T("b_test_end"), "warn")
            self.no_items_until = time.time() + WAIT_MAX
            return
        self.log(T("b_end_1"))
        self.act(cx, cy, T("a_x_list"), gold_check=False)
        self.wait_for(lambda im: not self.lay.picker_open(im), T("g_list_close"),
                      retry=lambda: self.act(cx, cy, T("a_again", what=T("a_x_list")), gold_check=False))
        self.no_items = True                               # ab jetzt: nichts Neues mehr einfügen, nur fertig werden lassen
        self._pend_logged = False

    def close_shop_and_stop(self):
        """Alles ist fertig und es gibt keine Items mehr: 2. Klick auf X (schliesst jetzt das Fenster), Meldung, Ende."""
        cx, cy = self.cal["picker_close"]
        self.log(T("b_end_2"))
        self.act(cx, cy, T("a_x_shop"), gold_check=False)
        self.wait_for(lambda im: not self.lay.main_open(im) and not self.lay.picker_open(im),
                      T("g_shop_close", shop=shop()),
                      retry=lambda: self.act(cx, cy, T("a_again", what=T("a_x_shop")), gold_check=False))
        self.notify("end", done=self.done)
        self.stop_requested = True
        raise StopBot

    def notify(self, kind, **info):
        """Windows-Benachrichtigung: kind = 'end' (keine Items mehr, Bot gestoppt) oder 'item' (Item fertig)."""
        if self.test or not (self.notify_end if kind == "end" else self.notify_item):
            return
        if self.on_notify:
            title, msg = self.on_notify(kind, info)
        elif kind == "end":
            title, msg = "Roblox Auto-Bot", T("toast_end", shop=shop(), n=info.get("done", 0))
        else:
            slot = info.get("slot")
            title, msg = "Roblox Auto-Bot", (T("toast_item", shop=shop(), slot=T("slot", n=slot)) if slot else T("toast_item_any", shop=shop()))
        HILFEN.toast(title, msg)

    def pending_slots(self, img):
        """Plätze, die noch etwas zu tun haben: belegt (läuft) oder fertig (OFFENBAREN / BEANSPRUCHEN sichtbar)."""
        return [i + 1 for i in range(SLOTS) if self.lay.green_button(img, i) or not self.lay.slot_empty(img, i)]

    def pick_first_item(self, slot=None):
        """Liste ist offen: erstes Item wählen, das nicht [Broken] ist, und warten, bis sie zugeht."""
        if not self.test and not self.lay.picker_open(grab()):
            self.log(T("b_list_notopen"))
            return
        target = self.choose_item()
        if target == NO_CARDS:
            target = self.no_cards_target()               # wartet; "Keine Items" -> Ende; sonst Notlösung
            if target is None:
                return
        elif target is None:
            self.log(T("b_all_broken"), "warn")
            self.close_picker()
            self.no_items_until = time.time() + WAIT_MAX
            self.sleep(INTERVAL)
            return
        fx, fy, nr = target
        label = T("a_first") if nr == 1 else T("a_item", n=nr)
        self.act(fx, fy, label, gold_check=False)
        if self.test:
            return
        if self.wait_for(lambda im: not self.lay.picker_open(im),
                         T("g_list_no_items"),
                         retry=lambda: self.act(fx, fy, T("a_again", what=label), gold_check=False)):
            if slot is not None:
                self.wait_timer(slot)
            return
        self.close_picker()
        self.no_items_until = time.time() + INTERVAL
        self.sleep(INTERVAL)

    def read_timer(self, img, i):
        for text in ocr(self.lay.timer_images(img, i)):
            sec = parse_time(text)
            if sec:
                return sec
        return None

    def push_slot(self, info):
        """Einen einzelnen Platz sofort in der Oberfläche aktualisieren."""
        if self.on_slots:
            self.on_slots([info])

    def clear_slot(self, i):
        """Item ist fertig/offenbart -> Balken, Prozent und Zeit dieses Platzes leeren."""
        self.ends.pop(i, None)
        self.push_slot({"slot": i + 1, "state": "leer", "percent": None, "remaining": None})

    def wait_for(self, cond, giveup, timeout=WAIT_MAX, retry=None):
        """Im Nachklick-Takt (RETRY_INTERVAL) prüfen, ob cond(screenshot) stimmt.
        Solange nicht, wird retry() ausgeführt (= nochmal klicken). True sobald es stimmt,
        False mit FEHLER-Meldung, wenn nach timeout Sekunden insgesamt immer noch nichts passiert ist."""
        end = time.time() + timeout
        while True:
            self.sleep(RETRY_INTERVAL)
            if cond(grab()):
                return True
            if time.time() >= end:
                self.log(T("b_giveup", what=giveup, s=timeout), "err")
                return False
            if retry:
                if cond(grab()):           # direkt vor dem Nachklicken nochmal schauen (Roblox evtl. gerade fertig)
                    return True
                retry()

    def settle(self, cond, what, retry=None):
        """Nach einem Klick auf die Reaktion von Roblox warten, dabei im Takt nochmal klicken (max. WAIT_MAX s)."""
        if self.test:
            self.sleep(INTERVAL)
            return True
        return self.wait_for(cond, T("g_no_react", what=what), retry=retry)

    def refresh_timers(self, img):
        """Läuft ein Platz ohne bekannte Zeit (Timer war noch nicht lesbar), alle 5 s erneut versuchen."""
        now = time.time()
        for i in range(SLOTS):
            if i in self.ends or now - self.timer_try.get(i, 0) < 5:
                continue
            self.timer_try[i] = now
            info = self.slot_info(img, i)
            if info["state"] == "läuft" and info.get("end"):
                self.log(T("b_timer_read", n=i + 1, t=fmt_secs(info["remaining"])))
                self.push_slot(info)

    def wait_timer(self, i):
        """Nach dem Einfügen den Timer lesen - bis zu WAIT_MAX s, Roblox braucht manchmal länger."""
        end = time.time() + WAIT_MAX
        while True:
            sec = self.read_timer(grab(), i)
            if sec:
                end_t = time.time() + sec + TIMER_KORREKTUR
                self.ends[i] = (end_t, sec)
                self.push_slot({"slot": i + 1, "state": "läuft", "percent": 0, "remaining": end_t - time.time(),
                                "end": end_t, "total": sec})
                self.log(T("b_timer_read", n=i + 1, t=fmt_secs(sec)) + " - "
                         + T("b_done_at", t=time.strftime("%H:%M:%S", time.localtime(end_t))))
                return True
            if time.time() >= end:
                self.log(T("b_timer_fail", n=i + 1, s=WAIT_MAX), "warn")
                return False
            self.sleep(INTERVAL)

    def fill_slot(self, i):
        x, y = self.lay.plus[i]
        self.act(x, y, T("a_at_slot", what="+", n=i + 1))
        if self.test:
            return
        if self.wait_for(lambda im: self.lay.picker_open(im), T("g_list_open"),
                         retry=lambda: self.act(x, y, T("a_again", what=T("a_at_slot", what="+", n=i + 1)))):
            self.bg_fails = 0
            self.pick_first_item(i)
        elif self.background:
            self.bg_fails += 1
            if self.bg_fails >= 2:
                self.log(T("b_bg_fail"), "warn")

    def reveal(self, i, x, y):
        """OFFENBAREN klicken, kurz warten, an dieselbe Stelle klicken (Animation skippen), auf das Ergebnis warten."""
        if not self.act(x, y, T("a_at_slot", what=T("a_reveal"), n=i + 1)):
            return
        self.clear_slot(i)
        self.sleep(INTERVAL)
        if self.test:
            return
        if self.lay.reroll_visible(grab(), i):
            self.log(T("b_anim_done"))
            return
        self.act(x, y, T("a_skip"))
        self.wait_for(lambda im: self.lay.reroll_visible(im, i), T("g_result"),
                      retry=lambda: self.act(x, y, T("a_again", what=T("a_reveal") + " / " + T("a_skip"))))

    def slot_info(self, img, i):
        """Zustand eines Platzes für die Anzeige: state, percent, remaining (Sekunden)."""
        now = time.time()
        if self.lay.green_button(img, i):
            self.ends.pop(i, None)
            if self.lay.reroll_visible(img, i):   # schon offenbart -> Anzeige bleibt leer
                return {"slot": i + 1, "state": "leer", "percent": None, "remaining": None}
            return {"slot": i + 1, "state": "fertig", "percent": 100, "remaining": 0}
        if self.lay.slot_empty(img, i):
            self.ends.pop(i, None)
            return {"slot": i + 1, "state": "leer", "percent": None, "remaining": None}
        info = {"slot": i + 1, "state": "läuft", "percent": None, "remaining": None}
        if i not in self.ends:
            # Zeit unbekannt (z.B. Bot mitten im Lauf gestartet) -> Timer einmal lesen
            sec = self.read_timer(img, i)
            if sec:
                self.ends[i] = (time.time() + sec + TIMER_KORREKTUR, None)
        end, total = self.ends.get(i, (None, None))
        if end is not None:
            info["remaining"] = max(0.0, end - now)   # selbst heruntergezählt
            info["end"], info["total"] = end, total   # damit die Oberfläche jede Sekunde weiterzählen kann
            if total:
                info["percent"] = round(100 * (1 - info["remaining"] / total))
        if info["percent"] is None:
            f = self.lay.progress(img, i)
            if f is not None:
                info["percent"] = round(f * 100)
        return info

    def anti_afk_tick(self):
        if not self.anti_afk or time.time() - self.last_afk < AFK_INTERVAL:
            return
        self.last_afk = time.time()
        self.log((T("b_test_pfx") if self.test else "") + T("b_afk"), "warn" if self.test else "")
        if self.test:
            return
        if self.background:
            for vk, scan in ((0x41, SCAN_A),) * 3 + ((0x44, SCAN_D),) * 3:
                post_key(vk, scan)
            return
        focus_roblox()
        for scan in (SCAN_A,) * 3 + (SCAN_D,) * 3:
            press_key(scan)

    def report_status(self, img):
        infos = [self.slot_info(img, i) for i in range(SLOTS)]
        self.last_status_time = time.time()
        if self.on_slots:
            self.on_slots(infos)
        parts = []
        for s in infos:
            st = {"leer": "b_st_empty", "läuft": "b_st_running", "fertig": "b_st_done"}.get(s["state"])
            txt = f"P{s['slot']}: {T(st) if st else s['state']}"
            if s["state"] == "läuft":
                if s["percent"] is not None:
                    txt += f" {s['percent']}%"
                if s["remaining"] is not None:
                    eta = time.strftime("%H:%M:%S", time.localtime(time.time() + s["remaining"]))
                    txt += ", " + T("b_left", t=fmt_secs(s["remaining"])) + " (" + T("b_done_at", t=eta) + ")"
            parts.append(txt)
        self.log(" | ".join(parts), "status")

    def step(self):
        img = grab()
        lay = self.lay

        # Item-Liste offen (z.B. von vorhin) -> erstes Item nehmen
        if lay.picker_open(img):
            if self.no_items or time.time() < self.no_items_until:
                self.close_picker()
                self.sleep(INTERVAL)
            else:
                self.pick_first_item()
            return

        if not lay.main_open(img):
            if time.time() - self.last_status_time >= STATUS_INTERVAL:
                self.last_status_time = time.time()
                self.log(T("b_not_open", shop=shop()))
            return

        for i in range(SLOTS):
            btn = lay.green_button(img, i)
            if btn:
                if lay.reroll_visible(img, i):
                    # Neurollen sichtbar -> der grüne Knopf darüber ist BEANSPRUCHEN
                    if self.act(*btn, T("a_at_slot", what=T("a_claim"), n=i + 1)):
                        self.done += 1
                        self.notify("item", slot=i + 1, done=self.done)
                        self.settle(lambda im: not lay.reroll_visible(im, i), T("a_claim"),
                                    retry=lambda: self.act(*btn, T("a_again", what=T("a_at_slot", what=T("a_claim"), n=i + 1))))
                else:
                    self.reveal(i, *btn)
                return
            if lay.slot_empty(img, i) and not self.no_items and time.time() >= self.no_items_until:
                self.fill_slot(i)
                return

        # Nichts zu tun -> nur alle STATUS_INTERVAL Sekunden die Anzeige P1/P2/P3
        if self.no_items:
            pending = self.pending_slots(img)
            if not pending:
                self.close_shop_and_stop()
            if not self._pend_logged:
                self._pend_logged = True
                self.log(T("b_end_pending", slots=", ".join(map(str, pending))))
        self.refresh_timers(img)
        if time.time() - self.last_status_time >= STATUS_INTERVAL:
            self.report_status(img)

    def run(self):
        lock = single_instance()
        if lock is None:
            self.log(T("b_other_bot"), "warn")
            return
        self.log(T("b_run", shop=shop()) + ("  [TEST]" if self.test else ""))
        keep_awake()
        try:
            while True:
                self.check_keys()
                if self.paused:
                    self.sleep(0.2)
                    continue
                try:
                    self.step()
                    self.anti_afk_tick()
                except Exception:
                    # Nie abbrechen: Fehler protokollieren und nach kurzer Pause weitermachen
                    self.log(T("b_error") + "\n" + traceback.format_exc().rstrip(), "err")
                keep_awake()
                self.sleep(INTERVAL)
        except StopBot:
            self.log(T("b_end", n=self.done))
        finally:
            release_instance(lock)


def main():
    args = sys.argv[1:]
    global MONITOR, LANGUAGE
    if "--monitor" in args:   # z.B. --monitor 2  (Nummerierung von links nach rechts)
        MONITOR = list_monitors()[int(args[args.index("--monitor") + 1]) - 1]["rect"]
    if "--sprache" in args:   # z.B. --sprache en  (Sprache, in der Roblox die Etiketten zeigt)
        LANGUAGE = args[args.index("--sprache") + 1]
    try:
        cal = load_calibration()
        if "--calibrate" in args or cal is None:
            cal = calibrate()
    except StopBot:
        return
    Bot(cal, test="--test" in args, anti_afk="--anti-afk" in args, background="--hintergrund" in args,
        notify_end="--no-notify-end" not in args, notify_item="--notify-item" in args).run()


if __name__ == "__main__":
    main()
