"""Página 0: delineamento automático de bacia pelo exutório."""
from __future__ import annotations

import os
from pathlib import Path

import folium
import streamlit as st
from streamlit_folium import st_folium

from chuva_vazao import basin


st.title("0. Bacia de Contribuição")
st.caption(
    "Clique no mapa para marcar o exutório e delinear a bacia automaticamente "
    "(WhiteboxTools + DEM Copernicus ou local). As métricas resultantes podem "
    "ser aplicadas à Página 3 (Chuva-Vazão) para cálculo de tc e área."
)


# ---------------------------------------------------------------------------
# Parametros
# ---------------------------------------------------------------------------

default_lat = float(st.session_state.get("exutorio_lat", -22.68))
default_lon = float(st.session_state.get("exutorio_lon", -44.32))

st.subheader("Fonte do DEM")
fonte_dem = st.radio(
    "Origem dos dados topográficos:",
    [
        "Google Earth Engine (Copernicus GLO-30) — recomendado",
        "Upload local (GeoTIFF)",
        "OpenTopography API (Copernicus GLO-30)",
    ],
    horizontal=False,
)

dem_path: Path | None = None
if fonte_dem.startswith("Google"):
    col1, col2 = st.columns(2)
    buffer = col1.number_input("Buffer (graus)", 0.02, 1.0, 0.1, 0.01, key="gee_buffer")
    download_btn = col2.button("Baixar DEM via GEE")
    if download_btn:
        with st.spinner(f"Puxando Copernicus GLO-30 via GEE (buffer={buffer}°)..."):
            try:
                dem_path = basin.download_dem_gee(
                    lat=default_lat, lon=default_lon, buffer_deg=buffer,
                )
                st.session_state["_dem_path"] = str(dem_path)
                st.success(f"DEM salvo em {dem_path}")
            except Exception as exc:
                st.error(
                    f"Falhou: {exc}\n\n"
                    "Se for erro de autenticação, rode no terminal: "
                    "`uv run earthengine authenticate`"
                )
    if "_dem_path" in st.session_state:
        dem_path = Path(st.session_state["_dem_path"])
        st.caption(f"DEM em cache: {dem_path.name}")

elif fonte_dem.startswith("Upload"):
    up = st.file_uploader(
        "DEM local (.tif, qualquer CRS — será reprojetado para UTM)",
        type=["tif", "tiff"],
    )
    if up is not None:
        cache_dir = Path(st.session_state.get("_dem_cache_dir", "data/dems"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        dem_path = cache_dir / up.name
        dem_path.write_bytes(up.read())
        st.success(f"DEM salvo em {dem_path}")
else:
    api_key = os.environ.get("OPENTOPO_API_KEY", "")
    if not api_key:
        api_key = st.text_input(
            "OpenTopography API key",
            type="password",
            help="Crie gratuitamente em https://portal.opentopography.org/",
        )
    col1, col2, col3 = st.columns(3)
    buffer = col1.number_input("Buffer (graus)", 0.02, 1.0, 0.1, 0.01, key="opt_buffer")
    dem_type = col2.selectbox("Tipo", ["COP30", "COP90", "SRTMGL1", "SRTMGL3"])
    download_btn = col3.button("Baixar DEM")
    if download_btn:
        if not api_key:
            st.error("Informe a API key.")
        else:
            with st.spinner(f"Baixando DEM {dem_type} para buffer={buffer}°..."):
                try:
                    dem_path = basin.download_dem_opentopography(
                        lat=default_lat, lon=default_lon,
                        buffer_deg=buffer, dem_type=dem_type,
                        api_key=api_key,
                    )
                    st.session_state["_dem_path"] = str(dem_path)
                    st.success(f"DEM salvo em {dem_path}")
                except Exception as exc:
                    st.error(f"Falhou: {exc}")
    if "_dem_path" in st.session_state:
        dem_path = Path(st.session_state["_dem_path"])
        st.caption(f"DEM em cache: {dem_path.name}")


# ---------------------------------------------------------------------------
# Mapa para clicar exutorio
# ---------------------------------------------------------------------------

st.subheader("Mapa")
col1, col2 = st.columns([3, 1])
with col2:
    lat = st.number_input("Lat", -90.0, 90.0, default_lat, 0.001, format="%.6f")
    lon = st.number_input("Lon", -180.0, 180.0, default_lon, 0.001, format="%.6f")
    snap_dist = st.number_input("Snap (m)", 50, 2000, 500, 50)
    stream_thresh = st.number_input(
        "Threshold canal (células)", 10, 10000, 100, 10,
        help="Número mínimo de células para considerar canal. Maior = rede mais esparsa.",
    )

with col1:
    m = folium.Map(location=[lat, lon], zoom_start=13, tiles="OpenStreetMap")
    folium.TileLayer("OpenTopoMap", name="OpenTopoMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satélite", overlay=False,
    ).add_to(m)
    folium.Marker(
        [lat, lon], tooltip="Exutório (proposto)",
        icon=folium.Icon(color="blue", icon="tint", prefix="fa"),
    ).add_to(m)

    # Se ja houver bacia delineada, mostra
    bres = st.session_state.get("basin_result")
    if bres is not None:
        folium.GeoJson(
            bres["basin_geojson"],
            name="Bacia",
            style_function=lambda _: {"color": "#ff7f0e", "weight": 2, "fillOpacity": 0.25},
        ).add_to(m)
        folium.GeoJson(
            bres["stream_geojson"],
            name="Rede de drenagem",
            style_function=lambda _: {"color": "#1f77b4", "weight": 1.5},
        ).add_to(m)
        snapped = bres["outlet_snapped"]
        folium.Marker(
            [snapped[1], snapped[0]],
            tooltip="Exutório (snapped)",
            icon=folium.Icon(color="red", icon="water", prefix="fa"),
        ).add_to(m)
        m.fit_bounds(bres["bounds"])

    folium.LayerControl(collapsed=True).add_to(m)

    mapa_click = st_folium(
        m, height=450, width=None, returned_objects=["last_clicked"],
        key="mapa_bacia",
    )
    if mapa_click and mapa_click.get("last_clicked"):
        st.session_state.exutorio_lat = mapa_click["last_clicked"]["lat"]
        st.session_state.exutorio_lon = mapa_click["last_clicked"]["lng"]
        st.rerun()


# ---------------------------------------------------------------------------
# Delineamento
# ---------------------------------------------------------------------------

col1, col2 = st.columns([1, 4])
with col1:
    delinear = st.button("Delinear bacia", type="primary", disabled=dem_path is None)
with col2:
    if dem_path is None:
        st.warning("Carregue um DEM local ou baixe via OpenTopography antes.")

if delinear and dem_path is not None:
    with st.status("Executando pipeline WhiteboxTools...", expanded=True) as status:
        try:
            st.write("Reprojetando DEM para UTM...")
            result = basin.delineate_basin(
                lat=lat, lon=lon, dem_path=dem_path,
                snap_dist_m=snap_dist, stream_threshold=int(stream_thresh),
            )
            st.write("Delineamento concluído.")
            status.update(label="Bacia delineada ✓", state="complete")
        except Exception as exc:
            status.update(label=f"Falhou: {exc}", state="error")
            st.stop()

    # Salvar no session_state (convertendo para formato serializavel)
    st.session_state["basin_result"] = {
        "basin_geojson": result.basin_gdf.to_json(),
        "stream_geojson": result.stream_gdf.to_json(),
        "outlet_snapped": (result.outlet_snapped.x, result.outlet_snapped.y),
        "outlet_original": (result.outlet_original.x, result.outlet_original.y),
        "bounds": [
            [result.basin_gdf.total_bounds[1], result.basin_gdf.total_bounds[0]],
            [result.basin_gdf.total_bounds[3], result.basin_gdf.total_bounds[2]],
        ],
        "metrics": result.metrics.summary_dict(),
    }
    st.session_state["basin_metrics"] = result.metrics
    st.rerun()


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

bres = st.session_state.get("basin_result")
metrics = st.session_state.get("basin_metrics")
if bres is not None and metrics is not None:
    st.divider()
    st.subheader("Métricas da bacia")
    cols = st.columns(4)
    d = bres["metrics"]
    cols[0].metric("Área", f"{d['A (km2)']:g} km²")
    cols[1].metric("Perímetro", f"{d['P (km)']:g} km")
    cols[2].metric("L canal", f"{d['L canal (km)']:g} km")
    cols[3].metric(
        "S canal", f"{d['S media (%)']:g} %",
        help="Declividade do canal principal = ΔH / L_canal. Não é declividade média dos pixels.",
    )

    cols = st.columns(4)
    cols[0].metric("Z máx", f"{d['Z max (m)']:g} m")
    cols[1].metric("Z mín", f"{d['Z min (m)']:g} m")
    cols[2].metric("ΔH", f"{d['dH (m)']:g} m")
    cols[3].metric(
        "Método recomendado",
        "Racional" if metrics.area_km2 <= 2 else ("SCS-HU" if metrics.area_km2 <= 250 else "Distribuído"),
    )

    if st.button("Aplicar na Página 3 (Chuva-Vazão)", type="primary"):
        st.session_state.area_km2 = float(metrics.area_km2)
        # L_km e H_m para calculo de tc
        st.session_state["bacia_L_km"] = float(metrics.flowlength_km)
        st.session_state["bacia_H_m"] = float(metrics.delta_h_m)
        st.success(
            f"Bacia aplicada: A = {metrics.area_km2:.3f} km², "
            f"L = {metrics.flowlength_km:.3f} km, ΔH = {metrics.delta_h_m:.1f} m. "
            "Vá para a Página 3 para calcular tc e Q."
        )
