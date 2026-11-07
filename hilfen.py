"""
Gemeinsame Hilfen für beide Bots (Waschen + Werten):

find_cards()   erkennt die Item-Karten in der Auswahlliste von selbst (4, 3, 2 oder 1 pro Reihe - bei weniger als 4
               werden die Karten zentriert). Kein Kalibrieren der Item-Positionen nötig.
empty_list()   erkennt den Zustand "Keine Items verfügbar" (grosser Text, keine Karten).
toast()        zeigt eine Windows-Benachrichtigung.
"""
import os
import subprocess
import threading

import numpy as np

EDGE_STEP = 3          # Pixel Abstand zum Nachbarn, mit dem die 1-Pixel-Rahmen verglichen werden
EDGE_MIN = 30          # so viel heller (Summe R+G+B) muss ein Rahmenpixel als sein ÄUSSERER Nachbar sein
MIN_RUN = 80           # kürzeste senkrechte Linie, die ein Kartenrand sein kann (Pixel)
SQUARE_TOL = 0.04      # Karten sind quadratisch: Breite = Höhe +- 4 %


def _vertical_runs(edge, min_len):
    """Alle senkrechten Linien (x, y0, y1) aus einer True/False-Karte; Lücken bis 2 Pixel werden überbrückt."""
    e = edge.copy()
    e[1:] |= edge[:-1]
    e[:-1] |= edge[1:]
    h, w = e.shape
    cnt = np.zeros(w, np.int32)
    start = np.zeros(w, np.int32)
    prev = np.zeros(w, bool)
    runs = []
    for y in range(h + 1):
        row = e[y] if y < h else np.zeros(w, bool)
        began = row & ~prev
        start[began] = y
        ended = prev & ~row
        if ended.any():
            xs = np.nonzero(ended & (cnt >= min_len))[0]
            runs.extend((int(x), int(start[x]), y - 1) for x in xs)
        cnt = np.where(row, cnt + 1, 0)
        prev = row
    return runs


def _merge(runs):
    """Direkt nebeneinander liegende Linien (2 Pixel breiter Rand) zu einer zusammenfassen."""
    runs.sort()
    out = []
    for x, y0, y1 in runs:
        if out and x - out[-1][0] <= 2 and abs(y0 - out[-1][1]) <= 3 and abs(y1 - out[-1][2]) <= 3:
            continue
        out.append((x, y0, y1))
    return out


def find_cards(img, size_hint=None):
    """Item-Karten in einem Screenshot (H x W x 3, int) finden.
    size_hint = (kleinste, grösste) erwartete Kartenbreite in Pixeln (aus der Kalibrierung) - schliesst z.B. die
    "+"-Kästen oder Fensterrahmen aus. Gibt eine Liste (x0, y0, x1, y1) zurück, in Lesereihenfolge
    (Reihe für Reihe, von links nach rechts). Karten sind quadratisch mit 1-Pixel-Rand; abgeschnittene Karten
    werden ignoriert. Die hervorgehobene Karte (Maus darüber) hat dicke Eckklammern: ihre Randlinien sind
    oben/unten unterbrochen, aber symmetrisch - daraus wird sie ebenfalls rekonstruiert."""
    s = img.astype(np.int32).sum(axis=2)
    h, w = s.shape
    k = EDGE_STEP
    el = np.zeros((h, w), bool)                    # linker Rand: heller als der Nachbar LINKS (aussen)
    er = np.zeros((h, w), bool)                    # rechter Rand: heller als der Nachbar RECHTS (aussen)
    el[:, k:] = (s[:, k:] - s[:, :-k]) >= EDGE_MIN
    er[:, :-k] = (s[:, :-k] - s[:, k:]) >= EDGE_MIN
    left, right = _merge(_vertical_runs(el, 40)), _merge(_vertical_runs(er, 40))
    lo, hi = size_hint if size_hint else (0, 1e9)
    used_l, used_r, cards = set(), set(), []
    # 1. Durchgang: linke + rechte Linie gleicher Höhe, quadratisch -> Karte
    for i, (xa, ya0, ya1) in enumerate(left):
        hh = ya1 - ya0
        if hh < MIN_RUN:
            continue
        for j, (xb, yb0, yb1) in enumerate(right):
            ww = xb - xa
            if ww < lo or ww > hi or ww > 1.15 * hh:
                continue
            if xb > xa and abs(ya0 - yb0) <= 3 and abs(ya1 - yb1) <= 3 and abs(ww - hh) <= SQUARE_TOL * hh:
                cards.append((xa, ya0, xb, ya1))
                used_l.add(i)
                used_r.add(j)
                break
    common = int(np.bincount([c[2] - c[0] for c in cards]).argmax()) if cards else None
    # 2. Durchgang: übrig gebliebene, verkürzte Linien (hervorgehobene Karte): Karte = quadratisch um die Mitte
    for i, (xa, ya0, ya1) in enumerate(left):
        if i in used_l:
            continue
        for j, (xb, yb0, yb1) in enumerate(right):
            if j in used_r or xb <= xa:
                continue
            ww, hh = xb - xa, ya1 - ya0
            if abs(ya0 - yb0) > 3 or abs(ya1 - yb1) > 3 or hh < 40 or ww < MIN_RUN or ww < lo or ww > hi:
                continue
            if common and abs(ww - common) > max(3, 0.03 * common):
                continue
            if 0.45 <= hh / ww <= 0.85:
                cy = (ya0 + ya1) / 2
                cards.append((xa, int(round(cy - ww / 2)), xb, int(round(cy + ww / 2))))
                used_l.add(i)
                used_r.add(j)
                break
    if not cards:
        return []
    # alle Karten sind gleich gross -> die häufigste Grösse behalten
    common = int(np.bincount([c[2] - c[0] for c in cards]).argmax())
    cards = [c for c in cards if abs((c[2] - c[0]) - common) <= max(3, 0.03 * common)]
    # Lesereihenfolge: Reihen nach der Oberkante gruppieren
    cards.sort(key=lambda c: (c[1], c[0]))
    rows, cur = [], [cards[0]]
    for c in cards[1:]:
        if abs(c[1] - cur[0][1]) <= 0.3 * common:
            cur.append(c)
        else:
            rows.append(cur)
            cur = [c]
    rows.append(cur)
    return [c for r in rows for c in sorted(r)]


def card_size_hint(title_pt, close_pt):
    """Erwartete Kartenbreite (min, max) aus dem Abstand zwischen Titel und X der Auswahlliste.
    Die Breite der Karten beträgt ca. 0.224 des Abstands; enger Bereich (+-15 %), damit z.B. die Hotbar am Bildrand nicht als Karten gilt."""
    d = abs(close_pt[0] - title_pt[0])
    if d < 150:
        return None
    return (0.19 * d, 0.26 * d)


def empty_list(img, title_pt, close_pt):
    """Zeigt die Auswahlliste "Keine Items verfügbar"? Erkennt den grossen hellen Text mitten in der Liste,
    ohne ihn zu lesen (funktioniert in jeder Sprache). Punkte in Bildkoordinaten.
    Kartennamen sind viel kleiner (ca. 0.025 des Abstands Titel-X), der Hinweis-Text ca. 0.055."""
    d = abs(close_pt[0] - title_pt[0])
    if d < 150:
        return False
    hh, ww = img.shape[:2]
    x0, x1 = int(max(0, title_pt[0] - 0.05 * d)), int(min(ww, close_pt[0] + 0.05 * d))
    y0, y1 = int(max(0, title_pt[1] + 0.12 * d)), int(min(hh, title_pt[1] + 0.75 * d))
    if x1 - x0 < 50 or y1 - y0 < 50:
        return False
    reg = img[y0:y1, x0:x1]
    bright = reg.min(axis=2) >= 160
    rows = np.nonzero(bright.sum(axis=1) >= 8)[0]
    if len(rows) == 0:
        return False
    groups, start, prev = [], rows[0], rows[0]
    for y in rows[1:]:
        if y - prev > 4:
            groups.append((start, prev))
            start = y
        prev = y
    groups.append((start, prev))
    for g0, g1 in groups:
        height = g1 - g0 + 1
        if not (0.04 * d <= height <= 0.12 * d):
            continue
        cols = np.nonzero(bright[g0:g1 + 1].any(axis=0))[0]
        best, run_start, last = 0, cols[0], cols[0]
        for x in cols[1:]:
            if x - last > 0.04 * d:                  # Lücke grösser als ein Wortabstand -> Kette bricht
                best = max(best, last - run_start)
                run_start = x
            last = x
        best = max(best, last - run_start)
        if best >= 0.18 * d:
            return True
    return False


# ---------------------------------------------------------------- Windows-Benachrichtigung
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:BOT_TOAST_TITLE)
$m = [Security.SecurityElement]::Escape($env:BOT_TOAST_MSG)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def toast(title, message, wait=False):
    """Windows-Benachrichtigung anzeigen (läuft im Hintergrund, blockiert den Bot nicht).
    Gibt bei wait=True zurück, ob es geklappt hat."""
    env = dict(os.environ, BOT_TOAST_TITLE=str(title), BOT_TOAST_MSG=str(message))
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS]

    def run():
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20, creationflags=0x08000000)
            return r.returncode == 0
        except Exception:
            return False
    if wait:
        return run()
    threading.Thread(target=run, daemon=True).start()
    return True
