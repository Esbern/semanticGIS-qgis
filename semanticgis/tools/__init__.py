"""SemanticGIS tools in the Processing Toolbox.

General tools ship with the plugin and are grouped by what they do (Raster analysis, Vector table, …), the way QGIS
groups its own, not by course: the same tool serves many classes. Research groups' own tools will come
from their extension servers later and get their own group beside these.
"""
import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .aoi_wkt import AreaOfInterestWkt
from .flatten_snapshots import FlattenSnapshots
from .renumber_fid import RenumberFid
from .snapshot_series import SnapshotSeries
from .tabulate_area import TabulateAreaLegend
from .value_combination import ValueCombination
from .zonal_statistics import ZonalStatisticsLegend

ALGORITHMS = [TabulateAreaLegend, ZonalStatisticsLegend, RenumberFid, AreaOfInterestWkt, SnapshotSeries, FlattenSnapshots, ValueCombination]


class SemanticGisProvider(QgsProcessingProvider):
    def id(self): return "semanticgis"
    def name(self): return "SemanticGIS"
    def longName(self): return "SemanticGIS tools"
    def icon(self): return QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.svg"))

    def loadAlgorithms(self):
        for algorithm in ALGORITHMS:
            self.addAlgorithm(algorithm())
