# Project Libitina
Project Libitina is a project to make Linux on mobile phones appeal to regular users while also having options that are performant on low power devices / devices without graphics acceleration.

> [!WARNING]
> You really ahouldn't be using this on your main device right now, as it is very early in development.

# Tested devices
Currently, this project has only been tested on the LG Google Nexus 5 (hammerhead) running postmarketOS edge and the Phosh shell.

# The 2 interfaces
This project aims to support 2 interface options, Yui and Phosh

## Phosh
From the [Phosh about page](https://phosh.mobi/about/):
> The Phosh project aims to provide a daily-usable, robust and easy to use graphical user environment for mobile devices running mainline Linux. The name is a portmanteau of phone and shell as phosh was one of the first components developed by the project. It hence coined the whole project’s name and is still one of its core components.

Phosh is mostly based on GNOME/GTK (although it is only loosely related to GNOME as a DE) and was created by Purism for the Librem 5. We provide it as a user interface option for Libitina as it's an intuitive mobile shell with active community work and features most people would expect from a mobile device.

## Yui
Yui is a custom mobile shell specifically designed for the extreme low end of devices (e.g devices without DRM support). It is written in python and uses GTK 3 and Layer Shell, and runs on top of the labwc compositor. For most other usecases, use Phosh instead.