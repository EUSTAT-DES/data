import os
import math
import pandas as pd
import yaml
import sdg.ProgressMeasure
from sdg.ProgressMeasure import IndicatorProgress, SeriesProgress, get_progress_status, floatNone
from sdg.open_sdg import open_sdg_build


def apply_goldilocks_transforms(data_dir='data', config_dir='indicator-config'):
    """Lee cada indicator-config, detecta goldilocks_transform en progress_calculation_options
    y escribe la columna Progress en el CSV correspondiente.
    No modifica filas que no participan en el cálculo.
    """
    for config_file in os.listdir(config_dir):
        if not config_file.endswith('.yml'):
            continue
        inid = config_file[:-4]  # ej. '5-4-1'
        csv_path = os.path.join(data_dir, f'indicator_{inid}.csv')
        if not os.path.exists(csv_path):
            continue

        with open(os.path.join(config_dir, config_file), encoding='utf-8') as f:
            config = yaml.safe_load(f)
        if not config:
            continue

        options = config.get('progress_calculation_options', [])
        goldilocks_options = [o for o in options if isinstance(o, dict) and 'goldilocks_transform' in o]
        if not goldilocks_options:
            continue

        df = pd.read_csv(csv_path, dtype=str)
        if 'Progress' not in df.columns:
            df['Progress'] = ''

        for opt in goldilocks_options:
            formula = opt['goldilocks_transform']
            groups = opt.get('goldilocks_groups')

            if groups:
                _apply_goldilocks_multigroup(df, formula, groups, inid)
            else:
                _apply_goldilocks_simple(df, formula, inid)

        df.to_csv(csv_path, index=False)
        print(f'[EUSTAT] Goldilocks aplicado: {inid}')


def _apply_goldilocks_simple(df, formula, inid):
    """Transforma fila a fila: Progress = eval(formula) con Value como variable."""
    def transform(row):
        try:
            Value = float(row['Value'])  # noqa: N806 — nombre de variable intencional
            return eval(formula, {'__builtins__': {}}, {'Value': Value, 'abs': abs, 'max': max, 'min': min, 'math': math})
        except Exception as e:
            print(f'[EUSTAT] Goldilocks error en {inid} fila {row.name}: {e}')
            return ''

    mask = df['Value'].notna() & (df['Value'] != '')
    df.loc[mask, 'Progress'] = df[mask].apply(transform, axis=1)


def _apply_goldilocks_multigroup(df, formula, groups, inid):
    """Para cada año, calcula el valor combinando filas de distintos grupos
    y lo escribe en la fila que NO tiene ninguno de esos valores de grupo (fila total).
    """
    # Identificar las columnas y valores que definen cada variable
    # groups = {'fem': {'column': 'Sexo', 'value': 'F'}, 'masc': {'column': 'Sexo', 'value': 'M'}}
    group_columns = {var: (g['column'], str(g['value'])) for var, g in groups.items()}
    all_filter_cols = set(col for col, _ in group_columns.values())

    years = df['Year'].unique()
    for year in years:
        df_year = df[df['Year'] == year]
        context = {}
        ok = True
        for var, (col, val) in group_columns.items():
            if col not in df.columns:
                print(f'[EUSTAT] Goldilocks {inid}: columna {col} no encontrada')
                ok = False
                break
            rows = df_year[df_year[col] == val]
            # Fila total: las demás columnas de filtro deben estar vacías
            other_cols = all_filter_cols - {col}
            for other_col in other_cols:
                rows = rows[rows[other_col].isna() | (rows[other_col] == '')]
            if rows.empty or rows['Value'].isna().all() or (rows['Value'] == '').all():
                ok = False
                break
            try:
                context[var] = float(rows['Value'].iloc[0])
            except Exception:
                ok = False
                break

        if not ok:
            continue

        try:
            result = eval(formula, {'__builtins__': {}}, {**context, 'abs': abs, 'max': max, 'min': min, 'math': math})
        except Exception as e:
            print(f'[EUSTAT] Goldilocks error en {inid} año {year}: {e}')
            continue

        # Escribir en la fila total: todas las columnas de grupo vacías
        total_mask = df['Year'] == year
        for col in all_filter_cols:
            total_mask &= (df[col].isna() | (df[col] == ''))
        if total_mask.any():
            df.loc[total_mask, 'Progress'] = result
        else:
            print(f'[EUSTAT] Goldilocks {inid} año {year}: no se encontró fila total para escribir Progress')


class SeriesProgressEustat(SeriesProgress):
    @staticmethod
    def find_nearest_year(base_year, available_years):
        """Búsqueda alternante (espiral) del año más cercano al base_year.
        Orden: base_year, base+1, base-1, base+2, base-2, ...
        Devuelve el primer año que exista en available_years, o None.
        """
        if base_year in available_years:
            return base_year
        max_delta = int(max(abs(available_years.max() - base_year), abs(base_year - available_years.min()))) + 1
        for delta in range(1, max_delta + 1):
            for candidate in [base_year + delta, base_year - delta]:
                if candidate in available_years:
                    return candidate
        return None

    def __init__(self, indicator, config={}, logging=None):
        # Detectar indicadores booleanos via progress_boolean: true en indicator-config
        is_boolean = config.get('progress_boolean', False)

        if is_boolean:
            # Cortocircuitar: no llamar al __init__ completo (evita CAGR)
            IndicatorProgress.__init__(self, indicator, logging=logging)
            self.series = config.get('series')
            self.unit = 'BOOL_YES_NO'
            self.disaggregation = config.get('disaggregation')
            self.tag = self.inid
            # Atributos requeridos por get_progress_calculation_components()
            self.base_value = None
            self.base_year = None
            self.current_value = None
            self.current_year = None
            self.target = None
            self.target_year = config.get('target_year', 2030)
            self.direction = 1
            self.sign = 1
            self.limit = None
            # Determinar status por último valor usando indicator.data directamente
            bool_data = indicator.data[['Year', 'Value']].copy()
            bool_data['Value'] = bool_data['Value'].astype(float)
            bool_data = bool_data.dropna(subset=['Value']).sort_values('Year')
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

        # Post-procesado: recalcular base_year con búsqueda alternante
        if self.data is not None:
            years = self.data['Year'].values
            original_base = config.get('base_year', 2015)
            nearest = self.find_nearest_year(original_base, years)
            if nearest is not None and nearest != self.base_year:
                self.base_year = nearest
                self.base_value = self.data.Value[self.data.Year == self.base_year].item()
                self.sign = -1 if self.base_value < 0 else 1
                # Recalcular todo con el nuevo base_year
                self.progress_thresholds = self.get_progress_thresholds()
                self.target_achieved = self.is_target_achieved()
                self.progress_value = self.calculate_progress_value()
                self.status = get_progress_status_eustat(self.progress_value, self.progress_thresholds, self.target_achieved)
                self.score = self.get_score()

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
        elif z < 0 and value >= -abs(x):
            # Solo método 1: entre -0.1% y -1% → moderate_deterioration
            return "moderate_deterioration"
        else:
            return "significant_deterioration"

    return "not_available"


def get_target_variant(opts):
    """Detecta si el indicador es sin_target, con_target o mixto.
    opts: lista de dicts de progress_calculation_options.
    """
    if not opts:
        return 'sin_target'
    has_target = [bool(o.get('target') is not None) for o in opts if isinstance(o, dict)]
    if not has_target:
        return 'sin_target'
    if all(has_target):
        return 'con_target'
    if not any(has_target):
        return 'sin_target'
    return 'mixto'


# Función personalizada: status desde score agregado (nivel indicador)
def get_progress_status_from_score_eustat(score, target_achieved=False):
    """Eurostat: mapeo score [-5,+5] a 5 estados con banda neutral ±0.25."""
    if target_achieved:
        return "significant_progress"
    if score is None:
        return "not_available"
    elif score > 2.5:
        return "significant_progress"
    elif score > 0.25:
        return "moderate_progress"
    elif score >= -0.25:
        return "no_progress"
    elif score >= -2.5:
        return "moderate_deterioration"
    else:
        return "significant_deterioration"


def get_indicator_progress_eustat(self):
    """Si hay una sola serie (sin grupos), devuelve directamente el status
    de esa serie sin pasar por el score agregado.
    Con múltiples series, usa el flujo normal (media de scores).
    """
    if self.meta is None or self.meta.get('auto_progress_calculation') is not True:
        indicator_status = self.meta.get('progress_status', '') if self.meta else ''
        result = (None, indicator_status)
        if self.cache_store is None:
            self.cache_store = {}
        self.cache_store[self.inid] = {'progress_status': indicator_status, 'score': None, 'target_variant': 'sin_target'}
        return result

    if self.cache_store is not None and self.inid in self.cache_store:
        return (self.cache_store[self.inid]['score'], self.cache_store[self.inid]['progress_status'])

    opts = self.get_progress_calculation_options()
    target_variant = get_target_variant(opts)
    # Una sola serie: sin grupos y sin múltiples opciones
    if len(opts) == 1 and not opts[0].get('group'):
        series = sdg.ProgressMeasure.SeriesProgress(self.indicator, opts[0], logging=self.logging)
        indicator_status = series.status
        indicator_score = series.score
        components = series.get_progress_calculation_components()
    else:
        # Múltiples series o grupos: flujo normal
        from sdg.ProgressMeasure import grouped_score
        indicator_score, targets_achieved, components = grouped_score(
            self.indicator, opts, logging=self.logging
        )
        target_achieved = all(targets_achieved) if targets_achieved else False
        indicator_status = get_progress_status_from_score_eustat(indicator_score, target_achieved)

    if self.cache_store is None:
        self.cache_store = {}
    self.cache_store[self.inid] = {'progress_status': indicator_status, 'score': floatNone(indicator_score), 'target_variant': target_variant}
    self.cache_store[self.inid].update(components)
    return (indicator_score, indicator_status)


# Monkey patching
sdg.ProgressMeasure.SeriesProgress = SeriesProgressEustat
sdg.ProgressMeasure.get_progress_status = get_progress_status_eustat
sdg.ProgressMeasure.get_progress_status_from_score = get_progress_status_from_score_eustat
sdg.ProgressMeasure.IndicatorProgress.get_indicator_progress = get_indicator_progress_eustat


# ---------------------------------------------------------------------------
# Monkey patch: Sources report
# Añade una tarjeta "Sources report" en el índice de documentación y genera
# sources.html agrupando indicadores por fuente (url_text de indicator-config)
# ---------------------------------------------------------------------------

def _build_sources_store(config_dir='indicator-config', translations_file='translations/es/FUENTE.yml'):
    """Lee todos los indicator-config y devuelve un dict:
    { url_text_key: { 'label': str, 'url': str, 'organisation': str, 'indicators': [str] } }
    Las claves FUENTE.* se resuelven contra translations/es/FUENTE.yml.
    """
    with open(translations_file, encoding='utf-8') as f:
        fuente_trans = yaml.safe_load(f) or {}

    def resolve(key):
        if isinstance(key, str) and key.startswith('FUENTE.'):
            return fuente_trans.get(key[7:], key)
        return key or ''

    store = {}
    for config_file in sorted(os.listdir(config_dir)):
        if not config_file.endswith('.yml'):
            continue
        inid = config_file[:-4]
        with open(os.path.join(config_dir, config_file), encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

        for source in (config.get('sources') or []):
            if not isinstance(source, dict):
                continue
            url_text_key = source.get('url_text', '')
            if not url_text_key:
                continue
            if url_text_key not in store:
                store[url_text_key] = {
                    'label': resolve(url_text_key),
                    'url': resolve(source.get('url', '')),
                    'organisation': resolve(source.get('organisation', '')),
                    'indicators': [],
                }
            store[url_text_key]['indicators'].append(inid)

    return store


def _write_sources_report(self):
    """Genera sources.html y sources-report.csv en self.folder."""
    store = _build_sources_store()

    # --- CSV descargable ---
    csv_rows = []
    for key, info in sorted(store.items(), key=lambda x: x[1]['label']):
        for inid in info['indicators']:
            csv_rows.append({
                'source_key': key,
                'source_label': info['label'],
                'organisation': info['organisation'],
                'url': info['url'],
                'indicator_id': inid,
            })
    csv_path = os.path.join(self.folder, 'sources-report.csv')
    import csv as _csv
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = _csv.DictWriter(f, fieldnames=['source_key', 'source_label', 'organisation', 'url', 'indicator_id'])
        writer.writeheader()
        writer.writerows(csv_rows)

    # --- Tabla HTML por fuente (una fila por fuente) ---
    indicator_url = self.indicator_url or ''

    def indicator_link(inid):
        if indicator_url:
            href = indicator_url.replace('[id]', inid)
            return f'<a href="{href}">#{inid}</a>'
        return f'#{inid}'

    rows_html = ''
    for key, info in sorted(store.items(), key=lambda x: x[1]['label']):
        links = ', '.join(indicator_link(i) for i in info['indicators'])
        source_label = f'<a href="{info["url"]}">{info["label"]}</a>' if info['url'] else info['label']
        rows_html += f'<tr><td>{source_label}</td><td>{info["organisation"]}</td><td>{len(info["indicators"])}</td><td>{links}</td></tr>\n'

    import humanize as _humanize
    import os as _os
    filesize = _humanize.naturalsize(_os.stat(csv_path).st_size)

    content = f"""
    <div role="navigation" aria-describedby="contents-heading">
        <h2 id="contents-heading">On this page</h2>
        <ul>
            <li><a href="#by-source">By source</a></li>
        </ul>
    </div>
    <div>
        <h2 id="by-source" tabindex="-1">By source</h2>
        <div class="my-3">
            <a href="sources-report.csv" role="button" class="btn btn-primary">Download CSV of sources</a>
            <div class="download-info">Size: {filesize}</div>
        </div>
        <div class="total-rows">Total rows: <span class="total">{len(store)}</span></div>
        <table id="sources-table" class="table table-striped table-bordered">
            <thead><tr><th>Source</th><th>Organisation</th><th>Num. indicators</th><th>Indicators</th></tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """

    html = self.get_html('Sources report', content)
    self.write_page('sources.html', html)
    print('[EUSTAT] >>> sources.html generado')


def _write_index_with_sources(self, pages):
    """Llama al write_index original y luego inyecta la tarjeta de sources."""
    _original_write_index(self, pages)
    index_path = os.path.join(self.folder, 'index.html')
    with open(index_path, encoding='utf-8') as f:
        html = f.read()
    card = """
        <div class="row"><div class="col-sm mt-4">
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">Sources report</h5>
                    <p class="card-text">These tables show which indicators use each data source.</p>
                    <a href="sources.html" class="btn btn-primary">See sources report</a>
                </div>
            </div>
        </div></div>
    """
    html = html.replace('</main>', card + '</main>', 1)
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)


def _generate_documentation_with_sources(self):
    _original_generate_documentation(self)
    _write_sources_report(self)


from sdg.OutputDocumentationService import OutputDocumentationService as _ods_class
_original_write_index = _ods_class.write_index
_original_generate_documentation = _ods_class.generate_documentation
_ods_class.write_index = _write_index_with_sources
_ods_class.generate_documentation = _generate_documentation_with_sources

print('[EUSTAT] >>> Monkey patch sources report ACTIVO')

# ---------------------------------------------------------------------------


def generate_tabla_resumen(all_meta, config_dir='indicator-config', data_dir='data',
                           translations_dir='translations/es',
                           output_path='_site/tabla_resumen.csv'):
    """Genera tabla_resumen.csv: una fila por serie (o por indicador si tiene serie única).

    Columnas (según modelo acordado con Eustat 23/07/2026):
      NÚM OBJETIVO, NOMBRE OBJETIVO, NÚM META, NOMBRE META,
      NÚM INDICADOR, NOMBRE INDICADOR NNUU, INDICADOR DISPONIBLE,
      SERIE, NOMBRE SERIE, REPORTING_STATUS,
      BOOLEANO, GOLDILOCK, INDICADOR NO ESTADÍSTICO, TIPO GRÁFICO, MAPA,
      INDICADORES RELACIONADOS, DESAGREGACIÓN SEXO, DESAGREGACIÓN TH,
      DESAGREGACIÓN MUNICIPIO, AÑO INICIAL, PERIODICIDAD, TEXTO OCECA,
      DIRECCIÓN DESEADA, PROGRESO AUTOMÁTICO,
      AÑO BASE PROGRESO, ÚLTIMO AÑO, ÚLTIMO DATO, TARGET, LIMIT, SCORE,
      FECHA ÚLTIMA ACTUALIZACIÓN DATOS

    Casos especiales:
      - reporting_status = not_started → solo columnas 1-6, resto vacío
      - serie única → columna SERIE y NOMBRE SERIE vacías
      - no estadístico (data_non_statistical=si) → sin datos de progreso (cols 24-30)
    """
    # --- Cargar traducciones ---
    def load_yaml(path):
        try:
            with open(path, encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    t_goals      = load_yaml(f'{translations_dir}/global_goals.yml')
    t_targets    = load_yaml(f'{translations_dir}/global_targets.yml')
    t_indicators = load_yaml(f'{translations_dir}/global_indicators.yml')
    t_grafico    = load_yaml(f'{translations_dir}/GRAFICO.yml')
    t_data       = load_yaml(f'{translations_dir}/data.yml')
    t_fuente     = load_yaml(f'{translations_dir}/FUENTE.yml')

    def goal_name(num):
        """Nombre del objetivo — clave '{num}-title' en global_goals.yml."""
        return t_goals.get(f'{num}-title', '')

    def target_name(inid_dash):
        """Nombre de la meta — clave '{goal}-{target}-title' en global_targets.yml.
        inid_dash con guiones, ej: '1-5-4' → meta key '1-5-title'.
        """
        parts = inid_dash.split('-')
        key = '-'.join(parts[:2]) + '-title'
        return t_targets.get(key, '')

    def indicator_onu_name(inid_dash):
        """Nombre NNUU — clave '{inid_dash}-title' en global_indicators.yml."""
        return t_indicators.get(f'{inid_dash}-title', '')

    def indicador_disponible_str(meta_yaml):
        """Resuelve el campo graph_title (o indicador_disponible) del meta YAML.
        Primero intenta graph_title (ej: GRAFICO.1-5-4-titulo), luego indicador_disponible.
        """
        for field in ('graph_title', 'indicador_disponible'):
            raw = meta_yaml.get(field, '')
            if not raw:
                continue
            raw = str(raw)
            if raw.startswith('GRAFICO.'):
                return t_grafico.get(raw[len('GRAFICO.'):], raw)
            if raw.startswith('global_indicators.'):
                return t_indicators.get(raw[len('global_indicators.'):], raw)
            return raw
        return ''

    def serie_nombre(serie_code):
        return t_data.get(serie_code, '')

    def resolve_fuente(key):
        if isinstance(key, str) and key.startswith('FUENTE.'):
            return t_fuente.get(key[len('FUENTE.'):], key)
        return key or ''

    def bool_val(v):
        if isinstance(v, bool):
            return 'si' if v else 'no'
        if isinstance(v, str):
            return 'si' if v.lower() in ('true', 'si', 'yes', '1') else 'no'
        return 'no'

    # --- Leer TODAS las series de un CSV de datos ---
    def get_series_for_indicator(inid_dash):
        """Devuelve lista de códigos de serie únicos.
        Si no hay columna Series → serie única → lista vacía.
        Lee el CSV completo para capturar todas las series.
        """
        csv_path = os.path.join(data_dir, f'indicator_{inid_dash}.csv')
        if not os.path.exists(csv_path):
            return []
        try:
            df = pd.read_csv(csv_path, dtype=str, usecols=lambda c: c == 'Series')
            if 'Series' in df.columns:
                series = df['Series'].dropna().unique().tolist()
                return [s for s in series if str(s).strip()]
            return []
        except Exception:
            return []

    # --- Cabecera ---
    FIELDNAMES = [
        'NÚM OBJETIVO', 'NOMBRE OBJETIVO',
        'NÚM META', 'NOMBRE META',
        'NÚM INDICADOR', 'NOMBRE INDICADOR NNUU',
        'INDICADOR DISPONIBLE',
        'SERIE', 'NOMBRE SERIE',
        'REPORTING_STATUS',
        'BOOLEANO', 'GOLDILOCK', 'INDICADOR NO ESTADÍSTICO',
        'TIPO GRÁFICO', 'MAPA', 'INDICADORES RELACIONADOS',
        'DESAGREGACIÓN SEXO', 'DESAGREGACIÓN TH', 'DESAGREGACIÓN MUNICIPIO',
        'AÑO INICIAL', 'PERIODICIDAD', 'TEXTO OCECA',
        'DIRECCIÓN DESEADA', 'PROGRESO AUTOMÁTICO',
        'AÑO BASE PROGRESO', 'ÚLTIMO AÑO', 'ÚLTIMO DATO',
        'TARGET', 'LIMIT', 'SCORE',
        'FECHA ÚLTIMA ACTUALIZACIÓN DATOS',
    ]

    rows = []

    # Ordenar indicadores numéricamente
    def sort_key(inid_dash):
        parts = inid_dash.split('-')
        nums = []
        for p in parts:
            try:
                nums.append(int(p))
            except ValueError:
                nums.append(ord(p[0]) + 100 if p else 0)
        return nums

    inid_list = sorted(all_meta.keys(), key=sort_key)

    for inid_dash in inid_list:
        meta = all_meta[inid_dash]

        # inid_dash usa guiones: '1-5-4'
        # inid_dot usa puntos:   '1.5.4'  (para mostrar en el CSV)
        inid_dot = inid_dash.replace('-', '.')
        parts    = inid_dash.split('-')
        goal_num   = parts[0] if parts else ''
        target_dot = '.'.join(parts[:2]) if len(parts) >= 2 else ''

        g_name = goal_name(goal_num)
        t_name = target_name(inid_dash)
        i_name = indicator_onu_name(inid_dash)

        # reporting_status desde all.json (puede ser 'notstarted', 'complete', etc.)
        reporting_status = meta.get('reporting_status', '')
        # Normalizar: 'notstarted' → 'not_started' para comparar
        rs_norm = reporting_status.replace('_', '').lower()

        # Cargar indicator-config
        config_path = os.path.join(config_dir, inid_dash + '.yml')
        config = {}
        if os.path.exists(config_path):
            with open(config_path, encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # Cargar meta YAML (periodicidad, indicador_disponible, graph_title…)
        meta_yaml_path = os.path.join('meta', inid_dash + '.yml')
        meta_yaml = {}
        if os.path.exists(meta_yaml_path):
            with open(meta_yaml_path, encoding='utf-8') as f:
                meta_yaml = yaml.safe_load(f) or {}

        ind_disponible = indicador_disponible_str(meta_yaml)

        # Campos de identificación (siempre presentes)
        base = {
            'NÚM OBJETIVO':          goal_num,
            'NOMBRE OBJETIVO':       g_name,
            'NÚM META':              target_dot,
            'NOMBRE META':           t_name,
            'NÚM INDICADOR':         inid_dot,
            'NOMBRE INDICADOR NNUU': i_name,
            'INDICADOR DISPONIBLE':  ind_disponible,
        }

        # not_started y notapplicable: solo identificación + status
        if rs_norm in ('notstarted', 'notapplicable'):
            row = {f: '' for f in FIELDNAMES}
            row.update(base)
            row['REPORTING_STATUS'] = reporting_status
            rows.append(row)
            continue

        # --- Campos de config ---
        opts = config.get('progress_calculation_options', [])
        if not isinstance(opts, list):
            opts = []
        is_boolean      = any(isinstance(o, dict) and o.get('progress_boolean') for o in opts)
        is_goldilocks   = any(isinstance(o, dict) and 'goldilocks_transform' in o for o in opts)
        is_non_stat     = bool(config.get('data_non_statistical', False))

        graph_type = config.get('graph_type', '')
        show_map   = bool_val(config.get('data_show_map', False))
        related    = config.get('embedded_feature_tab_title', '')

        # Desagregaciones: columnas del CSV
        csv_path     = os.path.join(data_dir, f'indicator_{inid_dash}.csv')
        has_sexo     = 'no'
        has_th       = 'no'
        has_municipio = 'no'
        if os.path.exists(csv_path):
            try:
                df_cols    = pd.read_csv(csv_path, dtype=str, nrows=1)
                cols_lower = [c.lower() for c in df_cols.columns]
                has_sexo      = 'si' if 'sexo'      in cols_lower else 'no'
                has_th        = 'si' if 'territorio' in cols_lower else 'no'
                has_municipio = 'si' if 'municipio'  in cols_lower else 'no'
            except Exception:
                pass

        # Periodicidad y OCECA desde meta YAML
        periodicidad = resolve_fuente(meta_yaml.get('periodicidad', ''))
        texto_oceca_raw = meta_yaml.get('texto_oceca', '')
        if texto_oceca_raw == 'FUENTE.oceca':
            texto_oceca = 'si' if t_fuente.get('oceca', '').strip() else 'no'
        else:
            texto_oceca = 'si' if texto_oceca_raw and str(texto_oceca_raw).strip() else 'no'

        # Año inicial: mínimo año del CSV
        ano_inicial = ''
        if os.path.exists(csv_path):
            try:
                df_years = pd.read_csv(csv_path, dtype=str, usecols=['Year'])
                min_year = df_years['Year'].dropna().astype(int).min()
                ano_inicial = int(min_year) if pd.notna(min_year) else ''
            except Exception:
                pass

        fecha_act = config.get('national_data_updated_date',
                               meta_yaml.get('national_data_updated_date', ''))

        config_fields = {
            'REPORTING_STATUS':           reporting_status,
            'BOOLEANO':                   bool_val(is_boolean),
            'GOLDILOCK':                  bool_val(is_goldilocks),
            'INDICADOR NO ESTADÍSTICO':   bool_val(is_non_stat),
            'TIPO GRÁFICO':               graph_type,
            'MAPA':                       show_map,
            'INDICADORES RELACIONADOS':   related,
            'DESAGREGACIÓN SEXO':         has_sexo,
            'DESAGREGACIÓN TH':           has_th,
            'DESAGREGACIÓN MUNICIPIO':    has_municipio,
            'AÑO INICIAL':                ano_inicial,
            'PERIODICIDAD':               periodicidad,
            'TEXTO OCECA':                texto_oceca,
            'FECHA ÚLTIMA ACTUALIZACIÓN DATOS': fecha_act,
        }

        # --- Series ---
        series_list = get_series_for_indicator(inid_dash)
        if not series_list:
            series_list = [None]  # serie única → fila con SERIE vacía

        for serie_code in series_list:
            row = {f: '' for f in FIELDNAMES}
            row.update(base)
            row.update(config_fields)

            if serie_code:
                row['SERIE']        = serie_code
                row['NOMBRE SERIE'] = serie_nombre(serie_code)

            # --- Progreso ---
            auto_prog = config.get('auto_progress_calculation', False)
            if not is_non_stat and auto_prog:
                # Buscar datos de la serie en all.json
                # Para serie única el tag es el propio inid_dash
                # Para multiserie el tag es el código de serie
                tag = serie_code if serie_code else inid_dash
                serie_data = meta.get(tag) or {}

                # Dirección desde progress_calculation_options
                direction = ''
                for opt in opts:
                    if not isinstance(opt, dict):
                        continue
                    opt_series = opt.get('series', '')
                    if not serie_code or not opt_series or opt_series == serie_code:
                        dv = opt.get('direction', '')
                        direction = 'ascenso' if dv == 'positive' else ('descenso' if dv == 'negative' else '')
                        break

                # Target y limit desde progress_calculation_options
                target_val = ''
                limit_val  = ''
                for opt in opts:
                    if not isinstance(opt, dict):
                        continue
                    opt_series = opt.get('series', '')
                    if not serie_code or not opt_series or opt_series == serie_code:
                        t_v = opt.get('target')
                        l_v = opt.get('limit')
                        target_val = '' if t_v is None else t_v
                        limit_val  = '' if l_v is None else l_v
                        break

                row['DIRECCIÓN DESEADA']   = direction
                row['PROGRESO AUTOMÁTICO'] = 'si'
                row['AÑO BASE PROGRESO']   = serie_data.get('base_year', '')
                row['ÚLTIMO AÑO']          = serie_data.get('current_year', '')
                row['ÚLTIMO DATO']         = serie_data.get('current_value', '')
                row['TARGET']              = target_val
                row['LIMIT']               = limit_val
                # SCORE: nivel indicador (media de series), guardado en meta raíz
                row['SCORE']               = meta.get('score', '')
            else:
                row['PROGRESO AUTOMÁTICO'] = 'no'

            rows.append(row)

    # --- Escribir CSV con BOM para que Excel abra tildes correctamente ---
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[EUSTAT] >>> tabla_resumen.csv generado: {len(rows)} filas <<<")


# ---------------------------------------------------------------------------

# Goldilocks: precalcular columna Progress en CSVs antes del build
apply_goldilocks_transforms(data_dir='data', config_dir='indicator-config')

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
    indicator_rows = []
    for inid, meta in all_meta.items():
        goal = str(meta.get('goal_number', '')).strip()
        status = meta.get('progress_status', 'not_available')
        status = legacy_map.get(status, status)  # unificar
        if not goal or not goal.isdigit():
            continue
        counts[goal][status] += 1
        counts['overall'][status] += 1
        indicator_rows.append({'indicator': inid, 'goal': goal, 'status': status})

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

    # Generar progreso_indicadores.csv: una fila por indicador con su estado y objetivo
    indicator_rows_sorted = sorted(indicator_rows, key=lambda x: (int(x['goal']), x['indicator']))
    with open('_site/progreso_indicadores.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['indicator', 'goal', 'status'])
        writer.writeheader()
        writer.writerows(indicator_rows_sorted)

    print(f"[EUSTAT] >>> progreso_indicadores.csv generado: {len(indicator_rows_sorted)} filas <<<")

    # Generar tabla_resumen.csv: una fila por serie con todos los campos acordados
    generate_tabla_resumen(all_meta)

except Exception as e:
    print(f"[EUSTAT] !!! Error generando progreso.csv: {e} !!!")
