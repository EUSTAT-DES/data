import sdg.ProgressMeasure
from sdg.ProgressMeasure import IndicatorProgress, SeriesProgress, get_progress_status
from sdg.open_sdg import open_sdg_build


class SeriesProgressEustat(SeriesProgress):
    def __init__(self, indicator, config={}, logging=None):
        print("[EUSTAT] >>> Motor de progreso personalizado de Eustat ACTIVO <<<")
        # Detectar indicadores booleanos ANTES del cálculo CAGR
        raw_data = indicator.data.copy()
        unit_col = indicator.options.unit_column
        is_boolean = (unit_col in raw_data.columns and
                      (raw_data[unit_col] == 'BOOL_YES_NO').any())

        if is_boolean:
            # Cortocircuitar: no llamar al __init__ completo (evita CAGR)
            IndicatorProgress.__init__(self, indicator, logging=logging)
            self.series = config.get('series')
            self.unit = 'BOOL_YES_NO'
            self.tag = 'BOOL'
            # Determinar status por último valor
            bool_data = raw_data[raw_data[unit_col] == 'BOOL_YES_NO'].copy()
            bool_data['Value'] = bool_data['Value'].astype(float)
            bool_data = bool_data.sort_values('Year')
            ultimo_valor = bool_data.iloc[-1]['Value']
            if ultimo_valor == 1:
                self.status = 'significant_progress'
                self.score = 5
                self.target_achieved = True
            else:
                self.status = 'significant_deterioration'
                self.score = -5
                self.target_achieved = False
            self.progress_value = None
            self.data = None
            self.progress_thresholds = {}
            self.warn(f'{self.inid} - Indicador booleano: ultimo valor={ultimo_valor} -> {self.status}')
            return

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

        Método 1 (sin target): CAGR -> score.
          - CAGR > 2%: score = 5
          - CAGR entre -2% y 2%: score = 2.5 * CAGR (CAGR en tanto por uno, ej 0.02)
          - CAGR < -2%: score = -5
          Nota: 2.5 * 0.02 = 0.05... NO. CAGR se expresa como ratio (0.01 = 1%).
          Fórmula real: score = 2.5 * (CAGR / 0.01) = 250 * CAGR
          Equivalente: score = CAGR / 0.02 * 5

        Método 2 (con target): Ratio CAGR -> score (NO lineal, dos tramos).
          - Ratio > 130%: score = 5
          - Ratio entre 60% y 130%: score = 5/70 * (ratio - 60%)
          - Ratio entre -60% y 60%: score = 5/120 * (ratio + 60%) - 5
          - Ratio <= -60%: score = -5
        """
        if self.target_achieved:
            return 5
        if self.progress_value is None:
            return None

        v = self.progress_value

        if self.method == 1:
            # v es CAGR en tanto por uno (ej: 0.015 = 1.5%)
            #
            # Factor C (coeff): mide cuánto margen de mejora queda antes del límite natural.
            #   C = 1 - (base_value/limit)^4.44
            #   - Lejos del límite: C ≈ 1 (sin efecto)
            #   - Cerca del límite: C ≈ 0 (amplifica el score)
            # Se aplica solo al progreso positivo: un indicador cerca de su límite
            # (ej. tasa empleo 95% con limit 100) necesita menos CAGR para score alto.
            # Progreso negativo no se amplifica como en Canada (alejarse del límite no se "perdona").
            coeff = self.progress_thresholds.get('coefficient', 1)
            if v < -0.02:
                return -5
            if coeff == 0:
                # base_value == limit, mantener el límite ya es progreso significativo
                return 5
            if v > 0.02 * coeff:
                return 5
            elif v >= 0:
                return v / (0.02 * coeff) * 5
            else:
                # 2.5 * CAGR_en_porcentaje = 2.5 * (v*100) = 250*v = v/0.02*5
                return v / 0.02 * 5

        else:  # method == 2
            
            if v > 1.3:
                return 5
            elif v >= 0.6:
                # 5/70*(ratio%-60) = (5/0.7)*(v-0.6)
                return (5.0 / 0.7) * (v - 0.6)
            elif v > -0.6:
                # 5/120*(ratio%+60)-5 = (5/1.2)*(v+0.6)-5
                return (5.0 / 1.2) * (v + 0.6) - 5
            else:
                return -5


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
