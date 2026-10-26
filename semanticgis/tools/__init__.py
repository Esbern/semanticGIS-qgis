"""SemanticGIS tools in the Processing Toolbox.

General tools ship with the plugin and are grouped by what they do (Raster analysis, Vector table, …), the way QGIS
groups its own, not by course: the same tool serves many classes. Research groups' own tools will come
from their extension servers later and get their own group beside these.
"""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .renumber_fid import RenumberFid
from .tabulate_area import TabulateAreaLegend

ALGORITHMS = [TabulateAreaLegend, RenumberFid]


class SemanticGisProvider(QgsProcessingProvider):
    def id(self): return "semanticgis"
    def name(self): return "SemanticGIS"
    def longName(self): return "SemanticGIS tools"
    def icon(self): return QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.svg"))

    def loadAlgorithms(self):
        for algorithm in ALGORITHMS:
            self.addAlgorithm(algorithm())
