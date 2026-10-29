# Repo Lever

Install and update **`.rpm` / `.deb`** packages from **GitHub releases**.

**Homepage:** https://github.com/isorropisths/repolever  
**Application ID:** `com.tsolakidis.repolever`

## Build & run (Flatpak)

```sh
flatpak-builder build/ com.tsolakidis.repolever.json --user --force-clean
flatpak-builder --run build/ com.tsolakidis.repolever.json gearlever

# Install locally
flatpak-builder build/ com.tsolakidis.repolever.json --user --install --force-clean
flatpak run com.tsolakidis.repolever
```

Or open the project in **GNOME Builder** and press Run.

## Data locations

After install, config and state live under:

`~/.var/app/com.tsolakidis.repolever/`

(e.g. `config/repo-lever.conf`, `config/repo-lever-packages.json`).

License: GPL-3.0
