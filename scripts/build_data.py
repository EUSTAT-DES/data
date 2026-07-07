import sdg.ProgressMeasure
from sdg.ProgressMeasure import SeriesProgress, get_progress_status
from sdg.open_sdg import open_sdg_build


class SeriesProgressEustat(SeriesProgress):
    def __init__(self, indicator, config={}, logging=None):
        print("[EUSTAT] >>> Motor de progreso personalizado de Eustat ACTIVO <<<")
        super().__init__(indicator, config=config, logging=logging)

    def get_progress_thresholds(self):
        """Umbrales Eurostat.
        Método 1 (sin target): 1.0% / 0.1% / -0.1%
        Método 2 (con target): 95% / 60% / 0% (sin cambios)
        """
        user_thresholds = self.progress_thresholds

        if self.method == 1:
            progress_thresholds = {'high': 0.01, 'med': 0.001, 'low': -0.001}
            progress_thresholds.update(user_thresholds)

            # Mantener factor C si hay limit (decisión pendiente de EUSTAT)
            if self.limit is not None:
                if (self.base_value < self.limit) and (self.direction == -1):
                    self.warn(f'{self.inid} - Base value ({self.base_value}) is below minimum limit ({self.limit}).')
                if (self.base_value > self.limit) and (self.direction == 1):
                    self.warn(f'{self.inid} - Base value ({self.base_value}) is above maximum limit ({self.limit}).')
                base_value = abs(self.base_value)
                limit = abs(self.limit)
                a = 4.44
                if base_value >= 2 * limit:
                    coeff = 1
                elif base_value <= limit:
                    coeff = 1 - (base_value / limit) ** a
                else:
                    coeff = 1 - ((2 * limit - base_value) / limit) ** a

                for key in ['high', 'med']:
                    progress_thresholds[key] *= coeff
                # low (-0.1%) NO se reduce (Eurostat: umbrales <=0 no se reducen)
                progress_thresholds['coefficient'] = coeff

        elif self.method == 2:
            progress_thresholds = {'high': 0.95, 'med': 0.6, 'low': 0}
            progress_thresholds.update(user_thresholds)

            if self.limit is not None:
                self.warn(f'{self.inid} - Ignoring limit ({self.limit}) as target ({self.target}) already provided.')

        return progress_thresholds

    def get_score(self):
        """Scoring Eurostat: transforma progress_value a score [-5, +5].

        Método 1 (sin target): Función LINEAL.
          - Cut-off superior: CAGR = +2.0% * C -> score +5
          - Umbral high (1.0% * C) -> score +2.5
          - CAGR = 0% -> score 0
          - Umbral low (-0.1%) -> score aprox -0.25 (lineal)
          - Cut-off inferior: CAGR = -2.0% -> score -5

        Método 2 (con target): Función NO LINEAL (dos tramos).
          - Cut-off superior: Ratio = 130% -> score +5
          - Umbral high (95%) -> score +2.5
          - Umbral med (60%) -> score 0
          - Umbral low (0%) -> score -2.5
          - Cut-off inferior: Ratio = -60% -> score -5
        """
        if self.target_achieved:
            return 5
        if self.progress_value is None:
            return None

        v = self.progress_value

        if self.method == 1:
            # Función lineal: score = v / cutoff * 5
            # cutoff = 0.02 (2%) sin factor C
            # Con factor C, el cutoff positivo se reduce: cutoff_pos = 0.02 * C
            # El cutoff negativo NO se reduce: cutoff_neg = -0.02
            coeff = self.progress_thresholds.get('coefficient', 1)
            cutoff_pos = 0.02 * coeff  # +2% * C -> score +5
            cutoff_neg = -0.02          # -2% -> score -5

            if v >= 0:
                if cutoff_pos == 0:
                    # Caso especial: base_value == limit, cualquier v >= 0 es +5
                    return 5
                score = (v / cutoff_pos) * 5
            else:
                score = (v / abs(cutoff_neg)) * 5

            return max(-5, min(5, score))

        else:  # method == 2
            # Dos tramos (no lineal, armonizado con método 1):
            # Tramo superior: de med(0.6) a cutoff_high(1.3) -> score 0 a +5
            #   pendiente = 5 / (1.3 - 0.6) = 7.1429
            #   score = 7.1429 * v - 4.2857
            # Tramo inferior: de cutoff_low(-0.6) a med(0.6) -> score -5 a 0
            #   pendiente = 5 / (0.6 - (-0.6)) = 4.1667
            #   score = 4.1667 * v - 2.5
            if v >= 0.6:
                score = 7.1429 * v - 4.2857
            else:
                score = 4.1667 * v - 2.5

            return max(-5, min(5, score))


# Función personalizada: status desde progress_value (nivel serie)
def get_progress_status_eustat(value, thresholds, target_achieved=False):
    """Eurostat: 5 categorías.
    Método 1 (sin target): high/med/low = 1%/0.1%/-0.1% → 5 niveles con neutral
    Método 2 (con target): high/med/low = 95%/60%/0% → 4 niveles sin neutral
    La distinción se hace automáticamente por los umbrales recibidos.
    """
    x = float(thresholds['high'])
    y = float(thresholds['med'])
    z = float(thresholds['low'])

    if target_achieved:
        return "significant_progress"

    if value is not None:
        if value >= x:
            return "significant_progress"
        elif value >= y:
            return "moderate_progress"
        elif value >= z:
            # Método 1: z=-0.001, esto es "neutral" (entre -0.1% y +0.1%)
            # Método 2: z=0, esto es "insufficient progress" (entre 0% y 60%)
            if z < 0:
                return "no_progress"
            else:
                return "moderate_deterioration"
        elif value >= -abs(x):
            return "moderate_deterioration"
        else:
            return "significant_deterioration"

    return "not_available"


# Función personalizada: status desde score agregado (nivel indicador)
def get_progress_status_from_score_eustat(score, target_achieved=False):
    """Eurostat: mapeo score [-5,+5] a 5 estados."""
    if target_achieved:
        return "significant_progress"
    if score is None:
        return "not_available"
    elif score >= 2.5:
        return "significant_progress"
    elif score > 0:
        return "moderate_progress"
    elif score == 0:
        return "no_progress"
    elif score >= -2.5:
        return "moderate_deterioration"
    else:
        return "significant_deterioration"


# Monkey patching
sdg.ProgressMeasure.SeriesProgress = SeriesProgressEustat
sdg.ProgressMeasure.get_progress_status = get_progress_status_eustat
sdg.ProgressMeasure.get_progress_status_from_score = get_progress_status_from_score_eustat

open_sdg_build(config='config_data.yml')

# Generar progreso.csv a partir de los metadatos del build
import json, csv
from collections import defaultdict

print("[EUSTAT] >>> Generando progreso.csv <<<")
try:
    with open('_site/eu/meta/all.json', encoding='utf-8') as f:
        all_meta = json.load(f)

    # Mapeo legacy -> Eurostat para unificar estados en el CSV
    legacy_map = {
        'alcanzado': 'significant_progress',
        'progreso': 'moderate_progress',
        'retroceso': 'moderate_deterioration',
        'noevaluado': 'not_available',
        'notstarted': 'notstarted',
        'notapplicable': 'notapplicable',
    }

    counts = defaultdict(lambda: defaultdict(int))
    for inid, meta in all_meta.items():
        goal = str(meta.get('goal_number', '')).strip()
        status = meta.get('progress_status', 'not_available')
        status = legacy_map.get(status, status)  # unificar
        if not goal or not goal.isdigit():
            continue
        counts[goal][status] += 1
        counts['overall'][status] += 1

    rows = []
    goal_order = ['overall'] + sorted([g for g in counts if g != 'overall'], key=int)
    for goal in goal_order:
        if goal not in counts:
            continue
        statuses = counts[goal]
        total = sum(statuses.values())
        for status, count in statuses.items():
            rows.append({'goal': goal, 'status': status, 'count': count,
                         'percentage': count / total * 100, 'total': total})

    with open('_site/progreso.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['goal', 'status', 'count', 'percentage', 'total'])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[EUSTAT] >>> progreso.csv generado: {len(rows)} filas <<<")
except Exception as e:
    print(f"[EUSTAT] !!! Error generando progreso.csv: {e} !!!")
