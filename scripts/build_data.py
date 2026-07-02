import sdg.ProgressMeasure
from sdg.ProgressMeasure import SeriesProgress
from sdg.open_sdg import open_sdg_build


class SeriesProgressEustat(SeriesProgress):
    def __init__(self, indicator, config={}, logging=None):
        print("[EUSTAT] >>> Motor de progreso personalizado de Eustat ACTIVO <<<")
        super().__init__(indicator, config=config, logging=logging)


# Monkey patching: reemplazar SeriesProgress por la versión Eustat
sdg.ProgressMeasure.SeriesProgress = SeriesProgressEustat

open_sdg_build(config='config_data.yml')
