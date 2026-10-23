# Project Libitina
Project Libitina is a project to make Linux on mobile phones appeal to regular users while also having options that are performant on low power devices / devices without graphics acceleration.

> [!WARNING]
> You really ahouldn't be using this on your main device right now, as it is very early in development.

# Bases
There are 2 base operating system options available, Nura and Debian.

## Nura
Nura, formerly postmarketOS, is a Linux distro that is made for mobile devices.

## Debian
Debian is a Linux distro.
 
# Tested devices
Currently, this project has only been tested on the LG Google Nexus 5 (hammerhead) running Nura edge and the Phosh shell.

# The 2 interfaces
This project aims to support 2 interface options, Yui and Phosh

## Phosh
From the [Phosh about page](https://phosh.mobi/about/):
> The Phosh project aims to provide a daily-usable, robust and easy to use graphical user environment for mobile devices running mainline Linux. The name is a portmanteau of phone and shell as phosh was one of the first components developed by the project. It hence coined the whole project’s name and is still one of its core components.

Phosh is mostly based on GNOME/GTK (although it is only loosely related to GNOME as a DE) and was created by Purism for the Librem 5. We provide it as a user interface option for Libitina as it's an intuitive mobile shell with active community work and features most people would expect from a mobile device.

## Yui
Yui is a custom mobile shell specifically designed for the extreme low end of devices (e.g devices without DRM support). It is written in python and uses GTK 3 and Layer Shell, and runs on top of the labwc compositor. For most other usecases, use Phosh instead.

The Debian installer adds a **YuiHirasawa** option to the login manager alongside Labwc. Existing installations can register it with `sudo /usr/bin/python3 YuiHirasawa/install-session.py`. Log out and select **YuiHirasawa** to use the temporary Super/Win launcher bindings. User `rc.xml` settings take precedence and are never edited by the wrapper. The session starts `YuiHirasawa/launcher.py`, which starts `main.py` and `notification.py`, restarts either program when it exits, and restarts its launcher worker if it exits. Session termination stops the supervised programs. Pass `--force-legacy` to `YuiHirasawa/install-debian.sh` to also configure this launcher for ordinary Labwc autostart; by default, the installer leaves autostart untouched. A lock prevents duplicate launchers. Rerun the Debian installer with `--force-legacy` to update older autostart entries that start the scripts directly. The session entry points to this checkout, so keep it accessible at the same location; rerun registration after moving it.
