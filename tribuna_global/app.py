"""Tribuna Global: interfaz Streamlit para registrar y consultar opiniones sobre futbolistas."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import streamlit as st

from analisis import (
    VERSION_PROMPT,
    AnalisisOpinion,
    ErrorClasificacion,
    ErrorEntrada,
    analizar_opinion,
    construir_cadena,
    nombre_modelo,
    validar_texto,
)
from almacenamiento import (
    DIR_DATOS,
    RUTA_JUGADORES,
    RUTA_OPINIONES,
    SENTIMIENTOS,
    ErrorAlmacenamiento,
    ErrorCatalogo,
    cargar_jugadores,
    cargar_opiniones,
    consultar_opiniones,
    contar_por_sentimiento,
    crear_registro,
    guardar_opinion,
)

st.set_page_config(page_title="Tribuna Global", page_icon=":material/sports_soccer:", layout="wide")

DIR_FOTOS = DIR_DATOS / "fotos"
COLORES = {"positivo": "green", "negativo": "red", "neutro": "gray", "mixto": "orange"}
SECCION_EMITIR = "Emitir opinión"
SECCION_CONSULTAR = "Consultar opiniones"

# Tipografía del título: Bebas Neue (Google Fonts), con Impact como respaldo sin conexión.
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&display=swap');
    .tg-titulo {
        font-family: 'Bebas Neue', Impact, 'Arial Narrow', sans-serif;
        font-size: 3.2rem !important;
        line-height: 1;
        letter-spacing: 0.06em;
        margin: 0 0 0.2rem 0;
    }
    .tg-subtitulo {
        font-size: 0.8rem !important;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        opacity: 0.6;
        margin-bottom: 1rem;
    }
    .tg-jugador {
        font-family: 'Bebas Neue', Impact, 'Arial Narrow', sans-serif;
        font-size: 2.6rem !important;
        line-height: 1.05;
        letter-spacing: 0.04em;
        margin: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Estado de sesión (solo el chat visible; la persistencia está en disco)
# ---------------------------------------------------------------------------
if "mensajes" not in st.session_state:
    st.session_state.mensajes = []  # historial visual de la sesión
if "pendiente" not in st.session_state:
    st.session_state.pendiente = None  # envío con error, disponible para reintento


@st.cache_resource(show_spinner=False)
def obtener_cadena(_modelo: str):
    """Se cachea por nombre de modelo para no reconstruir el cliente en cada rerun."""
    return construir_cadena()


# ---------------------------------------------------------------------------
# Fotos de los jugadores (archivos locales descargados de Wikimedia Commons)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def cargar_creditos() -> dict:
    try:
        return json.loads((DIR_FOTOS / "creditos.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}  # sin fotos la app sigue funcionando


def ruta_foto(jugador_id: str | None) -> Path | None:
    credito = cargar_creditos().get(jugador_id or "")
    if credito and (DIR_FOTOS / credito["archivo"]).exists():
        return DIR_FOTOS / credito["archivo"]
    return None


def texto_credito(jugador_id: str) -> str:
    c = cargar_creditos().get(jugador_id, {})
    autor = c.get("autor", "")
    autor = autor if len(autor) <= 45 else autor[:42] + "..."
    return f"Foto: {autor} · {c.get('licencia', '')} · Wikimedia Commons"


def tarjeta_jugador(jugador: dict, ancho_foto: int = 150) -> None:
    """Foto, nombre y país del jugador."""
    foto = ruta_foto(jugador["id"])
    col_foto, col_texto = st.columns([1, 4], vertical_alignment="center")
    with col_foto:
        if foto:
            st.image(str(foto), width=ancho_foto)
            st.caption(texto_credito(jugador["id"]))
    with col_texto:
        st.markdown(f'<div class="tg-jugador">{jugador["nombre"]}</div>', unsafe_allow_html=True)
        st.write(jugador["pais"])


# ---------------------------------------------------------------------------
# Historial del chat
# ---------------------------------------------------------------------------
def agregar_mensaje(
    rol: str,
    contenido: str,
    jugador: dict | None = None,
    tipo: str = "texto",
) -> None:
    st.session_state.mensajes.append(
        {
            "rol": rol,
            "contenido": contenido,
            "jugador": jugador["nombre"] if jugador else None,
            "jugador_id": jugador["id"] if jugador else None,
            "tipo": tipo,
        }
    )


def mostrar_mensaje(mensaje: dict) -> None:
    if mensaje["rol"] == "user":
        foto = ruta_foto(mensaje.get("jugador_id"))
        with st.chat_message("user", avatar=str(foto) if foto else None):
            st.caption(f"Opinión sobre **{mensaje['jugador']}**")
            st.markdown(mensaje["contenido"])
        return
    with st.chat_message("assistant"):
        mostrar = {"exito": st.success, "error": st.error, "aviso": st.warning, "info": st.info}
        mostrar.get(mensaje["tipo"], st.markdown)(mensaje["contenido"])


def etiqueta_sentimiento(sentimiento: str) -> str:
    return f":{COLORES[sentimiento]}[**Sentimiento: {sentimiento}**]"


# ---------------------------------------------------------------------------
# Procesamiento de un envío (Actividad 7: errores separados por etapa)
# ---------------------------------------------------------------------------
def procesar_envio(envio: dict) -> None:
    """Valida, clasifica y guarda un envío. Deja `pendiente` si el error es recuperable."""
    jugador = envio["jugador"]

    # 1) Entrada: no se llama al modelo ni se guarda.
    try:
        texto = validar_texto(envio["texto"])
    except ErrorEntrada as exc:
        agregar_mensaje("assistant", f"**Entrada no válida.** {exc}", tipo="aviso")
        st.session_state.pendiente = None
        return

    # 2) Clasificación (se reutiliza si ya se obtuvo en un intento anterior).
    if envio.get("analisis") is None:
        try:
            with st.spinner(f"Analizando la opinión sobre {jugador['nombre']}..."):
                cadena = obtener_cadena(nombre_modelo())
                analisis = analizar_opinion(jugador, texto, cadena=cadena)
        except ErrorClasificacion as exc:
            agregar_mensaje(
                "assistant",
                f"**Error de clasificación.** {exc}\n\nLa opinión **no** se guardó. Puede reintentar.",
                tipo="error",
            )
            st.session_state.pendiente = envio
            return
        envio["analisis"] = analisis.model_dump()
    analisis = AnalisisOpinion.model_validate(envio["analisis"])

    # 3) Pertinencia: un mensaje ajeno no se guarda como neutro.
    if not analisis.es_pertinente:
        agregar_mensaje(
            "assistant",
            f"**Opinión no pertinente** para {jugador['nombre']}: {analisis.razon}\n\nNo se guardó.",
            tipo="info",
        )
        st.session_state.pendiente = None
        return

    # 4) Almacenamiento. El id del registro es el id del envío: un reintento no duplica.
    if envio.get("registro") is None:
        envio["registro"] = crear_registro(
            jugador,
            texto,
            analisis,
            modelo=nombre_modelo(),
            version_prompt=VERSION_PROMPT,
            registro_id=envio["envio_id"],
        )
    resultado = f"{etiqueta_sentimiento(analisis.sentimiento)}\n\n{analisis.razon}"
    try:
        escrito = guardar_opinion(RUTA_OPINIONES, envio["registro"])
    except ErrorAlmacenamiento as exc:
        agregar_mensaje(
            "assistant",
            f"{resultado}\n\n**Error de almacenamiento.** {exc}\n\n"
            "La opinión **no** se guardó. Puede reintentar el guardado.",
            tipo="error",
        )
        st.session_state.pendiente = envio
        return

    nota = "**Opinión guardada.**" if escrito else "Este envío ya estaba guardado; no se duplicó."
    agregar_mensaje("assistant", f"{resultado}\n\n{nota}", tipo="exito")
    st.session_state.pendiente = None


# ---------------------------------------------------------------------------
# Actividad 1. Catálogo
# ---------------------------------------------------------------------------
try:
    jugadores = cargar_jugadores(RUTA_JUGADORES)
except ErrorCatalogo as exc:
    st.error(f"No se pudo cargar el catálogo de jugadores: {exc}")
    st.stop()

por_id = {j["id"]: j for j in jugadores}

# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="tg-titulo">Tribuna Global</div>'
        '<div class="tg-subtitulo">La voz de la afición</div>',
        unsafe_allow_html=True,
    )
    seccion = st.radio("Sección", [SECCION_EMITIR, SECCION_CONSULTAR])
    st.selectbox(
        "Jugador",
        options=list(por_id),  # el valor interno es el identificador
        format_func=lambda jid: f"{por_id[jid]['nombre']} ({por_id[jid]['pais']})",
        key="jugador_id",
    )
    if st.button("Nueva conversación", width="stretch"):
        st.session_state.mensajes = []
        st.session_state.pendiente = None
        st.rerun()  # solo limpia la sesión; opiniones.json no se modifica
    st.divider()
    st.caption(f"Modelo: `{nombre_modelo() or 'sin configurar'}` · prompt v{VERSION_PROMPT}")
    st.caption(
        "Catálogo didáctico, no una clasificación oficial. Las cifras solo reflejan "
        "las opiniones registradas en esta aplicación."
    )

# ---------------------------------------------------------------------------
# Actividad 2. Emitir opinión
# ---------------------------------------------------------------------------
if seccion == SECCION_EMITIR:
    jugador_actual = por_id[st.session_state.jugador_id]
    st.header("Emitir opinión")
    tarjeta_jugador(jugador_actual)
    st.caption("Cada mensaje se analiza de forma independiente.")

    for mensaje in st.session_state.mensajes:
        mostrar_mensaje(mensaje)

    pendiente = st.session_state.pendiente
    if pendiente:
        st.warning(
            f"Envío pendiente para **{pendiente['jugador']['nombre']}**: «{pendiente['texto'].strip()}»"
        )
        col1, col2 = st.columns(2)
        if col1.button("Reintentar", width="stretch"):
            procesar_envio(pendiente)
            st.rerun()
        if col2.button("Descartar", width="stretch"):
            st.session_state.pendiente = None
            st.rerun()

    texto = st.chat_input(f"Escriba su opinión sobre {jugador_actual['nombre']}...", max_chars=2000)
    if texto is not None:
        # La selección se captura en este momento; cambiarla después no altera el envío.
        envio = {
            "envio_id": str(uuid.uuid4()),
            "jugador": dict(jugador_actual),
            "texto": texto,
            "analisis": None,
            "registro": None,
        }
        agregar_mensaje("user", texto.strip() or "(mensaje vacío)", jugador=jugador_actual)
        procesar_envio(envio)
        st.rerun()

# ---------------------------------------------------------------------------
# Actividad 6. Consultar opiniones (sin llamadas al modelo)
# ---------------------------------------------------------------------------
else:
    st.header("Consultar opiniones")
    try:
        datos = cargar_opiniones(RUTA_OPINIONES)
    except ErrorAlmacenamiento as exc:
        st.error(f"No se pudieron cargar las opiniones: {exc}")
        st.stop()

    registros = datos["opiniones"]

    # Jugadores del catálogo + los que existan en registros aunque ya no estén en el catálogo.
    nombres = {jid: j["nombre"] for jid, j in por_id.items()}
    for r in registros:
        nombres.setdefault(r["jugador_id"], r["jugador_nombre"])

    col1, col2 = st.columns(2)
    filtro_jugador = col1.selectbox(
        "Jugador",
        options=[None, *nombres],
        format_func=lambda jid: "Todos" if jid is None else nombres[jid],
    )
    filtro_sentimiento = col2.selectbox(
        "Sentimiento",
        options=[None, *SENTIMIENTOS],
        format_func=lambda s: "Todos" if s is None else s.capitalize(),
    )

    if filtro_jugador in por_id:
        tarjeta_jugador(por_id[filtro_jugador], ancho_foto=110)

    filtrados = consultar_opiniones(registros, filtro_jugador, filtro_sentimiento)
    filtrados = sorted(filtrados, key=lambda r: r["fecha_utc"], reverse=True)
    conteo = contar_por_sentimiento(filtrados)  # mismos filtros que la tabla

    metricas = st.columns(5)
    metricas[0].metric("Total filtrado", len(filtrados))
    for col, s in zip(metricas[1:], SENTIMIENTOS):
        col.metric(s.capitalize(), conteo[s])

    if not filtrados:
        st.info("No hay opiniones que coincidan con los filtros seleccionados.")
    else:
        st.dataframe(
            [
                {
                    "Fecha (UTC)": r["fecha_utc"],
                    "Jugador": r["jugador_nombre"],
                    "Opinión original": r["opinion_original"],
                    "Sentimiento": r["sentimiento"],
                    "Razón": r["razon"],
                }
                for r in filtrados
            ],
            width="stretch",
            hide_index=True,
        )

    exportacion = {
        "version": 1,
        "filtros": {"jugador_id": filtro_jugador, "sentimiento": filtro_sentimiento},
        "total": len(filtrados),
        "conteo_por_sentimiento": conteo,
        "opiniones": filtrados,
    }
    st.download_button(
        "Descargar resultados (JSON)",
        data=json.dumps(exportacion, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="opiniones_filtradas.json",
        mime="application/json",
        disabled=not filtrados,
    )
    st.caption(f"Archivo: `{RUTA_OPINIONES.name}` · {len(registros)} registros en total.")
