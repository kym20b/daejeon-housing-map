import os
from html import escape
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium


load_dotenv()

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "").strip()

DEFAULT_HOSPITAL_CSV = r"C:\Users\kym\Desktop\파이썬 연습\병원정보서비스(2026.3.).csv"
DEFAULT_DAYCARE_CSV = r"C:\Users\kym\Desktop\파이썬 연습\어린이집기본정보조회(정기)-기준일(20260430).csv"

DAEJEON_LAT_MIN = 36.10
DAEJEON_LAT_MAX = 36.60
DAEJEON_LON_MIN = 127.10
DAEJEON_LON_MAX = 127.70
EARTH_RADIUS_M = 6_371_000


def read_csv_safely(file_path: Path) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    for enc in encodings:
        try:
            return pd.read_csv(file_path, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSV 인코딩을 확인하지 못했습니다: {file_path}")


def safe_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value)
    text = text.replace("`", "'").replace("${", "")
    text = text.replace("\r", " ").replace("\n", " ")
    return escape(text, quote=True)


def html_popup(title: str, rows: dict) -> folium.Popup:
    html = f"<b>{safe_text(title)}</b><br>"
    for key, value in rows.items():
        value_text = safe_text(value)
        if value_text:
            html += f"<b>{safe_text(key)}</b>: {value_text}<br>"
    return folium.Popup(html, max_width=420)


def filter_daejeon_bounds(df: pd.DataFrame, lat_col: str = "위도", lon_col: str = "경도") -> tuple[pd.DataFrame, pd.DataFrame]:
    valid = (
        df[lat_col].between(DAEJEON_LAT_MIN, DAEJEON_LAT_MAX)
        & df[lon_col].between(DAEJEON_LON_MIN, DAEJEON_LON_MAX)
    )
    return df[valid].copy(), df[~valid].copy()


def haversine_distance_m(lat1: float, lon1: float, lats2: pd.Series, lons2: pd.Series) -> pd.Series:
    lat1_rad = np.radians(lat1)
    lon1_rad = np.radians(lon1)
    lat2_rad = np.radians(lats2.astype(float))
    lon2_rad = np.radians(lons2.astype(float))

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_rad) * np.cos(lat2_rad) * (np.sin(dlon / 2) ** 2)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def distance_filter(df: pd.DataFrame, center_lat: float, center_lon: float, radius_m: int) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["거리(m)"] = haversine_distance_m(center_lat, center_lon, out["위도"], out["경도"])
    out = out[out["거리(m)"] <= radius_m].copy()
    out["거리(m)"] = out["거리(m)"].round(1)
    return out.sort_values("거리(m)")


@st.cache_data(show_spinner=False)
def load_and_preprocess(hospital_csv: str, daycare_csv: str) -> dict:
    df_hospital = read_csv_safely(Path(hospital_csv))
    df_daycare = read_csv_safely(Path(daycare_csv))

    hospital = df_hospital.copy()
    hospital["경도"] = pd.to_numeric(hospital["좌표(X)"], errors="coerce")
    hospital["위도"] = pd.to_numeric(hospital["좌표(Y)"], errors="coerce")

    daejeon_hospital = hospital[hospital["시도코드명"].astype(str).str.strip().eq("대전")].copy()
    jong_code = pd.to_numeric(daejeon_hospital["종별코드"], errors="coerce")
    name_contains_pediatric = daejeon_hospital["요양기관명"].astype(str).str.contains("소아", na=False)
    daejeon_hospital["소아과여부"] = jong_code.eq(31) & name_contains_pediatric
    daejeon_hospital = daejeon_hospital.dropna(subset=["위도", "경도"]).copy()
    daejeon_hospital, excluded_hospital = filter_daejeon_bounds(daejeon_hospital)

    regular_hospital = daejeon_hospital[~daejeon_hospital["소아과여부"]].copy()
    pediatric_hospital = daejeon_hospital[daejeon_hospital["소아과여부"]].copy()

    daycare = df_daycare.copy()
    daycare["위도"] = pd.to_numeric(daycare["위도"], errors="coerce")
    daycare["경도"] = pd.to_numeric(daycare["경도"], errors="coerce")

    daejeon_daycare = daycare[
        daycare["시도"].astype(str).str.strip().eq("대전광역시")
        & daycare["운영현황"].astype(str).str.strip().eq("정상")
    ].dropna(subset=["위도", "경도"]).copy()
    daejeon_daycare, excluded_daycare = filter_daejeon_bounds(daejeon_daycare)

    return {
        "regular_hospital": regular_hospital,
        "pediatric_hospital": pediatric_hospital,
        "daycare": daejeon_daycare,
        "excluded_hospital": excluded_hospital,
        "excluded_daycare": excluded_daycare,
    }


def search_apartment_kakao(query: str, api_key: str) -> list[dict]:
    if not api_key:
        raise ValueError(".env에 KAKAO_REST_API_KEY를 설정해 주세요.")
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"query": f"{query} 대전", "size": 15}
    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get("documents", [])


def build_map(
    center_lat: float,
    center_lon: float,
    home_name: str,
    radius_m: int,
    near_regular_hospital: pd.DataFrame,
    near_pediatric_hospital: pd.DataFrame,
    near_daycare: pd.DataFrame,
) -> folium.Map:
    m = folium.Map(location=[center_lat, center_lon], zoom_start=14, tiles="OpenStreetMap")

    hospital_group = folium.FeatureGroup(name=f"병원 ({len(near_regular_hospital):,})", show=True).add_to(m)
    pediatric_group = folium.FeatureGroup(name=f"소아과 ★ ({len(near_pediatric_hospital):,})", show=True).add_to(m)
    daycare_group = folium.FeatureGroup(name=f"어린이집 ({len(near_daycare):,})", show=True).add_to(m)
    home_group = folium.FeatureGroup(name="관심 주거지", show=True).add_to(m)

    for _, row in near_regular_hospital.iterrows():
        folium.CircleMarker(
            location=[row["위도"], row["경도"]],
            radius=5,
            color="blue",
            fill=True,
            fill_color="blue",
            fill_opacity=0.7,
            weight=1,
            tooltip=f"병원: {safe_text(row.get('요양기관명', ''))} ({row.get('거리(m)', '')}m)",
            popup=html_popup(
                row.get("요양기관명", ""),
                {
                    "거리(m)": row.get("거리(m)", ""),
                    "구분": row.get("종별코드명", ""),
                    "주소": row.get("주소", ""),
                    "전화번호": row.get("전화번호", ""),
                },
            ),
        ).add_to(hospital_group)

    for _, row in near_pediatric_hospital.iterrows():
        folium.Marker(
            location=[row["위도"], row["경도"]],
            tooltip=f"★ 소아과: {safe_text(row.get('요양기관명', ''))} ({row.get('거리(m)', '')}m)",
            popup=html_popup(
                f"★ {row.get('요양기관명', '')}",
                {
                    "거리(m)": row.get("거리(m)", ""),
                    "구분": row.get("종별코드명", ""),
                    "주소": row.get("주소", ""),
                    "전화번호": row.get("전화번호", ""),
                },
            ),
            icon=folium.DivIcon(
                icon_size=(32, 32),
                icon_anchor=(16, 16),
                html="""
                    <div style="
                        font-size: 28px;
                        color: red;
                        text-shadow: 1px 1px 3px white, -1px -1px 3px white;
                        line-height: 28px;
                        text-align: center;
                    ">★</div>
                """,
            ),
        ).add_to(pediatric_group)

    for _, row in near_daycare.iterrows():
        folium.CircleMarker(
            location=[row["위도"], row["경도"]],
            radius=5,
            color="green",
            fill=True,
            fill_color="green",
            fill_opacity=0.7,
            weight=1,
            tooltip=f"어린이집: {safe_text(row.get('어린이집명', ''))} ({row.get('거리(m)', '')}m)",
            popup=html_popup(
                row.get("어린이집명", ""),
                {
                    "거리(m)": row.get("거리(m)", ""),
                    "유형": row.get("어린이집유형구분", ""),
                    "주소": row.get("주소", ""),
                    "전화번호": row.get("어린이집전화번호", ""),
                },
            ),
        ).add_to(daycare_group)

    folium.Marker(
        location=[center_lat, center_lon],
        tooltip=safe_text(home_name),
        popup=html_popup(home_name, {"반경": f"{radius_m}m"}),
        icon=folium.DivIcon(
            icon_size=(34, 34),
            icon_anchor=(17, 17),
            html="""
                <div style="
                    font-size: 22px;
                    color: black;
                    background: yellow;
                    border: 2px solid black;
                    border-radius: 50%;
                    width: 34px;
                    height: 34px;
                    line-height: 30px;
                    text-align: center;
                    font-weight: bold;
                ">H</div>
            """,
        ),
    ).add_to(home_group)

    folium.Circle(
        location=[center_lat, center_lon],
        radius=radius_m,
        color="red",
        fill=True,
        fill_opacity=0.08,
        popup=f"{safe_text(home_name)} 반경 {radius_m}m",
    ).add_to(home_group)

    folium.LayerControl(collapsed=False).add_to(m)
    return m


def main() -> None:
    st.set_page_config(page_title="대전 주거지 생활 인프라 맵", layout="wide")
    st.title("대전 주거지 기준 어린이집/병원 분포")
    st.caption("아파트명을 검색하면 반경 내 어린이집, 병원, 소아과를 지도와 표로 확인할 수 있습니다.")

    with st.sidebar:
        st.subheader("데이터/검색 설정")
        hospital_csv = st.text_input("병원 CSV 경로", value=DEFAULT_HOSPITAL_CSV)
        daycare_csv = st.text_input("어린이집 CSV 경로", value=DEFAULT_DAYCARE_CSV)
        apt_name = st.text_input("관심 아파트명", value="")
        radius_m = st.slider("반경 (m)", min_value=300, max_value=3000, value=800, step=100)
        search_clicked = st.button("아파트 검색", type="primary")
        st.markdown("---")
        st.write("카카오 키 상태:", "설정됨" if KAKAO_REST_API_KEY else "미설정")

    if not hospital_csv or not daycare_csv:
        st.warning("CSV 경로를 입력해 주세요.")
        return

    try:
        data = load_and_preprocess(hospital_csv, daycare_csv)
    except Exception as exc:
        st.error(f"데이터 로딩 실패: {exc}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("대전 병원(소아과 제외)", f"{len(data['regular_hospital']):,}")
    c2.metric("대전 소아과", f"{len(data['pediatric_hospital']):,}")
    c3.metric("대전 정상 어린이집", f"{len(data['daycare']):,}")

    if search_clicked:
        if not apt_name.strip():
            st.warning("아파트명을 입력해 주세요.")
            return
        try:
            candidates = search_apartment_kakao(apt_name.strip(), KAKAO_REST_API_KEY)
        except Exception as exc:
            st.error(f"카카오 API 검색 실패: {exc}")
            return

        if not candidates:
            st.info("검색 결과가 없습니다. 아파트명 또는 동/구를 함께 입력해 보세요.")
            return

        options = []
        for item in candidates:
            options.append(
                {
                    "name": item.get("place_name", ""),
                    "address": item.get("road_address_name") or item.get("address_name", ""),
                    "lat": float(item["y"]),
                    "lon": float(item["x"]),
                }
            )
        st.session_state["apt_options"] = options
        st.session_state["selected_idx"] = 0

    options = st.session_state.get("apt_options", [])
    selected_idx = st.session_state.get("selected_idx", 0)

    if options:
        labels = [f"{idx + 1}. {opt['name']} / {opt['address']}" for idx, opt in enumerate(options)]
        picked_label = st.selectbox("검색된 아파트 후보", options=labels, index=min(selected_idx, len(labels) - 1))
        picked_idx = labels.index(picked_label)
        chosen = options[picked_idx]

        center_lat = chosen["lat"]
        center_lon = chosen["lon"]
        home_name = chosen["name"]

        near_regular_hospital = distance_filter(data["regular_hospital"], center_lat, center_lon, radius_m)
        near_pediatric_hospital = distance_filter(data["pediatric_hospital"], center_lat, center_lon, radius_m)
        near_daycare = distance_filter(data["daycare"], center_lat, center_lon, radius_m)

        st.markdown(
            f"### `{home_name}` 기준 반경 `{radius_m}m` 결과 "
            f"(병원 {len(near_regular_hospital):,}, 소아과 {len(near_pediatric_hospital):,}, 어린이집 {len(near_daycare):,})"
        )

        m = build_map(
            center_lat=center_lat,
            center_lon=center_lon,
            home_name=home_name,
            radius_m=radius_m,
            near_regular_hospital=near_regular_hospital,
            near_pediatric_hospital=near_pediatric_hospital,
            near_daycare=near_daycare,
        )
        st_folium(m, width=None, height=700)

        tab1, tab2, tab3 = st.tabs(["어린이집", "병원", "소아과"])
        with tab1:
            show = near_daycare[
                ["어린이집명", "거리(m)", "어린이집유형구분", "시군구", "주소", "어린이집전화번호"]
            ].rename(columns={"어린이집전화번호": "전화번호"})
            st.dataframe(show, use_container_width=True, hide_index=True)
        with tab2:
            show = near_regular_hospital[["요양기관명", "거리(m)", "종별코드명", "주소", "전화번호"]].rename(
                columns={"요양기관명": "병원명", "종별코드명": "구분"}
            )
            st.dataframe(show, use_container_width=True, hide_index=True)
        with tab3:
            show = near_pediatric_hospital[["요양기관명", "거리(m)", "종별코드명", "주소", "전화번호"]].rename(
                columns={"요양기관명": "소아과명", "종별코드명": "구분"}
            )
            st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.info("좌측에서 아파트명을 입력하고 `아파트 검색`을 눌러 주세요.")


if __name__ == "__main__":
    main()
