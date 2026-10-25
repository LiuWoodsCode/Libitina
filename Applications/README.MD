This folder contains applications that will be installed onto Libitina devices. This folder contains the following subfolders:

* Internal
    * These apps are used for the development of Libitina
    * These should not be included in production builds
* Production
    * Apps users will interact with
    * Examples include a notepad, browser, settings, compass

Each of those folders contains these folders:

* Global
    * App is intended for all enviorments
* Phosh
    * App is intended only for Phosh
* Yui 
    * App is only intended for YuiHirasawa

Inside each is folders for each app. Each app contains a app.desktop file. These are normal desktop files, except for the fact that the Exec field is missing. All apps will include a launch.sh file that should be ran with the cwd of the app's directory. You will need to correct this (make sure you specify exact path and chmod +x the sh file) for this (and include the working directory field) when creating .desktop entries for apps in your installer.

Treat the folder name like an app ID, so for example if the folder name is "ChatClient" make sure you name your apps desktop files are "libitina-ChatClient.desktop" and such. This is so we can update applications without leaving duplicates.

Run `python3 Applications/install.py` from the repository root to install or
update applications for the current user. To install only Yui applications, use
`python3 Applications/install.py --environment Yui`. The installer bundles
`YuiHirasawa/displays.py` and `YuiHirasawa/settings.py` with YuiSettings so its installed desktop entry works
without the repository on Python's module search path. Rerun the installer after
updating the shared backend.
See [YuiSettings/README.md](Production/Yui/YuiSettings/README.md) for its Linux
service dependencies and available settings.
