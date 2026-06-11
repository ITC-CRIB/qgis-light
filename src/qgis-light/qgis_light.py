import os.path
import json
import psycopg2
import psycopg2.extras

from qgis.core import Qgis, QgsApplication, QgsAuthMethodConfig, QgsProject, QgsSettings
from qgis.gui import QgisInterface, QgsGui
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDockWidget,
    QMenu,
    QToolBar,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from processing import execAlgorithmDialog


class QGISLightPlugin:
    """QGIS Light plugin class."""

    # Message levels
    _message_levels = {
        "info": Qgis.MessageLevel.Info,
        "warning": Qgis.MessageLevel.Warning,
        "error": Qgis.MessageLevel.Critical,
    }

    # Toolbar areas
    _toolbar_areas = {
        "top": Qt.ToolBarArea.TopToolBarArea,
        "bottom": Qt.ToolBarArea.BottomToolBarArea,
        "left": Qt.ToolBarArea.LeftToolBarArea,
        "right": Qt.ToolBarArea.RightToolBarArea,
    }

    # Panel areas
    _panel_areas = {
        "top": Qt.DockWidgetArea.TopDockWidgetArea,
        "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
        "left": Qt.DockWidgetArea.LeftDockWidgetArea,
        "right": Qt.DockWidgetArea.RightDockWidgetArea,
    }

    def __init__(self, iface: QgisInterface):
        """Initializes the plugin.

        Args:
            iface (QgisInterface): QGIS interface object.
        """
        # Set interface
        self.iface = iface

        # Get main window
        self.mainwindow = iface.mainWindow()

        # Get settings
        self.settings = QgsSettings()

        # Get plugin directory
        self.plugin_dir = os.path.dirname(os.path.realpath(__file__))
        self.log(f"Plugin directory is {self.plugin_dir}.")

        # Defaults
        self.user = None
        self.matched_roles = []

        # Default config
        self.standard_config_path = os.path.join(self.plugin_dir, "config.json")

        # JSON paths
        connections_path = os.path.join(self.plugin_dir, "connections.json")

        roles_path = os.path.join(self.plugin_dir, "roles.json")

        users_path = os.path.join(self.plugin_dir, "users.json")

        projects_path = os.path.join(self.plugin_dir, "projects.json")

        # Load mapping JSONs
        self.connection_cfg = self._load_json(
            connections_path, label="Verbindungsparameter"
        )

        self.roles_cfg = self._load_json(roles_path, label="DB Role Mapping")

        self.users_cfg = self._load_json(users_path, label="User Mapping")

        self.projects_cfg = self._load_json(projects_path, label="Project Mapping")

        # Resolve config
        config_path = self.resolve_config()

        if config_path == self.standard_config_path:
            self.log("No specific mapping found – using default configuration.")

        # active config path
        self.config_path = config_path

        # active config
        self.config = {}

        # load config
        self.apply_config(config_path)

    def resolve_config(self) -> str:
        """Resolve active config path."""

        standard_config_path = os.path.join(self.plugin_dir, "config.json")

        config_path = standard_config_path

        # 1 user
        resolved = self._resolve_config_by_user(self.users_cfg)

        if resolved:
            return resolved

        # 2 role
        if self.connection_cfg and self.roles_cfg:
            resolved = self._resolve_config_by_role(
                self.connection_cfg, self.roles_cfg, fallback_path=None
            )

            if resolved:
                return resolved

        # 3 project
        if self.projects_cfg:
            resolved = self._resolve_config_by_project(self.projects_cfg)

            if resolved:
                return resolved

        return config_path

    def _load_json(self, path: str, label: str = "JSON") -> dict | None:
        """Loads a JSON file and returns its content.

        Returns None if the file does not exist or is invalid.

        Args:
            path: File path.
            label: Label used for log messages.
        """

        if not os.path.isfile(path):
            self.log(f"Couldn't find {label}: {path}", "warning")
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log(f"Couldn't load {label} ({path}): {e}", "error")
            return None

    def _get_auth_username(self) -> str | None:
        """Reads the username from the QGIS Auth Manager.

        Returns:
            Username or None if no auth entry exists.
        """

        try:
            manager = QgsApplication.authManager()
            auth_method_cfg = QgsAuthMethodConfig()
            auth_ids = manager.availableAuthMethodConfigs()
            if not auth_ids:
                return None
            auth_id = list(auth_ids.keys())[0]
            manager.loadAuthenticationConfig(auth_id, auth_method_cfg, True)
            return auth_method_cfg.configMap().get("username")
        except Exception as e:
            self.log(
                f"Username could not be read from the Auth Manager: {e}", "warning"
            )
            return None

    def _resolve_config_by_role(
        self, connection_cfg: dict, roles_cfg: dict, fallback_path: str | None
    ) -> str | None:
        """
        Checks the database roles of the current user and returns the corresponding config path.

        Args:
            connection_cfg: Connection parameters from connections.json.
            roles_cfg: Role mapping from roles.json.
            fallback_path: Return value if no role matches (None = no fallback).

        Returns:
            Config path or fallback_path.
        """

        try:
            # Retrieve auth credentials from the QGIS Auth Manager
            manager = QgsApplication.authManager()
            auth_ids = manager.availableAuthMethodConfigs()
            if not auth_ids:
                self.log(
                    "No authentication stored in QGIS – role check skipped.", "warning"
                )
                return fallback_path

            auth_id = list(auth_ids.keys())[0]
            auth_method_cfg = QgsAuthMethodConfig()
            manager.loadAuthenticationConfig(auth_id, auth_method_cfg, True)
            user = auth_method_cfg.configMap().get("username")
            password = auth_method_cfg.configMap().get("password")
            self.user = user

            # connection parameters
            host = connection_cfg.get("host")
            port = int(connection_cfg.get("port", 5432))
            dbname = connection_cfg.get("dbname")

            if not host or not port or not dbname:
                self.log(
                    "Verbindungsparameter unvollständig – Rollenprüfung übersprungen.",
                    "warning",
                )
                return fallback_path

            # roles
            role_entries: list[dict] = roles_cfg.get("roles", [])
            role_names = [
                entry["rolname"] for entry in role_entries if "rolname" in entry
            ]

            if not role_names:
                self.log(
                    "Keine Rollen in roles.json definiert – Rollenprüfung übersprungen.",
                    "warning",
                )
                return fallback_path

            # prepare SQL
            sql = """
                SELECT r1.rolname
                FROM pg_roles r
                JOIN pg_auth_members m ON m.member = r.oid
                JOIN pg_roles r1 ON m.roleid = r1.oid
                WHERE r.rolname = %s
                AND r1.rolname = ANY(%s)
            """

            # DB-connection with psycopg2
            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname, user=user, password=password
            )
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(sql, (user, role_names))
            rows = cur.fetchall()
            conn.close()

            self.matched_roles = [row["rolname"] for row in rows]

            if not self.matched_roles:
                self.log(f"User '{user}' does not have any of the configured roles.")
                return fallback_path

            self.log(f"User '{user}' – found roles: {self.matched_roles}")

            # First matching role → determine config path (priority = order in roles.json)

            for entry in role_entries:
                if entry.get("rolname") in self.matched_roles:
                    cfg_path = entry.get("config_path")
                    if cfg_path and os.path.isfile(cfg_path):
                        self.log(f"Role-based config found: {cfg_path}")
                        return cfg_path
                    else:
                        self.log(
                            f"Config path for role '{entry['rolname']}' not found: {cfg_path}",
                            "warning",
                        )

            self.log(
                "No valid role-based config – continuing with next step.", "warning"
            )
            return fallback_path

        except Exception as e:
            import traceback

            self.log(f"Role check failed: {e}", "error")
            self.log(traceback.format_exc(), "error")
            return fallback_path

    def _resolve_config_by_user(self, users_cfg: dict) -> str | None:
        """Returns config path based on users.json."""

        if not users_cfg:
            return None

        if not self.user:
            self.user = self._get_auth_username()

        if not self.user:
            self.log("No username available for users.json.", "warning")
            return None

        user_entries = users_cfg.get("users", [])

        for entry in user_entries:
            usernames = entry.get("usernames", [])

            if self.user in usernames:
                cfg_path = entry.get("config_path")
                if cfg_path and os.path.isfile(cfg_path):
                    self.log(f"User-based config found: {cfg_path}")
                    return cfg_path
                else:
                    self.log(
                        f"Config for user '{self.user}' is invalid: {cfg_path}",
                        "warning",
                    )

        self.log(f"No entry for user '{self.user}' in users.json.")
        return None

    def _resolve_config_by_project(self, projects_cfg: dict) -> str | None:
        """Returns config path based on current QGIS project."""

        if not projects_cfg:
            return None

        try:
            project = QgsProject.instance()

            project_path = project.fileName()

            if not project_path:
                self.log("No QGIS project loaded.")
                return None

            project_name = os.path.basename(project_path)

            self.log(f"Current project: {project_name}")

            project_entries = projects_cfg.get("projects", [])

            for entry in project_entries:
                names = entry.get("project_names", [])

                if project_name in names:
                    cfg_path = entry.get("config_path")

                    if cfg_path and os.path.isfile(cfg_path):
                        self.log(f"Project-based config found: {cfg_path}")
                        return cfg_path

                    else:
                        self.log(
                            f"Invalid config for project '{project_name}': {cfg_path}",
                            "warning",
                        )

            self.log(f"No project mapping found for '{project_name}'.")
            return None

        except Exception as e:
            self.log(f"Project mapping failed: {e}", "error")
            return None

    def apply_config(self, config_path: str):
        """Loads config file."""

        self.config = {}

        try:
            with open(config_path, encoding="utf-8") as f:
                self.config = json.load(f)

            self.config_path = config_path

            self.log(f"Configuration loaded from {config_path}")

        except Exception as e:
            self.log(f"Couldn't load config file ({config_path}): {e}", "error")

    def check_project_config(self, *args):
        """Re-resolve config after project change."""

        new_config = self.resolve_config()

        # already active
        if new_config == self.config_path:
            self.log("Correct config already active.")
            return

        self.log(f"Switching config to: {new_config}")

        enabled = self.settings.value("qgislight/enabled") == "true"

        # disable current UI
        if enabled:
            self.disable(store=False)

        # load new config
        self.apply_config(new_config)

        # enable again
        if enabled:
            self.enable(store=False)

    def log(self, message: str, level: str = "info"):
        """Logs a message to the log panel.

        Args:
            message (str): Log message.
            level (str): Level of the message (default = "info").
        """
        QgsApplication.messageLog().logMessage(
            message, "QGIS Light", self._message_levels.get(level, "info")
        )

    def message(self, message: str, level: str = "info"):
        """Displays a message in the message bar.

        Args:
            message (str): Message.
            level (str): Level of the message (default = "info")
        """
        self.iface.messageBar().pushMessage(
            "QGIS Light", message, self._message_levels.get(level, "info")
        )

    @staticmethod
    def associatedObjects(action: QAction) -> list:
        """Returns objects associated with an action.

        Bridges a Qt version difference: QAction.associatedWidgets() was
        renamed to associatedObjects() when QAction moved to QtGui in Qt6.

        Args:
            action (QAction): Action object.

        Returns:
            List of associated objects.
        """
        if hasattr(action, "associatedObjects"):
            return action.associatedObjects()
        return action.associatedWidgets()

    @staticmethod
    def enumValue(enum):
        """Returns value of a Qt enum type."""
        if enum is None:
            return None
        elif hasattr(enum, "value"):
            return enum.value
        else:
            return int(enum)

    def getProviders(self, name: bool = False) -> list[str]:
        """Returns list of processing providers.

        Args:
            name (bool): Set True to get provider names.

        Returns:
            List of processing providers.
        """
        return [
            provider.name() if name else provider.id()
            for provider in QgsApplication.processingRegistry().providers()
        ]

    def getAlgorithms(self) -> list:
        """Returns list of processing algorithms.

        Returns:
            List of processing algorithms (id, group, name).
        """
        algorithms = []

        for provider in QgsApplication.processingRegistry().providers():
            for algorithm in provider.algorithms():
                algorithms.append(
                    {
                        "id": algorithm.id(),
                        "group": algorithm.group(),
                        "name": algorithm.displayName(),
                    }
                )

        return algorithms

    def getDataItemProviders(self) -> list:
        """Returns list of data item providers.

        Returns:
            List of data item providers.
        """
        return [
            provider.name()
            for provider in QgsApplication.dataItemProviderRegistry().providers()
        ]

    def getDataSourceProviders(self) -> list:
        """Returns list of data source providers.

        Returns:
            List of data source providers.
        """
        return [
            provider.name()
            for provider in QgsGui.sourceSelectProviderRegistry().providers()
        ]

    def findAction(self, widget: QWidget, id: str) -> QAction:
        """Finds action with the specified identifier.

        Object name, text, and tooltip are checked as identifiers.

        Args:
            widget (QWidget): Associated widget object.
            id (str): Action identifier.

        Returns:
            Action object if found, None otherwise.
        """
        for action in widget.actions():
            if isinstance(action, QWidgetAction):
                action = self.findAction(action.defaultWidget(), id)

            elif id in [action.objectName(), action.text(), action.toolTip()]:
                pass

            elif action.menu():
                action = self.findAction(action.menu(), id)

            else:
                continue

            if action:
                return action

    def getItems(self, token: str) -> list:
        """Returns objects indicated by the identifier token.

        Token format is <parent>:<identifier>*?.
        Example: mFileToolBar:mActionNewProject.
        See `config.json` for the actual use cases.

        Args:
            token (str): Identifier token.

        Returns:
            List of objects identified by the token.
        """
        algorithm = QgsApplication.processingRegistry().algorithmById(token)
        if algorithm:
            action = QAction(self.mainwindow)
            action.setIcon(algorithm.icon())
            action.setText(algorithm.displayName())
            action.triggered.connect(lambda: execAlgorithmDialog(token))
            return [action]

        if token == "mActionDisableQGISLight":
            action = QAction(self.mainwindow)
            action.setObjectName("mActionDisableQGISLight")
            action.setIcon(QIcon(os.path.join(self.plugin_dir, "icons/qgis.svg")))
            action.setText("Disable QGIS Light")
            action.triggered.connect(lambda: self.disable(store=True))
            return [action]

        parent_name, name = token.split(":", 1)

        if parent_name == "section":
            action = QAction(self.mainwindow)
            action.setText(name)
            action.setSeparator(True)
            return [action]

        if parent_name == "algorithms":
            algorithms = self.config["algorithms"][name]

            menu = QMenu(self.mainwindow)
            self.addItems(menu, algorithms["items"])

            toolbutton = QToolButton(self.mainwindow)
            toolbutton.setIcon(QIcon(algorithms["icon"]))
            toolbutton.setMenu(menu)
            toolbutton.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

            return [toolbutton]

        parent = self.mainwindow.findChild(QWidget, parent_name)
        if not parent:
            self.log(f"Invalid parent object name {parent_name}.", "warning")
            return []

        wildcard = name[-1] == "*"
        if wildcard:
            name = name[:-1]

        if not name:
            return parent.actions()

        action = self.findAction(parent, name)
        if action:
            if not wildcard:
                return [action]

            for widget in self.associatedObjects(action):
                if isinstance(widget, QToolButton):
                    return [widget.menu()] if widget.menu() else widget.actions()

            for widget in self.associatedObjects(action):
                if isinstance(widget, QMenu):
                    return [widget]

        self.log(f"Invalid identifier token {token}.")
        return []

    def addItems(self, parent: QWidget, items: list):
        """Adds items to the associated parent object.

        Args:
            parent (QWidget): Parent object.
            items (list): List of items.
        """
        for item in items:
            if item == "separator":
                parent.addSeparator()

            elif isinstance(item, str):
                self.addItems(parent, self.getItems(item))

            elif isinstance(item, list):
                menu = QMenu()
                self.addItems(menu, item)
                self.addItems(parent, [menu])

            elif isinstance(item, QAction):
                parent.addAction(item)

            elif isinstance(item, QMenu) and item.actions():
                if isinstance(parent, QMenu):
                    group = None
                    for action in item.actions():
                        parent.addAction(action)
                        if action.actionGroup():
                            if not group:
                                group = action.actionGroup()
                            else:
                                action.setActionGroup(group)
                else:
                    toolbutton = QToolButton(self.mainwindow)
                    toolbutton.setMenu(item)
                    toolbutton.setPopupMode(
                        QToolButton.ToolButtonPopupMode.MenuButtonPopup
                    )
                    toolbutton.setDefaultAction(item.actions()[0])
                    item.triggered.connect(toolbutton.setDefaultAction)
                    parent.addWidget(toolbutton)

            elif isinstance(item, QWidget):
                parent.addWidget(item)

            else:
                self.log(f"Invalid item {item}.", "warning")

    def restoreLayout(self):
        """Restores layout of the user interface.

        Toolbars and panels are restored to the layout that was stored before
        the simplifications were enabled.
        """
        self.log("Restoring user interface layout.")

        # Restore toolbars
        items = self.settings.value("qgislight/toolbars", [])
        for item in items:
            toolbar = self.mainwindow.findChild(QToolBar, item["name"])
            if not toolbar:
                self.log(f"Toolbar {item['name']} not found.", "warning")
                continue

            area = Qt.ToolBarArea(item["area"])
            if self.mainwindow.toolBarArea(toolbar) != area:
                self.mainwindow.addToolBar(area, toolbar)

            toolbar.show()
            self.log(f"Toolbar {item['name']} is visible.")

        # Restore panels
        items = self.settings.value("qgislight/panels", [])
        for item in items:
            panel = self.mainwindow.findChild(QDockWidget, item["name"])
            if not panel:
                self.log(f"Panel {item['name']} not found.", "warning")
                continue

            area = Qt.DockWidgetArea(item["area"])
            if self.mainwindow.dockWidgetArea(panel) != area:
                self.mainwindow.addDockWidget(area, panel)

            panel.setFeatures(QDockWidget.DockWidgetFeature(item["features"]))

            if item["hidden"]:
                panel.hide()
            else:
                panel.show()

    def disable(self, store: bool = False):
        """Disables simplifications.

        Args:
            store (bool): Set True to store enabled flag (default = False).
        """
        self.log("Disabling simplifications.")

        # Clear enabled flag if required
        if store:
            self.settings.remove("qgislight/enabled")
            self.settings.sync()

        # Show menu bar
        self.mainwindow.menuBar().show()

        # Enable contextual menu
        self.mainwindow.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        # Remove simplified toolbars
        for name, item in self.config["toolbars"].items():
            toolbar = self.mainwindow.findChild(QToolBar, name)
            if not toolbar:
                self.log(f"Toolbar {name} not found.", "warning")
            elif not isinstance(item, dict):
                toolbar.hide()
                toolbar.setFloatable(True)
                toolbar.setMovable(True)
                toolbar.toggleViewAction().setDisabled(False)
            else:
                self.mainwindow.removeToolBar(toolbar)
                toolbar.deleteLater()
                self.log(f"Toolbar {name} removed.")

        # Restore layout
        self.restoreLayout()

        # Display data source providers message if required
        if self.config.get("providers", {}).get("data_sources"):
            self.message("Restart QGIS to enable removed data sources.")

        # Display data item providers message if required
        if self.config.get("providers", {}).get("data_items"):
            self.message("Restart QGIS to enable removed data items.")

        # Set up status bar
        for name, state in self.config.get("statusbar", {}).items():
            widget = self.mainwindow.findChild(QWidget, name)
            if not widget:
                self.log(f"Widget {name} not found.", "warning")
                continue
            if not state:
                widget.show()

    def enable(self, store: bool = False):
        """Enables simplifications.

        Args:
            store (bool): Set True to store layout (default = False)
        """
        self.log("Enabling simplifications.")

        # Set enabled flag
        self.settings.setValue("qgislight/enabled", "true")
        self.settings.sync()

        # Hide menu bar
        self.mainwindow.menuBar().hide()

        # Disable contextual menu
        self.mainwindow.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # Set up toolbars
        items = []

        for toolbar in self.mainwindow.findChildren(QToolBar):
            if toolbar.parent() == self.mainwindow and not toolbar.isHidden():
                name = toolbar.objectName()
                if isinstance(self.config["toolbars"].get(name), dict):
                    continue
                items.append(
                    {
                        "name": name,
                        "area": self.enumValue(self.mainwindow.toolBarArea(toolbar)),
                    }
                )
                toolbar.hide()
                self.log(f"Toolbar {name} is hidden.")

        if store:
            self.settings.setValue("qgislight/toolbars", items)
            self.settings.sync()

        for name, item in self.config["toolbars"].items():
            toolbar = self.mainwindow.findChild(QToolBar, name)
            if toolbar:
                if not isinstance(item, dict):
                    area = item
                else:
                    self.log(f"Toolbar {name} exists, skipping.")
                    continue
            elif not isinstance(item, dict):
                self.log(f"Toolbar {name} not found.", "warning")
                continue
            else:
                self.log(f"Creating toolbar {name}.")
                toolbar = QToolBar(item["title"], self.mainwindow)
                toolbar.setObjectName(name)
                self.addItems(toolbar, item["items"])
                area = item["area"]
            self.mainwindow.addToolBar(
                self._toolbar_areas.get(area, Qt.ToolBarArea.TopToolBarArea),
                toolbar,
            )
            toolbar.setFloatable(False)
            toolbar.setMovable(False)
            toolbar.toggleViewAction().setDisabled(True)
            toolbar.show()

        # Set up panels
        panels = self.config.get("panels", {})
        items = []

        for panel in self.mainwindow.findChildren(QDockWidget):
            name = panel.objectName()
            items.append(
                {
                    "name": name,
                    "area": self.enumValue(self.mainwindow.dockWidgetArea(panel)),
                    "features": self.enumValue(panel.features()),
                    "hidden": panel.isHidden(),
                }
            )
            if name not in panels and not panel.isHidden():
                panel.hide()
                self.log(f"Panel {name} is hidden.")

        for name in panels:
            panel = self.mainwindow.findChild(QDockWidget, name)
            if not panel:
                self.log(f"Panel {name} not found.", "warning")
                continue
            state, area = panels[name].split(":", 1)
            self.mainwindow.addDockWidget(
                self._panel_areas.get(area, Qt.DockWidgetArea.LeftDockWidgetArea), panel
            )
            if state == "fixed":
                panel.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
                panel.show()
            elif state == "hidden":
                panel.hide()
            self.log(f"Panel {name} is set as {state} at area {area}.")

        if store:
            self.settings.setValue("qgislight/panels", items)
            self.settings.sync()

        # Set up data source manager providers
        providers = self.config.get("providers", {}).get("data_sources", [])
        if providers:
            registry = QgsGui.sourceSelectProviderRegistry()
            for provider in registry.providers():
                if provider.name() not in providers:
                    registry.removeProvider(provider)

        # Set up data item providers
        providers = self.config.get("providers", {}).get("data_items", [])
        if providers:
            registry = QgsApplication.dataItemProviderRegistry()
            for provider in registry.providers():
                if provider.name() not in providers:
                    registry.removeProvider(provider)

        # Set up status bar
        for name, state in self.config.get("statusbar", {}).items():
            widget = self.mainwindow.findChild(QWidget, name)
            if not widget:
                self.log(f"Widget {name} not found.", "warning")
                continue
            if not state:
                widget.hide()

    def refresh(self):
        """Refreshes simplifications."""
        # Check project configuration
        self.check_project_config()

        # Get enabled flag
        enabled = self.settings.value("qgislight/enabled")
        self.log(f"Enabled flag is {enabled}.")

        # Check if simplifications are enabled
        if enabled == "true":
            # Delay enabling until UI restore is finished
            QTimer.singleShot(0, self.enable)

    def initGui(self):
        """Initializes plugin user interface."""
        self.log("Initializing user interface.")

        # Connect to initializationCompleted signal to delay initialization.
        #
        # This is required to have access to the final states of toolbars
        # and panels as modified by loaded plugins.
        self.mainwindow.initializationCompleted.connect(self.refresh)

        # Connect to readProject signal to refresh simplifications
        QgsProject.instance().readProject.connect(self.refresh)

        # Create enable simplifications action
        action = QAction(self.mainwindow)
        action.setObjectName("mActionToggleQGISLight")
        action.setIcon(QIcon(os.path.join(self.plugin_dir, "icons/qgis-green.svg")))
        action.setText("Toggle QGIS Light")
        action.triggered.connect(lambda: self.enable(store=True))

        # Add action to the file toolbar
        self.iface.fileToolBar().addAction(action)

        # Add action to the view menu
        self.iface.viewMenu().addAction(action)

    def unload(self):
        """Unloads plugin."""
        # Disable simplifications if required
        if self.settings.value("qgislight/enabled") == "true":
            self.disable()

        # Remove enable simplifications action if required
        action = self.mainwindow.findChild(QAction, "mActionToggleQGISLight")
        if action:
            for widget in self.associatedObjects(action):
                widget.removeAction(action)
            action.deleteLater()

        try:
            QgsProject.instance().readProject.disconnect(self.refresh)
        except:
            pass
