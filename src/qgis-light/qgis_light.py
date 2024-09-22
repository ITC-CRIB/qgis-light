import os.path
import json

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsSettings
)
from qgis.gui import (
    QgisInterface,
    QgsGui
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction,
    QDockWidget,
    QMenu,
    QToolBar,
    QToolButton,
    QWidget,
    QWidgetAction
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
        "top": Qt.TopToolBarArea,
        "bottom": Qt.BottomToolBarArea,
        "left": Qt.LeftToolBarArea,
        "right": Qt.RightToolBarArea,
    }

    # Panel areas
    _panel_areas = {
        "top": Qt.TopDockWidgetArea,
        "bottom": Qt.BottomDockWidgetArea,
        "left": Qt.LeftDockWidgetArea,
        "right": Qt.RightDockWidgetArea,
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

        # Load configuration
        with open(os.path.join(self.plugin_dir, "config.json")) as file:
            self.config = json.load(file)
        self.log("Configuration loaded.")


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

        Raises:
            Exception: If invalid parent object name.
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
            self.addItems(menu, algorithms["menu"])

            toolbutton = QToolButton(self.mainwindow)
            toolbutton.setIcon(QIcon(algorithms["icon"]))
            toolbutton.setMenu(menu)
            toolbutton.setPopupMode(QToolButton.MenuButtonPopup)

            return [toolbutton]

        parent = self.mainwindow.findChild(QWidget, parent_name)
        if not parent:
            raise Exception(f"Invalid parent object name {parent_name}.")

        wildcard = name[-1] == "*"
        if wildcard:
            name = name[:-1]

        if not name:
            return parent.actions()

        action = self.findAction(parent, name)
        if action:
            if not wildcard:
                return [action]

            for widget in action.associatedWidgets():
                if isinstance(widget, QToolButton):
                    return [widget.menu()] if widget.menu() else widget.actions()

            for widget in action.associatedWidgets():
                if isinstance(widget, QMenu):
                    return [widget]

        self.log(f"Invalid identifier token {token}.")
        return []


    def addItems(self, parent: QWidget, items: list):
        """Adds items to the associated parent object.

        Args:
            parent (QWidget): Parent object.
            items (list): List of items.

        Raises:
            Exception: If invalid item.
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
                    toolbutton.setPopupMode(QToolButton.MenuButtonPopup)
                    toolbutton.setDefaultAction(item.actions()[0])
                    item.triggered.connect(toolbutton.setDefaultAction)
                    parent.addWidget(toolbutton)

            elif isinstance(item, QWidget):
                parent.addWidget(item)

            else:
                raise Exception(f"Invalid item {item}.")


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

            if self.mainwindow.toolBarArea(toolbar) != item["area"]:
                self.mainwindow.addToolBar(item["area"], toolbar)

            toolbar.show()
            self.log(f"Toolbar {item['name']} is visible.")

        # Restore panels
        items = self.settings.value("qgislight/panels", [])
        for item in items:

            panel = self.mainwindow.findChild(QDockWidget, item["name"])
            if not panel:
                self.log(f"Panel {item['name']} not found.", "warning")
                continue

            if self.mainwindow.dockWidgetArea(panel) != item["area"]:
                self.mainwindow.addDockWidget(item["area"], panel)

            panel.setFeatures(QDockWidget.DockWidgetFeatures(item["features"]))

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
        self.mainwindow.setContextMenuPolicy(Qt.DefaultContextMenu)

        # Remove simplified toolbars
        for name in self.config["toolbars"]:
            toolbar = self.mainwindow.findChild(QToolBar, name)
            if not toolbar:
                self.log(f"Toolbar {name} not found.", "warning")
                continue
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
        self.mainwindow.setContextMenuPolicy(Qt.NoContextMenu)

        # Set up toolbars
        items = []

        for toolbar in self.mainwindow.findChildren(QToolBar):
            if toolbar.parent() == self.mainwindow and not toolbar.isHidden():
                name = toolbar.objectName()
                items.append({
                    "name": name,
                    "area": self.mainwindow.toolBarArea(toolbar),
                })
                toolbar.hide()
                self.log(f"Toolbar {name} is hidden.")

        if store:
            self.settings.setValue("qgislight/toolbars", items)

        for name, item in self.config["toolbars"].items():
            self.log(f"Creating toolbar {name}.")
            toolbar = QToolBar(item["title"], self.mainwindow)
            toolbar.setObjectName(name)
            toolbar.setFloatable(False)
            toolbar.setMovable(False)
            toolbar.toggleViewAction().setDisabled(True)
            self.mainwindow.addToolBar(
                self._toolbar_areas.get(item["area"], Qt.TopToolBarArea),
                toolbar
            )
            self.addItems(toolbar, item["items"])
            toolbar.show()

        # Set up panels
        panels = self.config.get("panels", {})
        items = []

        for panel in self.mainwindow.findChildren(QDockWidget):
            name = panel.objectName()
            items.append({
                "name": name,
                "area": self.mainwindow.dockWidgetArea(panel),
                "features": panel.features(),
                "hidden": panel.isHidden(),
            })
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
                self._panel_areas.get(area, Qt.LeftDockWidgetArea),
                panel
            )
            if state == "fixed":
                panel.setFeatures(QDockWidget.NoDockWidgetFeatures)
                panel.show()
            elif state == "hidden":
                panel.hide()
            self.log(f"Panel {name} is set as {state} at area {area}.")

        if store:
            self.settings.setValue("qgislight/panels", items)

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


    def initGui(self):
        """Initializes plugin user interface."""
        self.log("Initializing user interface.")

        # Get enabled flag
        enabled = self.settings.value("qgislight/enabled")
        self.log(f"Enabled flag is {enabled}.")

        # Check if simplifications are enabled
        if enabled == "true":
            # Connect to initializationCompleted signal to delay initialization.
            #
            # This is required to have access to the final states of toolbars
            # and panels as modified by loaded plugins.
            self.mainwindow.initializationCompleted.connect(self.enable)

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
            for widget in action.associatedWidgets():
                widget.removeAction(action)
            action.deleteLater()
