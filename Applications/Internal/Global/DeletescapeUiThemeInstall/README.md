# Deletescape UI Theme Installer

Run `./launch.sh` on Linux with Python 3, PyGObject and GTK 3.24 installed
(Debian: `python3-gi gir1.2-gtk-3.0`; Alpine: `py3-gobject3 gtk+3.0`).
The app has one button to install or update both themes for the current user,
without root privileges. The repository's `Applications/install.py` automatically
includes this app and all bundled theme assets.

Themes are installed in `$XDG_DATA_HOME/themes` (default `~/.local/share/themes`).
Select `DeletescapeUi` (light) or `DeletescapeUi-Dark` in your desktop's appearance
settings after installation. Installation does not change your selected theme or
GTK configuration. Existing installations of these two themes are replaced.
The light theme also provides `gtk-dark.css` for apps requesting a dark variant.
For a one-off preview: `GTK_THEME=DeletescapeUi-Dark gtk3-demo`.

The themes translate the named `LIGHT_PALETTE` and `DARK_PALETTE` and widget
styles from `dele2/dsui_qss_gen.py`: #FF00AA accents, flat surfaces, thin borders,
2px buttons/inputs, 4px cards, rounded sliders and switches. They import GTK's
bundled Adwaita CSS for standard widget layout and indicator assets, then apply
the Deletescape appearance. Qt custom animations are not ported. GTK4/libadwaita
and shell themes are outside this GTK3 theme's scope.

To regenerate the committed theme files after editing the palette or rules:
`python3 generate_themes.py`. No Qt or external source checkout is needed.

GTK theme lookup and dark variants follow the
[GTK3 CssProvider documentation](https://docs.gtk.org/gtk3/class.CssProvider.html).
