def classFactory(iface):
    from .plugin import SemanticGisPlugin

    return SemanticGisPlugin(iface)
