# WQHD skin convertor

Converts Enigma2 skins from any repository to 2560x1440 (WQHD), using GitHub Actions.
Paste a link, press a button, and download an installable zip.

## Convert a skin

1. Open the **Actions** tab, pick **Convert a skin from a link**, then **Run workflow**.
2. Fill in:
   - **link** - the skin's repository, or a folder in it. Any of these work:
     - `https://github.com/littlesat/skin-PLiHD`
     - `littlesat/skin-PLiHD`
     - `https://github.com/Taapat/skin-SimpleGray/tree/master/usr/share/enigma2/SimpleGray-FHD` (just that skin)
     - `https://github.com/OpenViX/skins/archive/refs/heads/master.zip`
     - GitLab, Bitbucket, Codeberg or any other `https` git link
   - **branch** - leave empty for the default branch (or the one in the link). A tag or commit also works.
   - **skins** - `all`, or folder names separated by commas, e.g. `PLi-FullHD, PLi-FullNightHD`.
   - **release** - on: publishes one zip per skin under **Releases** (kept until you delete it).
   - **save_to_repo** - on: also commits the converted files to `converted/<owner>-<repo>/` here.
3. When the run finishes, download the zip from the release (or from **Artifacts** at the
   bottom of the run page - those expire after 90 days).

If a skin can't be converted, the **Download and check** step says why and lists the skins
that can be.

## Install on the box

Unzip into `/` on the box (for example copy the zip to `/tmp` and run
`unzip -o /tmp/PLi-FullHD-1440.zip -d /`), then restart the GUI and choose the skin in
**Menu > Setup > User Interface > Skin setup**. Your box must be set to a 1440p video mode.

## What gets converted

- Skins whose `skin.xml` is 1920x1080 (scaled x4/3) or 1280x720 (scaled x2).
  720x576 (SD) and other non-16:9 skins are skipped, as are skins that are already 2560 wide.
- Every folder holding a `skin.xml` is found, wherever it sits in the repository.
  Shared folders next to it that the skin uses are converted too.
- Positions, sizes, fonts, list templates and PNG/JPG graphics are scaled. SVGs are copied as
  they are (they scale themselves).
- Front-panel (LCD/OLED) screens and their graphics are left at their own size.
- Renderers, converters and fonts the repository ships in `usr/lib/enigma2` and
  `usr/share/fonts` are copied unchanged. Python code is **not** rescaled: if a renderer draws at
  fixed pixel sizes, that part will look small.
- The converted folder gets a new name (`FHD` becomes `WQHD`, `1080` becomes `1440`, otherwise
  `-1440` is added), so it installs next to the original.

Each skin folder contains a `CONVERSION-REPORT.txt` listing anything worth checking: numbers
the converter didn't recognise, images it couldn't read, missing files and plugin code.

Skins that rebuild their own `skin.xml` from a settings plugin (MetrixHD with MyMetrixLite, for
example) will overwrite the converted file when you save settings in that plugin.

## Private repositories

Create a token that can read the repository, then add it here under **Settings > Secrets and
variables > Actions > New repository secret** with the name `SKIN_REPO_TOKEN`. Leave it out for
public repositories.

## Run it on your own computer

```
pip install pillow
python3 fetch_source.py https://github.com/littlesat/skin-PLiHD --dest src
python3 wqhd_convert.py --repo-root src --skins all --out dist
```

`python3 wqhd_convert.py --help` lists all options (`--rename OLD=NEW` to choose the output
folder name, `--no-extras` to leave out plugin files).

## Files

| File | What it does |
| --- | --- |
| `.github/workflows/convert-from-link.yml` | the workflow |
| `fetch_source.py` | understands the link and downloads the repository |
| `wqhd_convert.py` | the converter (the same one used for OpenViX skins in Vix_skins) |
