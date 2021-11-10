from typing import Union, Any, List, Dict
import pandas as pd
import geopandas as gpd
import itertools
from src.settings import *
import json5 as json
import numpy as np
from src.tools.logger import logging, get_logger


logger = get_logger(__name__)
sparse_dtype = pd.SparseDtype(int, fill_value=0)

with open(RAW_DATA_DIR / "implicit_maxspeeds.jsonc", "r") as f:
    IMPLICIT_MAXSPEEDS = json.load(f)


def apply_feature_selection(edges: Union[pd.DataFrame, gpd.GeoDataFrame], features_config: dict) -> pd.DataFrame:
    features_merge: List[dict] = features_config["settings"]["merge"]
    features_selected: Dict[str, List[str]] = features_config["features"]

    edges_after_merge = apply_features_mapping(edges, features_merge)
    features = [f"{k}_{v}" for k, vs in features_selected.items() for v in vs]
    return edges_after_merge[features]


def apply_features_mapping(edges: Union[pd.DataFrame, gpd.GeoDataFrame], features_merge: List[dict]) -> pd.DataFrame:
    for feature_merge in features_merge:
        feature_name = feature_merge["feature"]
        feature_mapping = feature_merge["mapping"]

        for feature_source, feature_target in feature_mapping.items():
            feature_source = f"{feature_name}_{feature_source}"
            feature_target = f"{feature_name}_{feature_target}"
            
            edges[feature_target] = ((edges[feature_source] == 1) | (edges[feature_target] == 1)).astype(sparse_dtype)
            edges = edges.drop(columns=feature_source)

    return edges


def generate_features_for_edges(
    edges: Union[pd.DataFrame, gpd.GeoDataFrame], featureset: Dict[str, List[str]]
) -> gpd.GeoDataFrame:
    for feature in featureset.keys():
        if feature in edges:
            edges = edges.join(explode_and_pivot(edges, feature))

    feature_column_names = list(
        itertools.chain(*[[f"{k}_{v}" for v in vs] for k, vs in featureset.items()])
    )
    columns_to_keep = ["u", "v", "key", "id", "h3_id", "geometry"] + feature_column_names
    columns_to_drop = list(set(edges.columns) - set(columns_to_keep))
    columns_to_add = list(
        set(columns_to_keep) - (set(columns_to_keep).intersection(set(edges.columns)))
    )

    edges.drop(columns=columns_to_drop, inplace=True)

    for c in columns_to_add:
        edges[c] = 0

    edges = edges.reindex(columns_to_keep, axis=1)

    return gpd.GeoDataFrame(edges)  # type: ignore


def explode_and_pivot(
    df: Union[pd.DataFrame, gpd.GeoDataFrame], column_name: str
) -> pd.DataFrame:
    prefix = f"{column_name}_"
    new_column_name = f"{prefix}new"
    df[new_column_name] = df[column_name].apply(
        lambda x: preprocess_and_convert_to_list(str(x), column_name)
    )
    df_expl = df.explode(new_column_name)  # type: ignore
    # df_expl[new_column_name] = df_expl[new_column_name].astype(str)  # type: ignore
    df_piv = df_expl.pivot(columns=new_column_name, values=new_column_name).add_prefix(
        prefix
    )
    df_piv[df_piv.notnull()] = 1
    df_piv = df_piv.fillna(0).astype('int32')
    

    return df_piv


def melt_and_max(
    edges: gpd.GeoDataFrame, column_name: str, columns: List[str]
) -> pd.Series:
    gdf = edges.groupby("id").first().drop(columns="h3_id").reset_index()
    gdf = gdf[["id"] + columns].melt(id_vars="id", value_vars=columns)
    gdf["variable"] = gdf["variable"].apply(lambda x: float(x.split("_")[1]))
    gdf["mul"] = gdf["variable"] * gdf["value"]
    gdf = gdf.groupby("id").max()[["mul"]].rename(columns={"mul": column_name})
    return gdf


def preprocess_and_convert_to_list(x: str, column_name: str) -> List[str]:
    x_list = eval(x) if "[" in x else [x]
    x_list = [sanitize_and_normalize(x, column_name) for x in x_list]

    return list(set(x_list))
    

def sanitize_and_normalize(x: str, column_name: str) -> str:
    return normalize(sanitize(x, column_name), column_name)


def normalize(x: str, column_name: str) -> str:
    try:
        if x == "None":
            return x
        elif column_name == "lanes":
            x = min(int(x) , 15)
        elif column_name == "maxspeed":
            x = float(x)
            if x <= 5:
                x = 5
            elif x <= 7:
                x = 7
            elif x <= 10:
                x = 10
            elif x <= 15:
                x = 15
            else:
                x = min(int(round(x / 10) * 10), 200)
        elif column_name == "width":
            x = min(round(float(x) * 2) / 2, 30.0)
    except Exception as e:
        logger.warn(f"{column_name}: {x} - {type(x)} | {e}")
        return "None"

    return str(x)


def sanitize(x: str, column_name: str) -> str:
    if x == "" or x == "none":
        return "None"

    try:
        if column_name == "lanes":
            x = int(float(x))
        elif column_name == "maxspeed":
            if x in ("signals", "variable"):
                return "None"

            x = IMPLICIT_MAXSPEEDS[x] if x in IMPLICIT_MAXSPEEDS else x
            x = x.replace("km/h", "")
            if "mph" in x:
                x = float(x.split(" mph")[0])
                x = x * 1.6
            x = float(x)
        elif column_name == "width":
            if x.endswith(" m") or x.endswith("m") or x.endswith("meter"):
                x = x.split("m")[0].strip()
            elif "'" in x:
                x = float(x.split("'")[0])
                x = x * 0.0254
            elif x.endswith("ft"):
                x = float(x.split(" ft")[0])
                x = x * 0.3048
            x = float(x)


    except Exception as e:
        logger.warn(f"{column_name}: {x} - {type(x)} | {e}")
        return "None"

    return str(x)
