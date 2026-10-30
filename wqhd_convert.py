#!/usr/bin/env python3
"""
Convert Enigma2 skins (1920x1080 or 1280x720) to WQHD 2560x1440.

Scales every position, size, font size, list/item height, parameter and
list-template value in the skin XML, plus all PNG/JPG graphics, and renames
the skin folder (and any shared "Common" folders it uses) so the result
installs alongside the original.

Usage:
  # an OpenViX/skins checkout (src/skins/1080 and 720)
  python3 wqhd_convert.py --skins-root path/to/OpenViX-skins/src/skins \
      --skins "Simple_Ten_Eighty,YouViX-Blue" --out dist

  # any repository: skins are found wherever a folder holds a skin.xml
  python3 wqhd_convert.py --repo-root path/to/checkout --skins all --out dist

Each selected skin produces  dist/<NewName>.zip  (unpacks to
usr/share/enigma2/...) and  dist/<NewName>-report.txt.
"""
import argparse
import collections
import fnmatch
import os
import re
import shutil
import sys
import zipfile
from fractions import Fraction

from PIL import Image

TARGET_W, TARGET_H = 2560, 1440
SKIP_FILES = ("Makefile.am", "Makefile", "Thumbs.db", "*.db")

# ---------------------------------------------------------------------------
# What counts as a pixel value
# ---------------------------------------------------------------------------
# Geometry attributes that may hold layout expressions (e, c, center, *, %, top, fill ...)
EXPR_ATTRS = {"position", "size"}
# Integer (or integer list) attributes measured in pixels.
PIXEL_ATTRS = {
    "borderWidth", "widgetBorderWidth", "scrollbarWidth", "scrollbarSliderBorderWidth",
    "ProgressbarBorderWidth", "progressbarBorderWidth", "progressbarHeight", "progressBarWidth",
    "progressPercentWidth", "EventBorderWidth", "ServiceBorderWidth", "EventBorderVerWidth",
    "ServiceBorderVerWidth", "EventNamePadding", "ServiceNamePadding", "IconXPadding",
    "IconYPadding", "RecIconSize", "itemHeight", "itemHeightTwoLine", "itemWidth", "itemmHeight",
    "itemmheight", "itemSpacing", "itemsDistances", "iconMargin", "iconsWidth", "offset",
    "textOffset", "shadowOffset", "fieldMargins", "nonplayableMargins", "dateWidth",
    "durationWidth", "partIconeShift", "partIconeShiftCompact", "partIconeShiftMinimal",
    "partIconeShiftOriginal", "pbarHeight", "pbarLargeWidth", "piconMargin", "rowHeight",
    "rowSplit", "rowSplit1", "rowSplit2", "satPosLeft", "separatorLineThickness",
    "serviceItemHeight", "spaceIconeText", "spaceLeft", "spaceRight", "spacing", "padding",
    "bubbletextPadding", "treeDescription", "columns", "columnsCompactDescription",
    "columnsOriginal", "compactColumn", "column", "fontSizesCompact", "fontSizesMinimal",
    "setColGap", "setColWidths", "setIconDistance", "setPboxDistance", "setPiconDistance",
    "setPiconSize", "setPict", "setTemp", "setTempUnits", "setTime", "setTimeWidth", "setWind",
    "setWindPict", "setWindUnits", "text1Pos", "text2Pos", "text3Pos", "text4Pos", "textPos",
    "height", "width", "xres", "yres", "resolution", "indent", "listMarginLeft",
    "listMarginRight", "splitPosition", "itemHeights", "fontSizesOriginal",
}
# Attributes that hold numbers which are NOT pixels.
NON_PIXEL_ATTRS = {
    "numOfSpaces",
    "zPosition", "zposition", "NumberOfRows", "NumberOfItems", "NoAnimationAfter", "rowsincolumn",
    "colposition", "maxPages", "Wrap", "wrap", "nostrip", "scale", "split", "selectionDisabled",
    "index", "id", "bpp", "version", "replacement", "alphaBlend", "hidePointerOnZeroLength",
    "noLabel", "allVisible", "setPictScale", "setWindPictScale", "enableWrapAround", "noWrap",
    "transparent", "paddingFactor", "selectionZoom", "steptime", "options", "objectTypes",
    "conditional", "text", "title", "name", "source", "render", "type", "flags", "filename",
    "pixmap", "pixmaps", "selectionPixmap", "backgroundPixmap", "path", "encoding",
    "standalone", "connection", "elements", "addon", "pos", "layout", "orientation",
}
# Name patterns for plugin-specific geometry attributes (e.g. CoolTVGuide "CoolBarPos").
PIXEL_NAME = re.compile(r"(^spacing|Pos$|Size$|SizeSa$|Width$|Height$|High$|Padding$|Margins?$|Distances?$|"
                        r"Gap$|Offset$|Shift$|Thickness$|Spacing$|Pico$)")
PARAM_NOT_PIXEL = {"AllowUserDatesAndTimes", "AboutHddSplit", "SecondInfoBarSimple"}
FONT_VALUE = re.compile(r"^\s*[^;,\"]+;\s*\d+\s*(,\s*[^;,\"]+;\s*\d+\s*)*$")
INT = re.compile(r"^(\s*)([+-]?\d+)(\s*)$")

# Front-panel (LCD/OLED/VFD) skins and screens keep their own resolution.
DISPLAY_FILE = re.compile(r"^skin_(display|lcd|vfd|oled|summary)", re.I)
SCREEN_BLOCK = re.compile(r"<screen\b([^>]*?)(?:/>|>(.*?)</screen>)", re.S)
OUTPUT_BLOCK = re.compile(r"<output\b([^>]*)>(.*?)</output>", re.S)
IMAGE_REF = re.compile(r"([\w\-./~+]+\.(?:png|jpe?g|svg))", re.I)


def attr_of(attrs, name):
    m = re.search(rf'(?<![\w-]){name}\s*=\s*"([^"]*)"', attrs or "")
    return m.group(1) if m else None


def is_display_id(v):
    return v is not None and v.strip() not in ("", "0")


class Converter:
    def __init__(self, factor, renames, display_names=frozenset()):
        self.f = Fraction(factor)
        self.renames = renames  # {old_folder: new_folder}
        self.display_names = set(display_names)  # screen templates used only by front-panel screens
        self.log = collections.Counter()
        self.unknown = collections.Counter()
        self.left_expr = collections.Counter()

    # -- number scaling ------------------------------------------------------
    def sc(self, v):
        v = int(v)
        x = abs(v) * self.f
        r = int(x + Fraction(1, 2))  # round half up
        return -r if v < 0 else r

    def scale_int_token(self, tok):
        m = INT.match(tok)
        return f"{m.group(1)}{self.sc(m.group(2))}{m.group(3)}" if m else tok

    def scale_list(self, val):
        return ",".join(self.scale_int_token(t) for t in val.split(","))

    def scale_expr_token(self, tok):
        if INT.match(tok):
            return self.scale_int_token(tok)
        s = tok.strip()
        if s in ("center", "fill", "top", "bottom", "left", "right", "e", "c", "*", ""):
            return tok
        if re.search(r"[fwh]", s):  # skin-factor / font-metric formulas rescale themselves
            self.left_expr[s] += 1
            return tok
        m = re.fullmatch(r"(\d+)\*(\d+)", s)
        if m:  # rows * itemHeight -> only the pixel part scales
            new = f"{m.group(1)}*{self.sc(m.group(2))}"
        else:
            new = re.sub(r"(?<![\d.])\d+(?![\d%.])", lambda d: str(self.sc(d.group(0))), s)
        self.log[f"expr {s} -> {new}"] += 1
        return tok.replace(s, new)

    def scale_expr(self, val):
        return ",".join(self.scale_expr_token(t) for t in val.split(","))

    def scale_font(self, val):
        if re.search(r";\s*[^\d\s,]", val):  # e.g. "Regular;20*f" - self scaling
            return val
        return re.sub(r";(\s*)(\d+)", lambda m: f";{m.group(1)}{self.sc(m.group(2))}", val)

    # -- XML -----------------------------------------------------------------
    ATTR = re.compile(r'(?<![\w:.-])([A-Za-z_][\w:.-]*)(\s*=\s*)"([^"]*)"')
    TAG = re.compile(r"<([A-Za-z_][\w:.-]*)((?:\s+[\w:.-]+\s*=\s*\"[^\"]*\")*\s*/?)>")

    def scale_tag(self, m):
        tag, body = m.group(1), m.group(2)
        pname = None
        if tag == "parameter":
            pm = re.search(r'\bname\s*=\s*"([^"]*)"', body)
            pname = pm.group(1) if pm else None

        def attr(a):
            name, eq, val = a.groups()
            new = val
            if tag == "font" or not re.search(r"\d", val) or val.lstrip().startswith("#"):
                return a.group(0)
            if tag == "parameter" and name == "value":
                if pname in PARAM_NOT_PIXEL:
                    pass
                elif val.startswith("*"):
                    if pname and pname.lower().endswith("font"):  # "*Regular,33"
                        new = re.sub(r",(\s*)(\d+)$", lambda d: f",{d.group(1)}{self.sc(d.group(2))}", val)
                elif FONT_VALUE.match(val):
                    new = self.scale_font(val)
                else:
                    new = self.scale_list(val)
            elif name in EXPR_ATTRS:
                new = self.scale_expr(val)
            elif name in ("pointer", "seek_pointer"):
                f, _, xy = val.rpartition(":")
                new = f"{f}:{self.scale_list(xy)}" if f else val
            elif name == "cornerRadius" or "CornerRadius" in name:
                new = re.sub(r"^\s*\d+", lambda d: str(self.sc(d.group(0))), val)
            elif FONT_VALUE.match(val) and name not in NON_PIXEL_ATTRS:
                new = self.scale_font(val)
            elif name in PIXEL_ATTRS or (name not in NON_PIXEL_ATTRS and PIXEL_NAME.search(name)):
                new = self.scale_list(val)
                if name not in PIXEL_ATTRS:
                    self.log[f"pixel by name: {name}"] += 1
            elif name not in NON_PIXEL_ATTRS and not re.search(r"Colou?r", name):
                if re.fullmatch(r"[\d\s,+-]+", val):
                    self.unknown[f"<{tag} {name}=\"{val}\">"] += 1
            return f'{name}{eq}"{new}"'

        return f"<{tag}{self.ATTR.sub(attr, body)}>"

    def scale_code_block(self, text):
        """TemplatedMultiContent bodies."""
        sc = self.sc
        pair = lambda m: f"{m.group(1)}{sc(m.group(2))}{m.group(3)}{sc(m.group(4))}{m.group(5)}"
        one = lambda m: f"{m.group(1)}{sc(m.group(2))}"
        text = re.sub(r"(\b(?:pos|size)\s*=\s*\(\s*)(-?\d+)(\s*,\s*)(-?\d+)(\s*\))", pair, text)
        text = re.sub(r'(gFont\(\s*"[^"]*"\s*,\s*)(\d+)', one, text)
        text = re.sub(r'("item(?:Height|Width)"\s*:\s*)(\d+)', one, text)
        text = re.sub(r'("[\w -]+"\s*:\s*\(\s*)(\d+)(?=\s*,\s*\[)', one, text)
        text = re.sub(r"(\b(?:border_width|borderWidth|cornerRadius|corner_radius)\s*=\s*)(\d+)", one, text)
        return text

    def scale_applet(self, code):
        """Python applets: scale integer literals, except indexes, divisors and text-length checks."""
        out = []
        for line in code.split("\n"):
            if line.lstrip().startswith("#"):
                out.append(line)
                continue
            has_len = "len(" in line

            def num(m):
                s, pre = m.group(0), line[:m.start()].rstrip()
                if pre.endswith(("[", "/", "getDesktop(")):
                    return s
                if has_len and re.search(r"(&lt;|&gt;|<|>|==|!=)=?$", pre):
                    return s
                return str(self.sc(s))
            new = re.sub(r"(?<![\w.])\d+(?![\w.])", num, line)
            if new != line:
                self.log["applet lines scaled"] += 1
            out.append(new)
        return "\n".join(out)

    def rename_refs(self, text):
        for old, new in self.renames.items():
            text = text.replace(f"/usr/share/enigma2/{old}/", f"/usr/share/enigma2/{new}/")
            text = re.sub(rf"(?<=[\"'>,:]){re.escape(old)}/", f"{new}/", text)
        return text

    def convert_xml(self, text):
        # front-panel screens and outputs are set aside untouched, then put back
        kept = []

        def keep(m):
            kept.append(m.group(0))
            return f"\x00KEEP{len(kept) - 1}\x00"

        text = OUTPUT_BLOCK.sub(lambda m: keep(m) if is_display_id(attr_of(m.group(1), "id")) else m.group(0), text)

        def screen(m):
            a = m.group(1)
            if is_display_id(attr_of(a, "id")) or attr_of(a, "name") in self.display_names:
                self.log["front-panel screens left at their own size"] += 1
                return keep(m)
            return m.group(0)
        text = SCREEN_BLOCK.sub(screen, text)
        text = self.TAG.sub(self.scale_tag, text)
        text = re.sub(r'(<convert\s+type="TemplatedMultiContent"\s*>)(.*?)(</convert>)',
                      lambda m: m.group(1) + self.scale_code_block(m.group(2)) + m.group(3), text, flags=re.S)
        text = re.sub(r"(<applet\b[^>]*>)(.*?)(</applet>)",
                      lambda m: m.group(1) + self.scale_applet(m.group(2)) + m.group(3), text, flags=re.S)
        text = re.sub(r"\x00KEEP(\d+)\x00", lambda m: kept[int(m.group(1))], text)
        return self.rename_refs(text)

    # -- images --------------------------------------------------------------
    def convert_image(self, src, dst):
        im = Image.open(src)
        w, h = im.size
        nw, nh = max(1, self.sc(w)), max(1, self.sc(h))
        if src.lower().endswith((".jpg", ".jpeg")):
            im.convert("RGB").resize((nw, nh), Image.LANCZOS).save(dst, quality=92, subsampling=0)
            return
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        if has_alpha:
            # resample with premultiplied alpha so transparent edges don't get dark fringes
            out = im.convert("RGBA").convert("RGBa").resize((nw, nh), Image.LANCZOS).convert("RGBA")
            if out.getextrema()[3][0] == 255:
                out = out.convert("RGB")
        else:
            out = im.convert("RGB").resize((nw, nh), Image.LANCZOS)
        out.save(dst, optimize=True)


# ---------------------------------------------------------------------------
# Folder discovery / naming
# ---------------------------------------------------------------------------
def default_new_name(name):
    if name == "Simple_Ten_Eighty":
        return "Simple_Fourteen_Forty"
    for a, b in (("1080", "1440"), ("FHD", "WQHD"), ("720", "1440")):
        if a in name:
            return name.replace(a, b)
    return f"{name}-1440"


def index_folders(skins_root):
    """{folder_name: path} for every top-level folder under 1080/ and 720/ (1080 wins)."""
    found = {}
    for res in ("720", "1080"):
        base = os.path.join(skins_root, res)
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                if os.path.isdir(os.path.join(base, d)):
                    found[d] = os.path.join(base, d)
    return found


# Symlinks are followed only while they stay inside this tree (set by main), so a
# repository can't pull files from elsewhere on the machine into the output.
ALLOWED_ROOT = None


def inside_allowed(path):
    if ALLOWED_ROOT is None:
        return True
    real = os.path.realpath(path)
    return real == ALLOWED_ROOT or real.startswith(ALLOWED_ROOT + os.sep)


def walk_files(folder, skip_dirs=()):
    """Relative paths of every file under folder. Symlinked folders/files are followed
    (some repos share assets that way) but only inside ALLOWED_ROOT, and never in a loop."""
    seen = set()
    out = []
    for root, dirs, files in os.walk(folder, followlinks=True):
        real = os.path.realpath(root)
        if real in seen or not inside_allowed(root):
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and d not in SKIP_DIRS)
        for f in sorted(files):
            p = os.path.join(root, f)
            if os.path.isfile(p) and inside_allowed(p):
                out.append(os.path.relpath(p, folder))
    return out


def xml_files(folder):
    for rel in walk_files(folder):
        if rel.lower().endswith(".xml"):
            yield os.path.join(folder, rel)


def find_refs(folder, known):
    """Folder names referenced from this folder's XML, plus the exact relative paths used."""
    own_subdirs = {d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))}
    names = "|".join(re.escape(n) for n in sorted(known, key=len, reverse=True))
    pat = re.compile(rf"(?:/usr/share/enigma2/|(?<=[\"'>,:]))({names})/([^\"'<>,:\s]*)")
    refs = collections.defaultdict(set)
    for f in xml_files(folder):
        text = re.sub(r"<!--.*?-->", "", open(f, encoding="utf-8", errors="replace").read(), flags=re.S)
        for m in pat.finditer(text):
            name = m.group(1)
            absolute = text[max(0, m.start() - 19):m.start()] == "/usr/share/enigma2/"
            if name in own_subdirs and not absolute:
                continue  # resolves inside the skin's own folder first
            refs[name].add(m.group(2))
    return refs


def _read(p):
    return re.sub(r"<!--.*?-->", "", open(p, encoding="utf-8", errors="replace").read(), flags=re.S)


def skin_size(folder):
    """(xres, yres) of the GUI output, from skin.xml or a file it includes; None if not found."""
    name = os.path.basename(os.path.normpath(folder))
    todo, seen = [os.path.join(folder, "skin.xml")], set()
    while todo:
        p = todo.pop(0)
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        t = _read(p)
        for m in OUTPUT_BLOCK.finditer(t):
            if is_display_id(attr_of(m.group(1), "id")):
                continue
            r = re.search(r"<resolution\b([^>]*)>", m.group(2))
            if r:
                x, y = attr_of(r.group(1), "xres"), attr_of(r.group(1), "yres")
                if x and x.isdigit():
                    return int(x), int(y) if y and y.isdigit() else None
        for inc in re.findall(r'<include\b[^>]*filename="([^"]+)"', t):
            if inc.startswith("/usr/share/enigma2/"):
                parts = inc[len("/usr/share/enigma2/"):].split("/", 1)
                if parts[0] == name and len(parts) == 2:
                    todo.append(os.path.join(folder, parts[1]))
            elif not inc.startswith("/"):
                todo.append(os.path.normpath(os.path.join(folder, inc)))
    return None


def skin_resolution(folder):
    size = skin_size(folder)
    return size[0] if size else None


def why_not_convertible(folder):
    """None if the skin can be converted, else the reason."""
    size = skin_size(folder)
    if not size:
        return "no screen resolution found in its skin.xml (or the files it includes)"
    x, y = size
    if x == TARGET_W:
        return f"it is already {TARGET_W} wide"
    if y and x * 9 != y * 16:
        return f"it is {x}x{y}, not 16:9, so it can't be scaled evenly to {TARGET_W}x{TARGET_H}"
    if x > TARGET_W:
        return f"it is {x} wide, larger than {TARGET_W}"
    return None


SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".svn"}


def discover_repo(root, root_name=None):
    """Any repository: every folder holding a skin.xml is a skin; its sibling folders are
    candidates for shared folders it may reference. Returns (folders, skins, duplicates)."""
    root = os.path.abspath(root)
    skins, dups = {}, []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if "skin.xml" in files:
            name = os.path.basename(dirpath) if dirpath != root else (root_name or os.path.basename(root))
            if name in skins:
                dups.append(os.path.relpath(dirpath, root))
            else:
                skins[name] = dirpath
    folders = dict(skins)
    for path in list(skins.values()):
        parent = os.path.dirname(path)
        if path == root:
            continue
        for d in sorted(os.listdir(parent)):
            q = os.path.join(parent, d)
            if os.path.isdir(q) and d not in SKIP_DIRS:
                folders.setdefault(d, q)
    return folders, skins, dups


def norm_ref(ref, own, deps):
    """'/usr/share/enigma2/X/a.png', 'X/a.png' or 'a.png' -> (folder, path inside it)."""
    r = ref.split("#")[0]
    if r.startswith("/usr/share/enigma2/"):
        r = r[len("/usr/share/enigma2/"):]
    first, _, rest = r.partition("/")
    if rest and first in deps:
        return first, rest
    return own, r


def analyse_display(dep_xmls, own, deps):
    """Find front-panel material in a skin: whole display files, screen templates used only
    by front-panel screens, and images used only there. Returns
    (display_files, display_names, display_only_images)."""
    whole, screens = set(), []  # screens: [name, is_display, body]
    disp_refs, main_refs = set(), set()

    def refs_in(text, dep):
        out = set()
        for v in re.findall(r'"([^"]*)"', text):
            for tok in IMAGE_REF.findall(v):
                out.add(norm_ref(tok.split(":")[-1], dep, deps))
        return out

    for dep, path in dep_xmls:
        t = _read(path)
        outs = [attr_of(m.group(1), "id") for m in re.finditer(r"<output\b([^>]*)>", t)]
        if DISPLAY_FILE.match(os.path.basename(path)) or (outs and all(is_display_id(o) for o in outs)):
            whole.add(path)
            disp_refs |= refs_in(t, dep)
            continue
        for m in SCREEN_BLOCK.finditer(t):
            a = m.group(1)
            screens.append([attr_of(a, "name"), is_display_id(attr_of(a, "id")), m.group(0), dep])
        rest = SCREEN_BLOCK.sub("", t)
        main_refs |= refs_in(rest, dep)

    def panels(body):
        return set(re.findall(r'<panel\b[^>]*\bname="([^"]+)"', body))

    changed = True
    while changed:
        changed = False
        from_disp = set().union(*[panels(s[2]) for s in screens if s[1]])
        from_main = set().union(*[panels(s[2]) for s in screens if not s[1]])
        for s in screens:
            if not s[1] and s[0] in from_disp and s[0] not in from_main:
                s[1] = True
                changed = True
    names = {s[0] for s in screens if s[1] and s[0]}
    for s in screens:
        (disp_refs if s[1] else main_refs).update(refs_in(s[2], s[3]))
    return whole, names, disp_refs - main_refs


# ---------------------------------------------------------------------------
def convert_skin(name, folders, out_dir, rename_overrides, include_extras=True):
    if name not in folders:
        raise SystemExit(f"Skin '{name}' not found. Available: {', '.join(sorted(folders))}")
    src = folders[name]
    reason = why_not_convertible(src)
    if reason:
        raise SystemExit(f"can't convert '{name}': {reason}")
    xres = skin_size(src)[0]
    factor = Fraction(TARGET_W, xres)

    # dependency closure
    deps, partial, queue = {}, collections.defaultdict(set), [name]
    while queue:
        cur = queue.pop()
        if cur in deps:
            continue
        deps[cur] = folders[cur]
        for ref, paths in find_refs(folders[cur], folders).items():
            if ref == cur:
                continue
            other_skin = os.path.exists(os.path.join(folders[ref], "skin.xml"))
            other_res = os.path.dirname(folders[ref]) != os.path.dirname(src)
            if ref != name and (other_skin or other_res):
                partial[ref] |= paths  # another skin's folder: only borrow the files actually used
                deps.setdefault(ref, folders[ref])
            else:
                queue.append(ref)
    renames = {d: rename_overrides.get(d, default_new_name(d)) for d in deps}
    for d, n in renames.items():
        if n in folders:
            raise SystemExit(f"New name '{n}' for '{d}' clashes with an existing skin folder; use --rename {d}=OtherName")

    dep_xmls = [(d, p) for d, dp in deps.items() if d not in partial for p in xml_files(dp)]
    display_files, display_names, display_imgs = analyse_display(dep_xmls, name, set(deps))
    conv = Converter(factor, renames, display_names)
    new_name = renames[name]
    stage = os.path.join(out_dir, new_name)
    if os.path.exists(stage):
        shutil.rmtree(stage)
    root_out = os.path.join(stage, "usr", "share", "enigma2")
    counts = collections.Counter()
    missing, unreadable = [], []

    for dep, dep_src in deps.items():
        dst_root = os.path.join(root_out, renames[dep])
        if dep in partial:
            files = sorted(p for p in partial[dep] if p and not p.endswith("/"))
        else:
            files = walk_files(dep_src)
        for rel in files:
            s, d = os.path.join(dep_src, rel), os.path.join(dst_root, rel)
            base = os.path.basename(rel)
            if any(fnmatch.fnmatch(base, p) for p in SKIP_FILES):
                continue
            if rel.split(os.sep)[0] == "meta":  # package metadata installs to /usr/share/meta, handled below
                continue
            if not os.path.isfile(s) or not inside_allowed(s):
                missing.append(f"{dep}/{rel}")
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            low = rel.lower()
            rel_web = rel.replace(os.sep, "/")
            if low.endswith(".xml") and s in display_files:
                text = open(s, "rb").read().decode("utf-8", errors="replace")
                open(d, "wb").write(conv.rename_refs(text).encode("utf-8"))  # front-panel skin: paths only
                counts["display xml (unscaled)"] += 1
            elif low.endswith(".xml"):
                text = open(s, "rb").read().decode("utf-8", errors="replace")
                open(d, "wb").write(conv.convert_xml(text).encode("utf-8"))
                counts["xml"] += 1
            elif (dep, rel_web) in display_imgs:
                shutil.copy2(s, d)
                counts["front-panel images (unscaled)"] += 1
            elif low.endswith((".png", ".jpg", ".jpeg")):
                try:
                    conv.convert_image(s, d)
                    counts["images"] += 1
                except Exception as e:  # empty/corrupt file: keep it as it is
                    shutil.copy2(s, d)
                    unreadable.append(f"{dep}/{rel_web}  ({type(e).__name__})")
            else:
                shutil.copy2(s, d)
                counts["copied"] += 1

    # plugin-style repos: usr/share/enigma2/<skin> next to usr/lib/enigma2 (renderers,
    # converters) and usr/share/fonts -> carried along unchanged so the skin still works
    extras = []
    tail = os.path.join("usr", "share", "enigma2", os.path.basename(src))
    if include_extras and os.path.normpath(src).endswith(tail):
        prefix = os.path.normpath(src)[:-len(tail)]
        for sub in (os.path.join("usr", "lib", "enigma2"), os.path.join("usr", "share", "fonts")):
            base = os.path.join(prefix, sub)
            if not os.path.isdir(base):
                continue
            for r in walk_files(base):
                s = os.path.join(base, r)
                rel = os.path.relpath(s, prefix)
                d = os.path.join(stage, rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                extras.append(rel.replace(os.sep, "/"))
        if extras:
            counts["plugin/font files (unchanged)"] = len(extras)

    # package metadata (src/skins/<res>/<skin>/meta/skin_<skin>.xml installs to /usr/share/meta)
    meta_src = os.path.join(src, "meta", f"skin_{name}.xml")
    if os.path.isfile(meta_src):
        m = open(meta_src, encoding="utf-8").read()
        m = m.replace(f"<name>{name}</name>", f"<name>{new_name}</name>")
        def pkg(x):
            old = x.group(2)
            new = old.replace("1080", "1440").replace("fhd", "wqhd")
            return x.group(1) + (new if new != old else old + "-wqhd")
        m = re.sub(r"(<(?:packagename>|file type=\"package\" name=\"))([^<\"]+)", pkg, m)
        m = re.sub(r"\b(1080|720|FHD|HD) Skin\b", "1440 Skin", m)
        meta_dst = os.path.join(stage, "usr", "share", "meta", f"skin_{new_name}.xml")
        os.makedirs(os.path.dirname(meta_dst), exist_ok=True)
        open(meta_dst, "w", encoding="utf-8").write(m)

    # note the conversion in skin.xml
    skin_xml = os.path.join(root_out, new_name, "skin.xml")
    t = open(skin_xml, encoding="utf-8").read()
    t = re.sub(r"(<skin[^>]*>)", r"\1\n\t<!-- WQHD (2560x1440) conversion of " + name +
               f" by wqhd_convert.py: geometry, fonts and graphics scaled x{factor} -->", t, count=1)
    open(skin_xml, "w", encoding="utf-8").write(t)

    # report
    lines = [f"{name} -> {new_name}  (source {xres} wide, scale x{factor} = {float(factor):.4f})", ""]
    lines.append("Folders written to /usr/share/enigma2:")
    for dep in deps:
        how = "only the files it uses" if dep in partial else "full folder"
        lines.append(f"  {dep} -> {renames[dep]}  ({how})")
    lines += ["", f"Files: {dict(counts)}", ""]
    if display_files or display_imgs or conv.log.get("front-panel screens left at their own size"):
        lines.append("Front-panel (LCD/OLED) parts left at their own size:")
        lines += [f"  file {os.path.relpath(p, src)}" for p in sorted(display_files)]
        lines.append(f"  {conv.log.get('front-panel screens left at their own size', 0)} front-panel screens,"
                     f" {len(display_imgs)} images used only by them")
        lines.append("")
    if extras:
        py = [e for e in extras if e.endswith((".py", ".pyc", ".pyo", ".so"))]
        lines.append(f"Plugin and font files copied unchanged ({len(extras)}): renderers/converters are"
                     " NOT rescaled - if one draws at fixed pixel sizes it will look small:")
        lines += [f"  {e}" for e in py[:40]] + (["  ..."] if len(py) > 40 else []) + [""]
    if unreadable:
        lines.append("Images that couldn't be read (copied unchanged - check them):")
        lines += [f"  {u}" for u in unreadable] + [""]
    if missing:
        lines.append("Referenced files not found in the repo (left out):")
        lines += [f"  {m}" for m in missing] + [""]
    if conv.unknown:
        lines.append("Numeric attributes NOT scaled (unknown meaning - check these screens):")
        lines += [f"  {k}  x{v}" for k, v in sorted(conv.unknown.items())] + [""]
    if conv.left_expr:
        lines.append("Expressions left as-is (they use f/w/h and rescale themselves):")
        lines += [f"  {k}  x{v}" for k, v in sorted(conv.left_expr.items())] + [""]
    lines.append("Notes:")
    lines += [f"  {k}  x{v}" for k, v in sorted(conv.log.items()) if not k.startswith("expr ")]
    lines.append("  Layout expressions converted:")
    lines += [f"    {k[5:]}  x{v}" for k, v in sorted(conv.log.items()) if k.startswith("expr ")]
    report = "\n".join(lines) + "\n"
    open(os.path.join(out_dir, f"{new_name}-report.txt"), "w", encoding="utf-8").write(report)
    open(os.path.join(root_out, new_name, "CONVERSION-REPORT.txt"), "w", encoding="utf-8").write(report)
    # zip
    zpath = os.path.join(out_dir, f"{new_name}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(stage):
            for f in sorted(fs):
                p = os.path.join(r, f)
                z.write(p, os.path.relpath(p, stage))

    print(f"{name} -> {zpath}  {dict(counts)}  unknown={sum(conv.unknown.values())}")
    return zpath


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--skins-root", help="an OpenViX/skins src/skins folder (contains 1080/ and 720/)")
    src.add_argument("--repo-root", help="any checkout: skins are found wherever a folder holds a skin.xml")
    ap.add_argument("--checkout-root", help="with --repo-root pointing inside a checkout: the checkout itself"
                    " (symlinks may point anywhere inside it; default: --repo-root)")
    ap.add_argument("--repo-name", help="name for a skin whose skin.xml sits at the repo root (default: folder name)")
    ap.add_argument("--skins", required=True, help="comma separated skin folder names, or 'all'")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                    help="override an output folder name (repeatable)")
    ap.add_argument("--no-extras", action="store_true",
                    help="don't copy a repo's usr/lib/enigma2 and usr/share/fonts files alongside the skin")
    ap.add_argument("--list", action="store_true",
                    help="only check the names and print them as a JSON list (used by the workflow)")
    a = ap.parse_args(argv)
    global ALLOWED_ROOT
    ALLOWED_ROOT = os.path.realpath(a.checkout_root or a.skins_root or a.repo_root)
    overrides = dict(r.split("=", 1) for r in a.rename)
    if a.skins_root:
        folders = index_folders(a.skins_root)
        skins = {n: p for n, p in folders.items() if os.path.isfile(os.path.join(p, "skin.xml"))}
        dups = []
    else:
        folders, skins, dups = discover_repo(a.repo_root, a.repo_name)
    for d in dups:
        print(f"Note: a second skin folder with the same name was ignored: {d}", file=sys.stderr)
    reasons = {n: why_not_convertible(p) for n, p in skins.items()}
    if "Default1080" in reasons:
        reasons["Default1080"] = "it only wraps the image's built-in skin_default.xml"
    convertible = [n for n in skins if not reasons[n]]
    if a.skins.strip().lower() == "all":
        if a.skins_root:
            names = [n for n in convertible if os.path.basename(os.path.dirname(folders[n])) == "1080"]
        else:
            names = convertible
        if not names:
            print("ERROR: no convertible skins found.", file=sys.stderr)
            for n, r in sorted(reasons.items()):
                print(f"  {n}: {r}", file=sys.stderr)
            if not skins:
                print("  (no folder in this repository contains a skin.xml)", file=sys.stderr)
            sys.exit(2)
    else:
        names = list(dict.fromkeys(s.strip().strip("/") for s in re.split(r"[,\s]+", a.skins) if s.strip()))
    bad = [n for n in names if n not in convertible]
    if bad:
        for n in bad:
            if n in reasons:
                why = reasons[n]
            elif n in folders:
                why = "it is a shared folder used by other skins, not a skin"
            else:
                why = "no skin folder with that name" + (" in 1080/ or 720/" if a.skins_root else " in this repository")
            print(f"ERROR: can't convert '{n}': {why}", file=sys.stderr)
        print("Convertible skins: " + (", ".join(sorted(convertible, key=str.lower)) or "none"), file=sys.stderr)
        sys.exit(2)
    if a.list:
        import json
        print(json.dumps(names))
        return
    os.makedirs(a.out, exist_ok=True)
    failed = []
    for n in names:
        try:
            zpath = convert_skin(n, folders, a.out, overrides, include_extras=not a.no_extras)
        except SystemExit as e:
            print(f"ERROR {n}: {e}", file=sys.stderr)
            failed.append(n)
            continue
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as gh:
                gh.write(f"new_name={os.path.basename(zpath)[:-4]}\n")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
