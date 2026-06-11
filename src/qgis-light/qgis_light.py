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

        # Load custom configuration assets
        self.custom = {
            "connections": self._load_json("connections.json"),
            "roles": self._load_json("roles.json"),
            "users": self._load_json("users.json"),
            "projects": self._load_json("projects.json"),
        }

        # Resolve configuration path
        config_path = self.resolve_config()

        # Load configuration
        self.load_config(config_path)

    def resolve_config(self) -> str:
        """Resolve configuration path."""
        return (
            self._resolve_config_by_user()
            or self._resolve_config_by_role()
            or self._resolve_config_by_project()
            or os.path.join(self.plugin_dir, "config.json")
        )

    def _get_abspath(self, path: str) -> str | None:
        """Return absolute path."""
        if not path:
            return None
        if os.path.isabs(path):
            return path
        else:
            return os.path.join(self.plugin_dir, path)

    def _load_json(self, filename: str) -> dict:
        """Load a JSON file and return its content.

        Args:
            filename: File name.

        Returns:
            File content if successful, otherwise empty dictionary.
        """
        path = os.path.join(self.plugin_dir, filename)
        try:
            with open(path, encoding="utf-8") as file:
                return json.load(file)

        except Exception as err:
            self.log(f"Could not load {path}: {err}", "error")
            return {}

    def _get_user(self) -> dict | None:
        """Get user configuration from the QGIS Authentication Manager.

        Returns:
            User configuration if found, otherwise None.
        """
        try:
            manager = QgsApplication.authManager()
            auth_ids = manager.availableAuthMethodConfigs()
            if not auth_ids:
                return None
            auth_id = list(auth_ids.keys())[0]
            auth_method_cfg = QgsAuthMethodConfig()
            manager.loadAuthenticationConfig(auth_id, auth_method_cfg, True)
            return auth_method_cfg.configMap()

        except Exception as err:
            self.log(f"Could not read user configuration: {err}", "error")
            return None

    def _resolve_config_by_role(self) -> str | None:
        """
        Resolve configuration path based on user roles in the database.

        Returns:
            Configuration path if found, otherwise None.
        """
        self.log("Resolving configuration path based on user roles")

        # Get user information
        user = self._get_user() or {}

        username = user.get("username")
        password = user.get("password")

        # Return if no username
        if not username:
            self.log("No active user")
            return None

        # Get user roles
        role_entries: list[dict] = self.custom["roles"].get("roles", [])
        role_names = [entry["rolname"] for entry in role_entries if "rolname" in entry]

        if not role_names:
            self.log("No user roles", "warning")
            return None

        # Get database connection parameters
        host = self.custom["connections"].get("host")
        port = int(self.custom["connections"].get("port") or 5432)
        dbname = self.custom["connections"].get("dbname")

        # Return if connections parameters are incomplete
        if not host or not port or not dbname:
            self.log("Incomplete database connection parameters", "warning")
            return None

        # Fetch roles defined in the database
        try:
            sql = """
                SELECT r1.rolname
                FROM pg_roles r
                JOIN pg_auth_members m ON m.member = r.oid
                JOIN pg_roles r1 ON m.roleid = r1.oid
                WHERE r.rolname = %s
                AND r1.rolname = ANY(%s)
            """
            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname, user=username, password=password
            )
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute(sql, (username, role_names))
            rows = cur.fetchall()
            conn.close()

        except Exception as err:
            self.log(f"Could not fetch roles: {err}", "error")
            return None

        matched_roles = [row["rolname"] for row in rows]

        if not matched_roles:
            self.log(f"User '{username}' does not have any roles")
            return None

        self.log(f"User '{username}' has roles: {matched_roles}")

        for entry in role_entries:
            if entry.get("rolname") in matched_roles:
                path = self._get_abspath(entry.get("config_path"))
                if path and os.path.isfile(path):
                    self.log(
                        f"Configuration found for role {entry['rolname']}: {path}"
                    )
                    return path
                else:
                    self.log(
                        f"Invalid configuration for role '{entry['rolname']}': {path}",
                        "warning",
                    )

        self.log(f"No configuration found for roles '{matched_roles}'")
        return None

    def _resolve_config_by_user(self) -> str | None:
        """Resolve configuration path based on user.

        Returns:
            Configuration path if found, otherwise None.
        """
        self.log("Resolving configuration path based on user")

        user = self._get_user() or {}

        username = user.get("username")
        if not username:
            self.log("No active user")
            return None

        for entry in self.custom["users"].get("users", []):
            if username in entry.get("usernames", []):
                path = self._get_abspath(entry.get("config_path"))
                if path and os.path.isfile(path):
                    self.log(f"Configuration found for user '{username}': {path}")
                    return path
                else:
                    self.log(
                        f"Invalid configuration for user '{username}': {path}",
                        "warning",
                    )

        self.log(f"No configuration found for user '{username}'")
        return None

    def _resolve_config_by_project(self) -> str | None:
        """Resolve configuration path based on project filename.

        Returns:
            Configuration path if found, otherwise None.
        """
        self.log("Resolving configuration path based on project filename")

        project_path = QgsProject.instance().fileName()
        if not project_path:
            self.log("No active project")
            return None

        project_name = os.path.basename(project_path)

        for entry in self.custom["projects"].get("projects", []):
            if project_name in entry.get("project_names", []):
                path = self._get_abspath(entry.get("config_path"))
                if path and os.path.isfile(path):
                    self.log(
                        f"Configuration found for project '{project_name}': {path}"
                    )
                    return path
                else:
                    self.log(
                        f"Invalid configuration for project '{project_name}': {path}",
                        "warning",
                    )

        self.log(f"No configuration found for project '{project_name}'")
        return None

    def load_config(self, path: str):
        """Load configuration from the file.

        Args:
            path: Configuration file path.
        """
        self.config = {}
        self.config_path = None

        try:
            with open(path, encoding="utf-8") as file:
                self.config = json.load(file)

            self.config_path = path

            self.log(f"Configuration is loaded from {path}")

        except Exception as err:
            self.log(f"Could not load configuration file {path}: {err}", "error")

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
        """Refresh user interface."""
        self.log("Refreshing user interface.")

        # Get enabled flag
        enabled = self.settings.value("qgislight/enabled") == "true"
        self.log(f"Enabled flag is {enabled}.")

        # Get configuration path
        config_path = self.resolve_config()

        # Switch configuration if required
        if config_path != self.config_path:
            self.log(f"Switching configuration to {config_path}")

            # Disable simplifications if required
            if enabled:
                self.disable()

            # Load new configuration
            self.load_config(config_path)

        # Enable simplifications if required
        if enabled:
            QTimer.singleShot(0, self.enable)

    def initGui(self):
        """Initializes plugin user interface."""
        self.log("Initializing plugin user interface.")

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

        # Disconnect from project read signal
        try:
            QgsProject.instance().readProject.disconnect(self.refresh)
        except Exception:
            pass
