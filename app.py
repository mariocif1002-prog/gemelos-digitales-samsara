import requests
import json
import time
from datetime import datetime
import os
import streamlit as st
import pandas as pd
import base64 # <-- VUELVE
from streamlit.components.v1 import html # <-- VUELVE
# import pydeck as pdk <-- ELIMINADO (ya no hay mapa)
from streamlit_autorefresh import st_autorefresh # Para auto-refresh

# --- CONFIGURACIÓN DE PÁGINA (¡DEBE SER LO PRIMERO!) ---
st.set_page_config(layout="wide", page_title="Gemelos Digitales de Flota")

# --- CSS PERSONALIZADO ---
st.markdown("""
<style>
/* Estilo para el contenedor de la métrica vertical */
.metric-container {
    background-color: #262730; /* Color de fondo similar al tema oscuro de Streamlit */
    border-radius: 0.5rem;      /* Bordes redondeados */
    padding: 10px 12px;         /* Relleno interno */
    margin-bottom: 10px;        /* Espacio inferior */
    border: 1px solid #31333F; /* Borde sutil */
}
/* Estilo para la etiqueta (ej. "Temp. Motor") */
.metric-label {
    font-size: 0.85rem;          /* Fuente más pequeña para la etiqueta */
    color: #A0A0A0;              /* Color grisáceo para la etiqueta */
    font-weight: bold;
    margin-bottom: 4px;         /* Espacio entre etiqueta y valor */
}
/* Estilo para el valor (ej. "85.0 °C") */
.metric-value {
    font-size: 1.25rem;          /* Fuente grande para el valor */
    color: #FAFAFA;              /* Color blanco para el valor */
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)


# --- CONFIGURACIÓN GENERAL ---
try:
    SAMSARA_API_TOKEN = st.secrets["SAMSARA_API_TOKEN"]
except KeyError:
    st.error("Error: La clave 'SAMSARA_API_TOKEN' no se encontró en los secretos de Streamlit. "
             "Por favor, configura tu token de Samsara en .streamlit/secrets.toml.")
    st.stop()

# Usaremos un mapa que no requiere token, pero dejamos la variable por si se quiere usar Mapbox en el futuro.
MAPBOX_API_TOKEN = st.secrets.get("MAPBOX_API_TOKEN", None)


BASE_URL = "https://api.samsara.com/fleet"
HEADERS = {
    "Authorization": f"Bearer {SAMSARA_API_TOKEN}",
    "Content-Type": "application/json"
}

# --- ¡VUELVE! Mapa de Modelos 3D ---
# Asocia el string del 'modelo' de Samsara con tu archivo .glb
# ¡DEBES ACTUALIZAR ESTO con tus propios modelos y nombres de archivo!
MODEL_MAP = {
    # Ejemplo (debes cambiar "Cascadia" por como aparezca en tu app)
    "Cascadia": "modelos_3d/CASCADIA.glb", 
    "T680": "modelos_3d/kenworth_t680.glb",
    "VNL": "modelos_3d/volvo_vnl.glb",
    # ... añade más modelos aquí ...
    
    # Un modelo por defecto si no se encuentra
    "default": "truck5.glb" # ¡Asegúrate de tener esta imagen!
}


# --- Cargar las definiciones de DTCs ---
DTC_DEFINITIONS = {}
try:
    with open("dtc_definitions.json", "r", encoding='utf-8') as f:
        DTC_DEFINITIONS = json.load(f)
except FileNotFoundError:
    st.warning("Advertencia: El archivo 'dtc_definitions.json' no se encontró. Las descripciones de DTCs no estarán disponibles.")
except json.JSONDecodeError:
    st.error("Error: El archivo 'dtc_definitions.json' está mal formateado. No se pudieron cargar las descripciones de DTCs.")
except Exception as e:
    st.error(f"Error inesperado al cargar dtc_definitions.json: {e}")


# --- FUNCIÓN PARA OBTENER *TODOS* LOS VEHÍCULOS ---
@st.cache_data(ttl=3600, show_spinner=False) # Cachear por 1 hora, SIN SPINNER
def get_all_vehicle_details_list():
    """
    Obtiene la lista completa de vehículos (ID, nombre, etc.) de la flota.
    """
    # ¡NUEVO! Solo mostrar spinners/mensajes en la carga inicial
    show_messages = 'initial_load_complete' not in st.session_state or not st.session_state.initial_load_complete
    
    if show_messages:
        st.info("Obteniendo lista completa de vehículos de la flota...")
        
    endpoint = f"{BASE_URL}/vehicles"
    all_vehicles = []
    next_cursor = None
    page = 1

    while True:
        params = {}
        if next_cursor:
            params['after'] = next_cursor

        try:
            response = requests.get(endpoint, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            current_page_vehicles = data.get('data', [])
            all_vehicles.extend(current_page_vehicles)
            
            pagination = data.get('pagination', {})
            next_cursor = pagination.get('endCursor')
            
            if not next_cursor:
                break # Salir del bucle si no hay más páginas
            
            page += 1

        except requests.exceptions.RequestException as e:
            st.error(f"Error al obtener la lista de vehículos (Página {page}): {e}")
            break # Salir del bucle en caso de error

    if show_messages:
        st.success(f"¡Lista de flota obtenida! Se encontraron {len(all_vehicles)} vehículos.")
        
    return all_vehicles


# --- Función para obtener datos de MÚLTIPLES vehículos (OPTIMIZADA) ---
@st.cache_data(ttl=55, show_spinner=False) # TTL más corto (55s), SIN SPINNER
def fetch_samsara_data_multiple_vehicles(vehicle_ids_to_fetch):
    
    # Mapas para almacenar los datos dinámicos
    all_vehicle_locations = {}
    all_vehicle_stats_map = {}
    all_vehicle_maintenance_map = {}

    if not vehicle_ids_to_fetch:
        st.warning("No se proporcionaron IDs de vehículos para buscar datos.")
        return {}, {}, {}

    # --- 1. Obtener Ubicaciones (Optimizado) ---
    all_vehicle_locations = get_vehicle_locations(vehicle_ids_to_fetch)
    if not all_vehicle_locations:
        st.warning("No se pudieron obtener datos de ubicación.")

    # --- 2. Obtener Mantenimiento (Optimizado) ---
    all_vehicle_maintenance_map = get_all_vehicle_maintenance_data(vehicle_ids_to_fetch)
    if not all_vehicle_maintenance_map:
        st.warning("No se pudieron obtener datos de mantenimiento (DTCs).")

    # --- 3. Obtener Estadísticas (Optimizado) ---
    all_desired_stat_types = [
        'engineCoolantTemperatureMilliC',
        'ambientAirTemperatureMilliC',
        'engineRpm',
        'obdEngineSeconds',
        'engineOilPressureKPa'
    ]
    all_vehicle_stats_map = get_stats_for_multiple_vehicles(vehicle_ids_to_fetch, all_desired_stat_types)
    if not all_vehicle_stats_map:
        st.warning("No se pudieron obtener estadísticas del motor.")

    # Devolvemos los mapas llenos
    return all_vehicle_locations, all_vehicle_stats_map, all_vehicle_maintenance_map


# --- Función para obtener datos de UN SOLO vehículo (OPTIMIZADA) ---
@st.cache_data(ttl=55, show_spinner=False) # TTL corto, SIN SPINNER
def fetch_samsara_data_single_vehicle(vehicle_id_to_fetch):
    vehicle_locations = {}
    vehicle_stats = {}
    vehicle_maintenance_data = {}

    # Usar las funciones optimizadas de lote, pero solo con un ID
    vehicle_locations = get_vehicle_locations([vehicle_id_to_fetch])
    vehicle_maintenance_data = get_all_vehicle_maintenance_data([vehicle_id_to_fetch])
    
    all_desired_stat_types = [
        'engineCoolantTemperatureMilliC',
        'ambientAirTemperatureMilliC',
        'engineRpm',
        'obdEngineSeconds',
        'engineOilPressureKPa'
    ]
    vehicle_stats = get_stats_for_multiple_vehicles([vehicle_id_to_fetch], all_desired_stat_types)

    return vehicle_locations, vehicle_stats, vehicle_maintenance_data


# --- FUNCIONES DE API AUXILIARES (Optimizadas) ---

def get_vehicle_locations(vehicle_ids):
    """
    Obtiene ubicaciones para una lista de IDs de vehículos.
    """
    endpoint = f"{BASE_URL}/vehicles/locations"
    
    # La API de ubicaciones puede manejar múltiples IDs, pero a veces falla si son demasiados.
    # Es más seguro hacerlo en lotes de 100.
    locations_map = {}
    batch_size = 100
    
    for i in range(0, len(vehicle_ids), batch_size):
        batch_ids = vehicle_ids[i:i + batch_size]
        ids_str = ",".join(batch_ids)
        params = {'ids': ids_str}
        
        try:
            response = requests.get(endpoint, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            locations_data = response.json().get('data', [])
            
            for loc in locations_data:
                locations_map[loc['id']] = loc['location']
                
        except requests.exceptions.RequestException as e:
            st.warning(f"ERROR_LOG: Error al obtener lote de ubicaciones: {e}")
            continue # Continuar con el siguiente lote
            
    return locations_map

def get_stats_for_multiple_vehicles(vehicle_ids, stat_types):
    """
    Obtiene estadísticas para múltiples vehículos en lotes.
    """
    stats_map = {vid: {} for vid in vehicle_ids}
    endpoint = f"{BASE_URL}/vehicles/stats"
    
    # Dividir los tipos de estadísticas en lotes de 4
    stat_type_batches = [stat_types[i:i + 4] for i in range(0, len(stat_types), 4)]
    
    # Dividir los IDs de vehículos en lotes (ej. 100) para evitar URLs muy largas
    batch_size = 100
    
    for i in range(0, len(vehicle_ids), batch_size):
        batch_ids = vehicle_ids[i:i + batch_size]
        batch_ids_str = ",".join(batch_ids)
        
        try:
            for batch_of_types in stat_type_batches:
                params = {
                    "types": ",".join(batch_of_types),
                    "vehicleIds": batch_ids_str # ¡Clave de la optimización!
                }
                response = requests.get(endpoint, headers=HEADERS, params=params, timeout=15)
                response.raise_for_status()
                data = response.json().get('data', [])

                # Organizar los datos en el mapa
                for item in data:
                    vehicle_id = item.get('id')
                    if vehicle_id in stats_map:
                        for stat_type in batch_of_types:
                            if stat_type in item:
                                if isinstance(item[stat_type], dict) and 'value' in item[stat_type]:
                                    stats_map[vehicle_id][stat_type] = item[stat_type]['value']
                                else:
                                    stats_map[vehicle_id][stat_type] = item[stat_type]
            
        except requests.exceptions.RequestException as e:
            st.error(f"ERROR_LOG: Fallo al obtener stats por lotes: {e}")
            continue # Continuar con el siguiente lote de IDs
            
    return stats_map


def get_all_vehicle_maintenance_data(target_vehicle_ids):
    """
    Obtiene todos los datos de mantenimiento y los filtra por los IDs objetivo.
    Devuelve un MAPA {vehicle_id: maintenance_data}
    """
    endpoint = "https://api.samsara.com/v1/fleet/maintenance/list"
    next_cursor = None
    page_count = 0
    
    # Este mapa contendrá solo los vehículos que nos interesan
    maintenance_map = {}
    target_id_set = set(target_vehicle_ids) # Más rápido para buscar

    while True:
        page_count += 1
        params = {}
        if next_cursor:
            params['after'] = next_cursor

        try:
            response = requests.get(endpoint, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            response_data = response.json()
            
            current_page_items = response_data.get('vehicleMaintenance', [])
            if not current_page_items:
                current_page_items = response_data.get('vehicles', [])

            # Filtrar solo los vehículos que necesitamos EN ESTA PÁGINA
            for vehicle_item in current_page_items:
                vehicle_id = str(vehicle_item.get('id')) # Asegurar que sea string
                if vehicle_id in target_id_set:
                    maintenance_map[vehicle_id] = vehicle_item
                    target_id_set.remove(vehicle_id) # Dejamos de buscarlo

            pagination_info = response_data.get('pagination', {})
            next_cursor = pagination_info.get('endCursor')

            # Si ya encontramos todos o no hay más páginas, salimos
            if not next_cursor or not target_id_set:
                break
                
        except requests.exceptions.RequestException as e:
            st.error(f"ERROR_LOG: Fallo al obtener datos de mantenimiento (Página {page_count}): {e}")
            return {} # Devolver mapa vacío en caso de error

    return maintenance_map # Devolvemos el mapa filtrado


# --- LÓGICA DEL GEMELO DIGITAL Y DETECCIÓN DE ALERTA ---
def process_vehicle_data(vehicle_details, vehicle_locations, vehicle_stats, vehicle_maintenance_data):
    
    vehicle_id_str = str(vehicle_details.get('id', '')) # Asegurar que el ID sea string
    
    gemelo_digital = {
        'vehicle_id': vehicle_id_str,
        'vehicle_name': vehicle_details.get('name', 'N/A'),
        'make': vehicle_details.get('make', 'N/A'),
        'model': vehicle_details.get('model', 'N/A'),
        'year': vehicle_details.get('year', 'N/A'),
        'license_plate': vehicle_details.get('licensePlate', 'N/A'),
        'latitude': 'N/A', 'longitude': 'N/A', 'speed_mph': 'N/A', 'current_address': 'N/A',
        'gps_odometer_meters': 'N/A', 'location_updated_at': 'N/A',
        'engine_hours': 'N/A',
        'fuel_perc_remaining': 'N/A',
        'engine_oil_pressure_kpa': 'N/A',
        'engine_coolant_temperature_c': 'N/A',
        'engine_rpm': 'N/A',
        'ambient_air_temperature_c': 'N/A',
        'engine_check_light_warning': False,
        'engine_check_light_emissions': False,
        'engine_check_light_protect': False,
        'engine_check_light_stop': False,
        'diagnostic_trouble_codes': [],
        'last_data_sync': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'status_alert': 'OPERANDO NORMALMENTE',
        'alert_color': 'green'
    }

    # Intentar obtener datos de los mapas usando el ID string
    stats_data = vehicle_stats.get(vehicle_id_str, {})
    maintenance_data = vehicle_maintenance_data.get(vehicle_id_str, {})
    loc_data = vehicle_locations.get(vehicle_id_str)

    if loc_data:
        gemelo_digital['latitude'] = loc_data.get('latitude', 'N/A')
        gemelo_digital['longitude'] = loc_data.get('longitude', 'N/A')
        speed_value_loc = loc_data.get('speed')
        if isinstance(speed_value_loc, (int, float)):
            gemelo_digital['speed_mph'] = round(speed_value_loc, 2)
        else:
            gemelo_digital['speed_mph'] = 'N/A'
        gemelo_digital['current_address'] = loc_data.get('reverseGeo', {}).get('formattedLocation', 'N/A')
        loc_time_str = loc_data.get('time', 'N/A')
        if loc_time_str != 'N/A':
            try:
                gemelo_digital['location_updated_at'] = datetime.fromisoformat(loc_time_str.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                gemelo_digital['location_updated_at'] = loc_time_str
        else:
            gemelo_digital['location_updated_at'] = 'N/A'


    engine_seconds = stats_data.get('obdEngineSeconds')
    if isinstance(engine_seconds, (int, float)):
        gemelo_digital['engine_hours'] = round(engine_seconds / 3600, 2)
    else:
        gemelo_digital['engine_hours'] = 'N/A'

    gemelo_digital['fuel_perc_remaining'] = 'N/A'


    oil_pressure = stats_data.get('engineOilPressureKPa')
    if isinstance(oil_pressure, (int, float)):
        gemelo_digital['engine_oil_pressure_kpa'] = round(oil_pressure, 2)
    else:
        gemelo_digital['engine_oil_pressure_kpa'] = 'N/A'

    temp_c_milli = stats_data.get('engineCoolantTemperatureMilliC')
    if isinstance(temp_c_milli, (int, float)):
        gemelo_digital['engine_coolant_temperature_c'] = round(temp_c_milli / 1000, 2)
    else:
        gemelo_digital['engine_coolant_temperature_c'] = 'N/A'

    temp_ambient_milli = stats_data.get('ambientAirTemperatureMilliC')
    if isinstance(temp_ambient_milli, (int, float)):
        gemelo_digital['ambient_air_temperature_c'] = round(temp_ambient_milli / 1000, 2)
    else:
        gemelo_digital['ambient_air_temperature_c'] = 'N/A'

    engine_rpm_val = stats_data.get('engineRpm')
    if isinstance(engine_rpm_val, (int, float)):
        gemelo_digital['engine_rpm'] = engine_rpm_val
    else:
        gemelo_digital['engine_rpm'] = 'N/A'

    if maintenance_data:
        # ¡ARREGLO DE ERROR NoneType!
        # Asegurarse de que j1939_data sea un diccionario, incluso si la API devuelve 'null'
        j1939_data = maintenance_data.get('j1939') or {}
        
        check_engine_light_data = j1939_data.get('checkEngineLight', {})

        gemelo_digital['engine_check_light_warning'] = check_engine_light_data.get('warningIsOn', False)
        gemelo_digital['engine_check_light_emissions'] = check_engine_light_data.get('emissionsIsOn', False)
        gemelo_digital['engine_check_light_protect'] = check_engine_light_data.get('protectIsOn', False)
        gemelo_digital['engine_check_light_stop'] = check_engine_light_data.get('stopIsOn', False)

        dtcs_from_maintenance = j1939_data.get('diagnosticTroubleCodes', [])
        if isinstance(dtcs_from_maintenance, list):
            gemelo_digital['diagnostic_trouble_codes'] = dtcs_from_maintenance
        else:
            gemelo_digital['diagnostic_trouble_codes'] = []

    alerts = []

    if gemelo_digital['diagnostic_trouble_codes'] and isinstance(gemelo_digital['diagnostic_trouble_codes'], list) and len(gemelo_digital['diagnostic_trouble_codes']) > 0:
        dtc_codes_info_for_alert = []
        for code in gemelo_digital['diagnostic_trouble_codes']:
            spn = code.get('spnId', 'N/A')
            fmi = code.get('fmiId', 'N/A')
            dtc_codes_info_for_alert.append(f"SPN: {spn} (FMI: {fmi})")
        alerts.append(f"Fallas de motor (DTCs: {'; '.join(dtc_codes_info_for_alert)})")

    check_light_alerts = []
    if gemelo_digital['engine_check_light_warning']:
        check_light_alerts.append("Advertencia (Warning)")
    if gemelo_digital['engine_check_light_emissions']:
        check_light_alerts.append("Emisiones (Emissions)")
    if gemelo_digital['engine_check_light_protect']:
        check_light_alerts.append("Protección (Protect)")
    if gemelo_digital['engine_check_light_stop']:
        check_light_alerts.append("Detener (Stop)")

    if check_light_alerts:
        alerts.append(f"Luz de Check Engine ON ({', '.join(check_light_alerts)})")

    if alerts:
        gemelo_digital['status_alert'] = "ALERTA: " + '; '.join(alerts)
        gemelo_digital['alert_color'] = 'red'
    elif gemelo_digital['status_alert'] != 'OFFLINE o SIN DATOS':
        gemelo_digital['status_alert'] = 'OPERANDO NORMALMENTE'
        gemelo_digital['alert_color'] = 'green'

    return gemelo_digital

# --- ¡VUELVE! Función para mostrar el visor 3D ---
def display_gltf_viewer(model_path, height=500):
    """
    Muestra un modelo 3D (GLB/GLTF) en el dashboard.
    """
    if not os.path.exists(model_path):
        st.error(f"Error: El archivo del modelo 3D '{model_path}' no se encontró.")
        st.warning(f"Asegúrate de tener un archivo .glb en la ruta: {os.path.abspath(model_path)}")
        st.warning(f"Tip: Asegúrate de que tu diccionario 'MODEL_MAP' (línea 78) apunte a archivos .glb reales en tu carpeta 'modelos_3d'.")
        # Mostrar el modelo por defecto si el específico falla
        default_path = MODEL_MAP.get("default", "truck5.glb")
        if os.path.exists(default_path):
            st.warning("Mostrando modelo 3D por defecto.")
            model_path = default_path
        else:
            st.error("Error: Tampoco se encontró el modelo por defecto 'truck5.glb'. El visor 3D no puede cargarse.")
            return

    try:
        with open(model_path, "rb") as f:
            model_bytes = f.read()
        model_b64 = base64.b64encode(model_bytes).decode("utf-8")
        data_url = f"data:model/gltf-binary;base64,{model_b64}"

        html_code = f"""
        <script type="module" src="https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"></script>
        <style>
          model-viewer {{
            width: 100%;
            height: {height}px;
            margin: 0;
            padding: 0;
            display: block;
            background-color: #262730; /* Fondo oscuro para que combine */
            --poster-color: #262730;
          }}
        </style>
        <model-viewer
          src="{data_url}"
          alt="Modelo 3D de Camión"
          auto-rotate
          camera-controls
          shadow-intensity="1"
          exposure="1"
          ar
          ar-modes="webxr scene-viewer quick-look"
          camera-orbit="0deg 90deg 100%"
          field-of-view="30deg"
          min-field-of-view="20deg"
          max-field-of-view="60deg"
          interpolation-decay="200"
          shadow-softness="0.5"
          auto-rotate-delay="1000"
          interaction-prompt="none"
          camera-target="0.0m 0.5m 0.0m"
        ></model-viewer>
        """
        html(html_code, height=height, width=None, scrolling=False)
    except Exception as e:
        st.error(f"Error inesperado al cargar el modelo 3D: {e}")


# --- APLICACIÓN STREAMLIT ---
st.title("🚚 Gemelos Digitales de Flota (Samsara)")

# --- Auto-refresh cada 60 segundos ---
st_autorefresh(interval=60 * 1000, key="datarefresh") # 60 segundos

# --- BARRA LATERAL ---
with st.sidebar:
    st.image("https://assets-global.website-files.com/60ae107d3b5c65b3f14b679c/60b001f4e5c83e1c8b360f03_logo-grey.svg", width=200)
    st.title("Controles de Flota")
    
    # Placeholder para el selector de vehículo. Se llenará después de cargar los datos.
    vehicle_selector_placeholder = st.empty()

    if st.button("Actualizar Datos Manualmente"):
        st.cache_data.clear() # Limpiar toda la caché
        st.session_state.initial_load_complete = False # Forzar spinners en la próxima recarga
        st.rerun() # Reiniciar la app para forzar la recarga

    st.markdown("---")
    st.markdown(f"Última actualización: `{datetime.now().strftime('%H:%M:%S')}`")


# --- Inicialización del estado de sesión ---
if 'all_gemelos_digitales' not in st.session_state:
    st.session_state.all_gemelos_digitales = {}
if 'all_vehicle_details' not in st.session_state:
    st.session_state.all_vehicle_details = []
if 'initial_load_complete' not in st.session_state:
    st.session_state.initial_load_complete = False # Flag para actualización silenciosa


# --- LÓGICA DE CARGA DE DATOS (OPTIMIZADA) ---

# 1. Obtener la lista de detalles de vehículos (se cachea por 1 hora)
vehicle_details_list = get_all_vehicle_details_list()

if vehicle_details_list:
    st.session_state.all_vehicle_details = vehicle_details_list
    vehicle_ids = [str(v.get('id')) for v in vehicle_details_list] # Lista de IDs
    
    # 2. Obtener datos dinámicos (se cachea por 55 seg)
    # ¡NUEVO! Solo mostrar spinner si no es una actualización silenciosa
    if not st.session_state.initial_load_complete:
        with st.spinner(f"Cargando datos dinámicos para {len(vehicle_ids)} vehículos..."):
            locations, stats, maintenance = fetch_samsara_data_multiple_vehicles(vehicle_ids)
    else:
        # Carga silenciosa (sin spinner)
        locations, stats, maintenance = fetch_samsara_data_multiple_vehicles(vehicle_ids)

    
    # 3. Procesar y construir los gemelos
    temp_gemelos_map = {}
    for details in st.session_state.all_vehicle_details:
        gemelo = process_vehicle_data(details, locations, stats, maintenance)
        temp_gemelos_map[gemelo['vehicle_id']] = gemelo
    
    st.session_state.all_gemelos_digitales = temp_gemelos_map
    
    # ¡NUEVO! Marcar la carga inicial como completada
    st.session_state.initial_load_complete = True
    
else:
    st.error("No se pudieron cargar los vehículos de la flota. Revisa el token de API y los permisos.")
    st.stop()


# --- PÁGINA PRINCIPAL ---

# --- Mostrar Resumen de la Flota ---
df_fleet = pd.DataFrame(list(st.session_state.all_gemelos_digitales.values()))

st.subheader("Resumen de la Flota")
if not df_fleet.empty:
    # --- ¡ARREGLO PARA EL CRASH DE ARROW! ---
    # Convertir columnas con 'N/A' a numérico, 'coerce' convierte 'N/A' en NaN (Not-a-Number)
    df_fleet['engine_coolant_temperature_c'] = pd.to_numeric(df_fleet['engine_coolant_temperature_c'], errors='coerce')
    df_fleet['speed_mph'] = pd.to_numeric(df_fleet['speed_mph'], errors='coerce')
    
    # Columnas a mostrar en el resumen
    summary_cols = ['vehicle_name', 'make', 'model', 'status_alert',
                    'engine_coolant_temperature_c',
                    'speed_mph', 'current_address', 'last_data_sync']

    # Asegurarse de que solo mostramos columnas que existen
    display_cols = [col for col in summary_cols if col in df_fleet.columns]
    
    st.dataframe(df_fleet[display_cols], width='stretch') # ¡ARREGLADO! 'stretch' usa el ancho del contenedor
else:
    st.warning("No hay datos de vehículos disponibles para mostrar en el resumen de la flota.")

st.markdown("---")

# --- Llenar el selector de vehículo en la barra lateral ---
if not df_fleet.empty:
    vehicle_names = df_fleet['vehicle_name'].tolist()
    # Ordenar alfabéticamente
    vehicle_names.sort()
    selected_vehicle_name = vehicle_selector_placeholder.selectbox(
        "Selecciona un vehículo para ver detalles:", 
        vehicle_names, 
        key='selected_vehicle_detail'
    )
else:
    selected_vehicle_name = vehicle_selector_placeholder.selectbox(
        "Selecciona un vehículo para ver detalles:", 
        ["No hay vehículos cargados"], 
        key='selected_vehicle_detail'
    )

# --- Mostrar Detalle del Vehículo Seleccionado ---
st.subheader("Detalle del Gemelo Digital")
if selected_vehicle_name and selected_vehicle_name != "No hay vehículos cargados":
    
    selected_vehicle_data = df_fleet[df_fleet['vehicle_name'] == selected_vehicle_name].to_dict('records')[0]
    
    if selected_vehicle_data:
        # Definir 2 columnas: Detalles y Modelo 3D
        col_details, col_3d_model = st.columns([1.2, 1], gap="large")

        with col_details:
            st.write(f"### {selected_vehicle_name}")
            st.write(f"**Estado:** <span style='color:{selected_vehicle_data.get('alert_color', 'gray')}; font-weight:bold;'>{selected_vehicle_data.get('status_alert', 'N/A')}</span>", unsafe_allow_html=True)
            
            # --- Métricas Verticales (con CSS) ---
            st.markdown("---")
            
            # Procesar y redondear valores ANTES de pasarlos
            temp_motor_val = selected_vehicle_data.get('engine_coolant_temperature_c', 'N/A')
            temp_motor_str = f"{temp_motor_val:.1f} °C" if isinstance(temp_motor_val, (int, float)) else "N/A °C"
            
            rpm_val = selected_vehicle_data.get('engine_rpm', 'N/A')
            rpm_str = str(rpm_val) if isinstance(rpm_val, (int, float)) else "N/A"

            vel_val = selected_vehicle_data.get('speed_mph', 'N/A')
            vel_str = f"{vel_val:.1f} MPH" if isinstance(vel_val, (int, float)) else "N/A MPH"

            aceite_val = selected_vehicle_data.get('engine_oil_pressure_kpa', 'N/A')
            aceite_str = f"{aceite_val:.1f} KPa" if isinstance(aceite_val, (int, float)) else "N/A KPa"

            horas_val = selected_vehicle_data.get('engine_hours', 'N/A')
            horas_str = f"{horas_val:.1f} hrs" if isinstance(horas_val, (int, float)) else "N/A hrs"

            lat = selected_vehicle_data.get('latitude', 'N/A')
            lon = selected_vehicle_data.get('longitude', 'N/A')
            location_str = f"({lat:.4f}, {lon:.4f})" if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else "(N/A)"

            # Usar 2 columnas para apilar las métricas
            met1, met2 = st.columns(2)
            
            with met1:
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">🌡️ Temp. Motor</div>
                    <div class="metric-value">{temp_motor_str}</div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">⚡ Velocidad</div>
                    <div class="metric-value">{vel_str}</div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">⏱️ Horas de Motor</div>
                    <div class="metric-value">{horas_str}</div>
                </div>
                """, unsafe_allow_html=True)

            with met2:
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">🔄 RPM Motor</div>
                    <div class="metric-value">{rpm_str}</div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">💧 Presión Aceite</div>
                    <div class="metric-value">{aceite_str}</div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown(f"""
                <div class="metric-container">
                    <div class="metric-label">📍 Ubicación</div>
                    <div class="metric-value">{location_str}</div>
                </div>
                """, unsafe_allow_html=True)


            st.markdown("---")
            st.write(f"**Dirección Actual:** {selected_vehicle_data.get('current_address', 'N/A')}")
            st.write(f"**Marca:** {selected_vehicle_data.get('make', 'N/A')}")
            st.write(f"**Modelo:** {selected_vehicle_data.get('model', 'N/A')}")
            st.write(f"**Año:** {selected_vehicle_data.get('year', 'N/A')}")
            st.write(f"**Última Sincronización:** {selected_vehicle_data.get('last_data_sync', 'N/A')}")


        with col_3d_model:
            st.write(f"### Modelo 3D")
            
            # --- ¡NUEVO! Lógica de Modelo Dinámico ---
            vehicle_model_name = selected_vehicle_data.get('model', 'N/A')
            
            # 1. Buscar el archivo .glb correspondiente
            model_path_to_display = MODEL_MAP.get(vehicle_model_name)
            
            # 2. Si no se encuentra, buscar por palabra clave
            if not model_path_to_display:
                for key, path in MODEL_MAP.items():
                    # Comprobar si la "clave" (ej. "Cascadia") está DENTRO del nombre del modelo (ej. "Cascadia 126")
                    if key.lower() in vehicle_model_name.lower():
                        model_path_to_display = path
                        break # Usar la primera coincidencia
            
            # 3. Si sigue sin encontrarse, usar el default
            if not model_path_to_display:
                model_path_to_display = MODEL_MAP.get("default", "truck5.glb")
            
            # 4. Mostrar el modelo 3D
            display_gltf_viewer(model_path_to_display, height=500)


        # --- Sección de DTCs y Luces (debajo de las 2 columnas) ---
        st.markdown("---")
        st.subheader("Códigos de Falla y Luces de Advertencia")

        dtcs = selected_vehicle_data.get('diagnostic_trouble_codes')
        if dtcs and isinstance(dtcs, list) and len(dtcs) > 0:
            st.warning(f"🚨 **DTCs Activos:**")
            
            num_columns_dtcs = 4
            cols_dtc = st.columns(num_columns_dtcs)
            col_idx_dtc = 0

            for dtc in dtcs:
                with cols_dtc[col_idx_dtc]:
                    spn = dtc.get('spnId', 'N/A')
                    fmi = dtc.get('fmiId', 'N/A')
                    occurrence = dtc.get('occurrenceCount', 'N/A')

                    dtc_key = f"SPN:{spn} FMI:{fmi}"
                    dtc_info = DTC_DEFINITIONS.get(dtc_key, {})
                    description = dtc_info.get('description', f"Descripción no disponible para {dtc_key}")
                    suggestion = dtc_info.get('suggestion', 'No hay sugerencia de solución.')

                    with st.popover(f"**{dtc_key}** (Ocurrencias: `{occurrence}`)", width='content'):
                        st.markdown(f"**Código:** {dtc_key}")
                        st.markdown(f"**Ocurrencias:** `{occurrence}`")
                        st.markdown(f"**Descripción:** {description}")
                        st.markdown(f"**Sugerencia de Solución:** {suggestion}")
                
                col_idx_dtc = (col_idx_dtc + 1) % num_columns_dtcs
        else:
            st.info("✅ **DTCs:** Ninguno activo")

        # Luces de Check Engine
        st.write("🚦 **Luces de Check Engine:**")
        check_light_alerts = []
        if selected_vehicle_data.get('engine_check_light_warning'):
            check_light_alerts.append("- 🟠 Advertencia (Warning) ON")
        if selected_vehicle_data.get('engine_check_light_emissions'):
            check_light_alerts.append("- 💨 Emisiones (Emissions) ON")
        if selected_vehicle_data.get('engine_check_light_protect'):
            check_light_alerts.append("- 🛡️ Protección (Protect) ON")
        if selected_vehicle_data.get('engine_check_light_stop'):
            check_light_alerts.append("- 🛑 ¡Detener (Stop) ON!")
        
        if check_light_alerts:
            num_columns_lights = 2
            cols_lights = st.columns(num_columns_lights)
            col_idx_lights = 0
            for alert_text in check_light_alerts:
                with cols_lights[col_idx_lights]:
                    if "🛑" in alert_text:
                        st.error(alert_text)
                    else:
                        st.warning(alert_text)
                col_idx_lights = (col_idx_lights + 1) % num_columns_lights
        else:
            st.info("- 🟢 Ninguna luz de Check Engine activa.")

    else:
        st.warning("No se pudieron encontrar datos para el vehículo seleccionado.")
else:
    st.warning("No hay datos de vehículos disponibles para mostrar el detalle del camión.")