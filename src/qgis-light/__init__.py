def classFactory(iface):
    from .qgis_light import QGISLightPlugin
    return QGISLightPlugin(iface)